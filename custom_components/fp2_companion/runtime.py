"""Home Assistant's side of fp2_companion (decision 26).

`Fp2Companion` is blocking code written for one worker thread, the way
AppDaemon ran it. `HassFp2Companion` gives it that thread, an executor of its
own with one worker, and provides what AppDaemon provided: the log, timers,
MQTT and the devices Home Assistant knows. The app calls these methods from
its worker thread and from the live tracker's threads, never from the event
loop, so each of them hands its work to the loop in a thread-safe way.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import itertools
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.event import (
    async_call_later,
    async_track_time_change,
)
from homeassistant.util.async_ import run_callback_threadsafe

from .companion import Fp2Companion

_LOGGER = logging.getLogger(__package__)

LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

HOMEKIT_DOMAIN = "homekit_controller"

# How long the worker waits for the event loop to read the registries or
# subscribe. The loop answers in milliseconds; this only bounds a stall.
LOOP_TIMEOUT = 30


class HassFp2Companion(Fp2Companion):
    """The app, running inside Home Assistant."""

    def __init__(self, hass: HomeAssistant, args: dict[str, Any]) -> None:
        """Set up the runtime; `async_start` starts the app."""
        self.hass = hass
        self.args = args
        # One worker, so the app's callbacks run one at a time, in order.
        self._worker = ThreadPoolExecutor(1, thread_name_prefix="fp2_companion")
        self._handles = itertools.count(1)
        self._timers: dict[int, CALLBACK_TYPE] = {}
        self._unsubscribe: list[CALLBACK_TYPE] = []
        self._stopping = False

    # ---------------------------------------------------------------- lifecycle
    async def async_start(self) -> None:
        """Run `initialize` on the worker. An error fails the setup."""
        await self.hass.loop.run_in_executor(self._worker, self.initialize)

    async def async_stop(self) -> None:
        """Stop every subscription and timer, then let the app close."""
        self._stopping = True
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()
        await self.hass.loop.run_in_executor(
            self._worker, self._guarded, self.terminate, ()
        )
        # Waits for the worker thread to end, on one of Home Assistant's.
        await self.hass.async_add_executor_job(self._worker.shutdown)

    def _guarded(self, func: Callable[..., Any], args: tuple[Any, ...]) -> None:
        """One callback of the app. An error is logged, as AppDaemon did,
        and the next callback runs."""
        try:
            func(*args)
        except Exception:
            _LOGGER.exception("Error in %s", getattr(func, "__qualname__", repr(func)))

    @callback
    def _submit(self, func: Callable[..., Any], *args: Any) -> None:
        """Queue a callback of the app for the worker, from the event loop."""
        if not self._stopping:
            self._worker.submit(self._guarded, func, args)

    # -------------------------------------------------------------------- log
    def log(self, text: str, level: str = "INFO") -> None:
        """Write a line to Home Assistant's log."""
        _LOGGER.log(LEVELS.get(str(level).upper(), logging.INFO), "%s", text)

    # ----------------------------------------------------------------- timers
    def run_in(
        self, func: Callable[[dict[str, Any]], Any], delay: float, **kwargs: Any
    ) -> int:
        """Call `func(kwargs)` on the worker in `delay` seconds."""
        handle = next(self._handles)
        self.hass.loop.call_soon_threadsafe(self._arm_in, handle, func, delay, kwargs)
        return handle

    @callback
    def _arm_in(
        self,
        handle: int,
        func: Callable[[dict[str, Any]], Any],
        delay: float,
        kwargs: dict[str, Any],
    ) -> None:
        if self._stopping:
            return

        @callback
        def fire(_now: datetime) -> None:
            self._timers.pop(handle, None)
            self._submit(func, dict(kwargs))

        self._timers[handle] = async_call_later(self.hass, delay, fire)

    def run_daily(self, func: Callable[[dict[str, Any]], Any], start: str) -> int:
        """Call `func({})` on the worker every day at `start`, "HH:MM[:SS]"."""
        hour, minute, second = ([int(p) for p in str(start).split(":")] + [0])[:3]
        handle = next(self._handles)
        self.hass.loop.call_soon_threadsafe(
            self._arm_daily, handle, func, hour, minute, second
        )
        return handle

    @callback
    def _arm_daily(
        self,
        handle: int,
        func: Callable[[dict[str, Any]], Any],
        hour: int,
        minute: int,
        second: int,
    ) -> None:
        if self._stopping:
            return

        @callback
        def fire(_now: datetime) -> None:
            self._submit(func, {})

        self._timers[handle] = async_track_time_change(
            self.hass, fire, hour=hour, minute=minute, second=second
        )

    def cancel_timer(self, handle: int, silent: bool = False) -> None:
        """Cancel a timer. One that has fired already is not an error when
        `silent` is set."""
        self.hass.loop.call_soon_threadsafe(self._disarm, handle, silent)

    @callback
    def _disarm(self, handle: int, silent: bool) -> None:
        cancel = self._timers.pop(handle, None)
        if cancel is not None:
            cancel()
        elif not silent:
            _LOGGER.warning("No timer %s to cancel", handle)

    # ------------------------------------------------------------------- MQTT
    def publish(
        self, topic: str, payload: str, retain: bool = False, qos: int = 0
    ) -> None:
        """Publish through Home Assistant's MQTT client. The messages go out
        in the order they are published in: every one is its own task, and
        each task hands its message to the client before it first waits."""
        self.hass.create_task(
            self._async_publish(topic, payload, qos, retain),
            f"{__package__} publish {topic}",
        )

    async def _async_publish(
        self, topic: str, payload: str, qos: int, retain: bool
    ) -> None:
        try:
            await mqtt.async_publish(self.hass, topic, payload, qos, retain)
        except HomeAssistantError as exc:
            _LOGGER.warning("Could not publish to %s: %s", topic, exc)

    def subscribe(self, topics: list[str], func: Callable[[str, str], Any]) -> None:
        """Call `func(topic, payload)` on the worker for every message on
        `topics`. Returns once Home Assistant has subscribed."""
        asyncio.run_coroutine_threadsafe(
            self._async_subscribe(topics, func), self.hass.loop
        ).result(LOOP_TIMEOUT)

    async def _async_subscribe(
        self, topics: list[str], func: Callable[[str, str], Any]
    ) -> None:
        @callback
        def received(msg: mqtt.ReceiveMessage) -> None:
            self._submit(func, msg.topic, msg.payload)

        for topic in topics:
            self._unsubscribe.append(
                await mqtt.async_subscribe(self.hass, topic, received)
            )

    # -------------------------------------------------------------- discovery
    def find_devices(self) -> list[dict[str, Any]]:
        """Every Aqara PS device of the HomeKit Device integration, with its
        serial, model, original name, current name and area."""
        return run_callback_threadsafe(self.hass.loop, self._find_devices).result(
            LOOP_TIMEOUT
        )

    @callback
    def _find_devices(self) -> list[dict[str, Any]]:
        devices = dr.async_get(self.hass)
        areas = ar.async_get(self.hass)
        rows: dict[str, dict[str, Any]] = {}
        for entry in self.hass.config_entries.async_entries(HOMEKIT_DOMAIN):
            for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
                model = device.model
                if not (isinstance(model, str) and model.startswith("PS-")):
                    continue
                area = areas.async_get_area(device.area_id) if device.area_id else None
                # `original` is the name the integration gave the device, which
                # its entity ids were built from; `name` is the one a person
                # may have given it since.
                rows[device.id] = {
                    "serial": device.serial_number,
                    "model": model,
                    "original": device.name,
                    "name": device.name_by_user or device.name,
                    "area": area.name if area else None,
                }
        return list(rows.values())
