"""The zones and backups the card draws, a backup's preview, the cloud
status of each FP2, and the app's own list of devices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .companion import STATUS_TEXT
from .entity import Fp2Entity, Fp2HubEntity, async_setup_devices

if TYPE_CHECKING:
    from . import Fp2CompanionConfigEntry
    from .companion import Fp2Device
    from .runtime import HassFp2Companion

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Fp2CompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the app's sensor and the sensors of every FP2."""
    app = entry.runtime_data
    async_add_entities([DevicesSensor(app)])
    entry.async_on_unload(
        async_setup_devices(
            app,
            async_add_entities,
            lambda device: [
                ZonesSensor(app, device),
                BackupPreviewSensor(app, device),
                CloudStatusSensor(app, device),
            ],
        )
    )


class ZonesSensor(Fp2Entity, SensorEntity):
    """The number of zones; its attributes carry the zones, the zone types
    and the newest backups, which the card draws."""

    _attr_translation_key = "zones"
    # The card reads these from the state; the recorder need not keep them.
    _unrecorded_attributes = frozenset({"zones", "tags", "backups", "grid", "sensor"})

    def __init__(self, app: HassFp2Companion, device: Fp2Device) -> None:
        """Set up the sensor."""
        super().__init__(app, device, "sensor", "zones")

    @property
    def native_value(self) -> int | None:
        """How many zones the device has, after the first read."""
        if self._device.last_read is None:
            return None
        return len(self._device.zones)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What the card draws."""
        return self._device.zones_attrs


class BackupPreviewSensor(Fp2Entity, SensorEntity):
    """The backup the card asked to preview, with its zones. A sensor of its
    own, because the zones of every backup together would pass the 16 KB
    Home Assistant keeps per entity."""

    _attr_translation_key = "backup_preview"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"zones"})

    def __init__(self, app: HassFp2Companion, device: Fp2Device) -> None:
        """Set up the sensor."""
        super().__init__(app, device, "sensor", "backup_preview")

    @property
    def native_value(self) -> str | None:
        """The backup's name."""
        preview = self._device.preview
        return preview["name"] if preview else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The backup's zones and when it was saved."""
        return self._device.preview


class CloudStatusSensor(Fp2Entity, SensorEntity):
    """What the last cloud call or command came to, as a code; the card and
    automations decide on the code, never on the message beside it."""

    _attr_translation_key = "cloud_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(STATUS_TEXT)
    # The automations decide on the state; the session and the last error
    # need not stay in the history.
    _unrecorded_attributes = frozenset(
        {"did", "account", "country", "token_expires", "last_error", "detail"}
    )

    def __init__(self, app: HassFp2Companion, device: Fp2Device) -> None:
        """Set up the sensor."""
        super().__init__(app, device, "sensor", "cloud_status")

    @property
    def native_value(self) -> str:
        """The status code."""
        return self._device.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The message, the detail and the shared session."""
        return self._device.status_attrs


class DevicesSensor(Fp2HubEntity, SensorEntity):
    """The FP2s the app serves, for the card's dropdown, with the countries
    of its sign-in form and the session."""

    _attr_translation_key = "devices"
    _unrecorded_attributes = frozenset(
        {"devices", "countries", "account", "country", "token_expires"}
    )

    @property
    def native_value(self) -> int:
        """How many FP2s are served."""
        return len(self._app.hub_attrs["devices"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The devices, the countries and the session."""
        return self._app.hub_attrs
