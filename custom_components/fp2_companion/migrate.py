"""The move from MQTT discovery entities to native ones (decision 27).

Up to version 1.4.x every entity was an MQTT discovery entity. Their entity
ids are in people's dashboards and automations, so the first setup of a
version with native entities moves each one over with the same entity id and
the settings a person gave it (name, icon, area, labels, aliases, hidden,
disabled, display options). History and statistics follow the entity id, so
they continue. The MQTT devices those entities sat on are removed, and what
the old versions left retained on the broker is cleared, so the MQTT
entities do not come back.

Device ids change, so a device trigger, condition or action that names one
of the old "<FP2> Settings" devices has to be picked again.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, LEGACY_BASE_TOPIC, STATE_DIR, UNIQUE_ID_PREFIX

_LOGGER = logging.getLogger(__name__)

MQTT_DOMAIN = "mqtt"
# How long the move waits for Home Assistant to drop an MQTT entity's state
# after its registry entry is removed. It takes one loop turn.
STATE_GONE_TIMEOUT = 10
# How long the broker cleanup listens for the retained topics of the old
# versions. A broker sends the retained messages at once on subscribe.
RETAINED_WAIT = 2
# Kept in the state folder until the broker cleanup has gone through, with
# the discovery topics to clear, so a broker that was down is cleared on a
# later start. Without it the old MQTT entities would come back.
CLEANUP_PENDING = "mqtt_cleanup_pending.json"


@dataclass(frozen=True)
class _Kept:
    """What a person may have set on one MQTT entity."""

    entity_id: str
    domain: str
    unique_id: str
    settings: dict[str, Any]
    options: dict[str, Any]
    disabled_by: er.RegistryEntryDisabler | None
    hidden_by: er.RegistryEntryHider | None


async def async_migrate_mqtt_entities(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Move every MQTT entity of the old versions to this entry. Returns how
    many were moved; 0 on every setup after the first."""
    registry = er.async_get(hass)
    old = [
        e
        for e in list(registry.entities.values())
        if e.platform == MQTT_DOMAIN and e.unique_id.startswith(UNIQUE_ID_PREFIX)
    ]
    pending = hass.config.path(STATE_DIR, CLEANUP_PENDING)
    if old:
        kept = [_keep(e) for e in old]
        for e in old:
            registry.async_remove(e.entity_id)
        await _async_states_gone(hass, [k.entity_id for k in kept])
        for k in kept:
            _recreate(registry, entry, k)
        _remove_mqtt_devices(hass)
        _LOGGER.info("Moved %s MQTT entities of FP2 Companion to native ones", len(kept))
        discovery = [f"{k.domain}/{k.unique_id}/config" for k in kept]
        await hass.async_add_executor_job(_add_pending, pending, discovery)
    discovery = await hass.async_add_executor_job(_read_pending, pending)
    if discovery is not None:
        entry.async_create_background_task(
            hass,
            _async_clear_broker(hass, discovery, pending),
            "fp2_companion clear broker",
        )
    return len(old)


def _add_pending(path: str, discovery: list[str]) -> None:
    known = _read_pending(path) or []
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(sorted(set(known) | set(discovery)), fh)


def _read_pending(path: str) -> list[str] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return [str(d) for d in json.load(fh)]
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return []


def _keep(e: er.RegistryEntry) -> _Kept:
    # Only what a person chose carries over: an entity disabled because the
    # MQTT entry was disabled must not stay disabled under this one.
    disabled_by = e.disabled_by if e.disabled_by is er.RegistryEntryDisabler.USER else None
    hidden_by = e.hidden_by if e.hidden_by is er.RegistryEntryHider.USER else None
    return _Kept(
        entity_id=e.entity_id,
        domain=e.domain,
        unique_id=e.unique_id,
        settings={
            "name": e.name,
            "icon": e.icon,
            "area_id": e.area_id,
            "aliases": set(e.aliases),
            "labels": set(e.labels),
            "categories": dict(e.categories),
        },
        options={k: dict(v) for k, v in e.options.items()},
        disabled_by=disabled_by,
        hidden_by=hidden_by,
    )


