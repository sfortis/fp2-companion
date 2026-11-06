"""The base entity of fp2_companion.

Every entity reads its state from the app's objects (`companion.py`) and is
written again when the runtime signals that its device changed. Nothing
polls: the app reads the cloud at startup, after a write and on Refresh.

The entity ids are the ones the MQTT discovery entities of versions up to
1.4.x had, `<domain>.<homekit prefix>_<key>`, so dashboards and automations
keep working; `migrate.py` moves the existing registry entries over.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, HUB_ENTITY_ID, HUB_UNIQUE_ID, UNIQUE_ID_PREFIX
from .runtime import signal_added, signal_changed

if TYPE_CHECKING:
    from .companion import Fp2Device
    from .runtime import HassFp2Companion


def device_info(device: Fp2Device) -> DeviceInfo:
    """The FP2's settings device, "<FP2> Settings". Since Home Assistant
    2026.10 a device belongs to one config entry, so the settings cannot join
    the HomeKit Device integration's device; they take its name and area."""
    return DeviceInfo(
        identifiers={(DOMAIN, device.info["did"])},
        name=f"{device.info['name']} Settings",
        manufacturer="Aqara",
        model="Presence Sensor FP2",
        serial_number=device.key.upper(),
        suggested_area=device.info.get("area"),
    )


def hub_device_info(entry_id: str) -> DeviceInfo:
    """The app itself, as a service."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name="Aqara FP2 Companion",
        manufacturer="sfortis",
        model="FP2 Companion",
        entry_type=DeviceEntryType.SERVICE,
    )


def unique_id(device: Fp2Device, key: str) -> str:
    """The unique id the MQTT entities had, so the migration finds them."""
    return f"{UNIQUE_ID_PREFIX}{device.info['did'].replace('.', '_')}_{key}"


class Fp2Entity(Entity):
    """An entity of one FP2."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        app: HassFp2Companion,
        device: Fp2Device,
        domain: str,
        key: str,
        entity_key: str | None = None,
    ) -> None:
        """`key` names the entity in its unique id, `entity_key` (default
        `key`) in its entity id."""
        self._app = app
        self._device = device
        self._attr_unique_id = unique_id(device, key)
        # Only a suggestion: an entity already in the registry keeps its id.
        self.entity_id = f"{domain}.{device.info['prefix']}_{entity_key or key}"
        self._attr_device_info = device_info(device)

    async def async_added_to_hass(self) -> None:
        """Follow the device's changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_changed(self._app.entry_id, self._device.key),
                self._handle_changed,
            )
        )

    @callback
    def _handle_changed(self) -> None:
        self.async_write_ha_state()

    def _run(self, func: Any, *args: Any) -> None:
        """Run one of the device's commands on the app's worker."""
        self._app.submit(func, *args)


class Fp2HubEntity(Entity):
    """The app's own entity, which the card reads by its fixed id."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_unique_id = HUB_UNIQUE_ID

    def __init__(self, app: HassFp2Companion) -> None:
        """Set up the hub entity."""
        self._app = app
        self.entity_id = HUB_ENTITY_ID
        self._attr_device_info = hub_device_info(app.entry_id)

    async def async_added_to_hass(self) -> None:
        """Follow the app's changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_changed(self._app.entry_id, None),
                self._handle_changed,
            )
        )

    @callback
    def _handle_changed(self) -> None:
        self.async_write_ha_state()


def async_setup_devices(
    app: HassFp2Companion,
    async_add_entities: Any,
    factory: Any,
) -> CALLBACK_TYPE:
    """Add `factory(device)`'s entities for every device the app serves now
    and for every device it serves later, without a reload."""
    done: set[str] = set()

    @callback
    def add(device: Fp2Device) -> None:
        if device.key in done:
            return
        done.add(device.key)
        async_add_entities(factory(device))

    remove = async_dispatcher_connect(app.hass, signal_added(app.entry_id), add)
    for device in list(app.devices.values()):
        add(device)
    return remove
