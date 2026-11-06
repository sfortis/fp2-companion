"""Home Assistant's side of fp2_companion (decision 26).

`Fp2Companion` is blocking code written for one worker thread, the way
AppDaemon ran it. `HassFp2Companion` gives it that thread, an executor of its
own with one worker, and provides the log, timers, the devices Home Assistant
knows, and the signals that update the entities and the card's live view.
The app calls these methods from its worker thread and from the live
tracker's threads, never from the event loop, so each of them hands its work
to the loop in a thread-safe way. The entities and the card's commands go the
other way, through `submit`, which queues a call for the worker.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import itertools
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.dispatcher import dispatcher_send
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

# How long the worker waits for the event loop to read the registries. The
# loop answers in milliseconds; this only bounds a stall.
LOOP_TIMEOUT = 30

LiveListener = Callable[[dict[str, Any]], None]


class HassFp2Companion(Fp2Companion):
    """The app, running inside Home Assistant."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, args: dict[str, Any]
    ) -> None:
        """Set up the runtime; `async_start` starts the app."""
        self.hass = hass
        self.entry_id = entry_id
        self.args = args
        # The card's live views, per device key (websocket subscriptions).
        self._live_listeners: dict[str, set[LiveListener]] = {}
        # One worker, so the app's callbacks run one at a time, in order.
        self._worker = ThreadPoolExecutor(1, thread_name_prefix="fp2_companion")
        self._handles = itertools.count(1)
        self._timers: dict[int, CALLBACK_TYPE] = {}
        self._stopping = False
        self._stopped = False

    # ---------------------------------------------------------------- lifecycle
    async def async_start(self) -> None:
        """Run `initialize` on the worker. An error fails the setup."""
        await self.hass.loop.run_in_executor(self._worker, self.initialize)

    async def async_stop(self) -> None:
        """Stop every timer, then let the app close."""
        if self._stopped:
            return
        self._stopped = True
        self._stopping = True
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
    def submit(self, func: Callable[..., Any], *args: Any) -> None:
        """Queue a call of the app for the worker, from the event loop."""
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
            self.submit(func, dict(kwargs))

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
            self.submit(func, {})

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

    # ---------------------------------------------------------------- signals
    def added(self, device: Any) -> None:
        """A device is served for the first time: its entities are added."""
        dispatcher_send(self.hass, signal_added(self.entry_id), device)

    def changed(self, key: str | None) -> None:
        """A device's state changed, or the app's own when `key` is None."""
        dispatcher_send(self.hass, signal_changed(self.entry_id, key))

    def signed_out(self) -> None:
        """The Aqara session is gone: Home Assistant asks for a sign-in."""
        self.hass.loop.call_soon_threadsafe(self._start_reauth)

    @callback
    def _start_reauth(self) -> None:
        if self._stopping:
            return
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is not None:
            entry.async_start_reauth(self.hass)

    def live(self, key: str, message: dict[str, Any]) -> None:
        """One live position message, for the cards watching `key`."""
        self.hass.loop.call_soon_threadsafe(self._deliver_live, key, message)

    @callback
    def _deliver_live(self, key: str, message: dict[str, Any]) -> None:
        for listener in list(self._live_listeners.get(key, ())):
            listener(message)

    @callback
    def async_listen_live(self, key: str, listener: LiveListener) -> CALLBACK_TYPE:
        """Call `listener(message)` for every live message of `key`."""
        self._live_listeners.setdefault(key, set()).add(listener)

        @callback
        def remove() -> None:
            self._live_listeners.get(key, set()).discard(listener)

        return remove

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


def signal_added(entry_id: str) -> str:
    """The signal that carries a newly served device."""
    return f"fp2_companion_{entry_id}_added"


def signal_changed(entry_id: str, key: str | None) -> str:
    """The signal after a device's state, or the app's (`key` None), changed."""
    return f"fp2_companion_{entry_id}_changed_{key or 'app'}"