async def _async_states_gone(hass: HomeAssistant, entity_ids: list[str]) -> None:
    """Wait until Home Assistant has dropped the MQTT entities' states, so
    their entity ids are free again."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STATE_GONE_TIMEOUT
    while any(hass.states.get(i) is not None for i in entity_ids):
        if loop.time() > deadline:
            _LOGGER.warning("Old FP2 Companion entities are still loaded")
            return
        await asyncio.sleep(0.05)


@callback
def _recreate(registry: er.EntityRegistry, entry: ConfigEntry, k: _Kept) -> None:
    if registry.async_get_entity_id(k.domain, DOMAIN, k.unique_id) is not None:
        # The native entity exists already: this MQTT one is a duplicate an
        # uncleared broker brought back, and removing it was all there was
        # to do. The native entity keeps its id and its settings.
        _LOGGER.info("Removed %s, a duplicate of a native entity", k.entity_id)
        return
    object_id = k.entity_id.split(".", 1)[1]
    new = registry.async_get_or_create(
        k.domain,
        DOMAIN,
        k.unique_id,
        suggested_object_id=object_id,
        config_entry=entry,
        disabled_by=k.disabled_by,
        hidden_by=k.hidden_by,
    )
    changes: dict[str, Any] = dict(k.settings)
    if new.entity_id != k.entity_id:
        if registry.async_is_registered(k.entity_id) or (
            registry.hass.states.get(k.entity_id) is not None
        ):
            _LOGGER.warning(
                "%s is taken, so its entity is now %s", k.entity_id, new.entity_id
            )
        else:
            changes["new_entity_id"] = k.entity_id
    entity_id = changes.get("new_entity_id", new.entity_id)
    registry.async_update_entity(new.entity_id, **changes)
    for domain, options in k.options.items():
        registry.async_update_entity_options(entity_id, domain, options)


@callback
def _remove_mqtt_devices(hass: HomeAssistant) -> None:
    """The MQTT devices the old entities sat on: "<FP2> Settings" and
    "FP2 Companion"."""
    devices = dr.async_get(hass)
    for device in list(devices.devices.values()):
        if any(
            domain == MQTT_DOMAIN and str(ident).startswith("fp2_companion")
            for domain, ident in device.identifiers
        ):
            devices.async_remove_device(device.id)


async def _async_clear_broker(
    hass: HomeAssistant, discovery: list[str], pending: str
) -> None:
    """Clear what the old versions left retained on the broker: the discovery
    configs (`discovery`, under the MQTT discovery prefix) and everything
    under their topic prefix. The pending file goes once that went through;
    while MQTT is not set up or the broker cannot be reached it stays, and
    the next start tries again."""
    if not hass.config_entries.async_entries(MQTT_DOMAIN):
        # No MQTT at all, so no broker holds anything of the old versions.
        await hass.async_add_executor_job(_remove_pending, pending)
        return
    if MQTT_DOMAIN not in hass.config.components:
        _LOGGER.info(
            "MQTT is not loaded, so the old FP2 Companion topics stay on the "
            "broker until a later start"
        )
        return
    from homeassistant.components import mqtt  # noqa: PLC0415

    prefix = "homeassistant"
    for mqtt_entry in hass.config_entries.async_entries(MQTT_DOMAIN):
        conf = {**mqtt_entry.data, **mqtt_entry.options}
        prefix = str(conf.get(mqtt.CONF_DISCOVERY_PREFIX, prefix))
    topics: set[str] = {f"{prefix}/{d}" for d in discovery}

    @callback
    def received(msg: mqtt.ReceiveMessage) -> None:
        if msg.retain and msg.payload:
            topics.add(msg.topic)

    try:
        if not await mqtt.async_wait_for_mqtt_client(hass):
            raise ConnectionError("the MQTT client did not connect")
        unsubscribe = await mqtt.async_subscribe(
            hass, f"{LEGACY_BASE_TOPIC}/#", received
        )
        await asyncio.sleep(RETAINED_WAIT)
        unsubscribe()
        for topic in sorted(topics):
            await mqtt.async_publish(hass, topic, "", 0, True)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning(
            "Could not clear the old FP2 Companion topics from the broker, "
            "trying again at the next start: %s",
            exc,
        )
        return
    await hass.async_add_executor_job(_remove_pending, pending)
    _LOGGER.info("Cleared %s retained FP2 Companion topics", len(topics))


def _remove_pending(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
