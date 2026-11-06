"""The FP2's sensitivity, its approach distance and each detection zone's
sensitivity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .companion import APPROACH_LABELS, SENSITIVITY_LABELS, slugify
from .const import DOMAIN
from .entity import Fp2Entity, async_setup_devices, unique_id
from .runtime import signal_changed

if TYPE_CHECKING:
    from . import Fp2CompanionConfigEntry
    from .companion import Fp2Device
    from .runtime import HassFp2Companion

# Nothing polls, and every command is queued on the app's one worker.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Fp2CompanionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the selects of every FP2, and keep one per detection zone."""
    app = entry.runtime_data

    def entities(device: Fp2Device) -> list[SelectEntity]:
        _ZoneSelects(app, device, async_add_entities).start(entry)
        return [SensitivitySelect(app, device), ApproachDistanceSelect(app, device)]

    entry.async_on_unload(async_setup_devices(app, async_add_entities, entities))


class SensitivitySelect(Fp2Entity, SelectEntity):
    """The FP2's sensitivity."""

    _attr_translation_key = "sensitivity"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(SENSITIVITY_LABELS.values())

    def __init__(self, app: HassFp2Companion, device: Fp2Device) -> None:
        """Set up the select."""
        super().__init__(app, device, "select", "sensitivity")

    @property
    def current_option(self) -> str | None:
        """The sensitivity the device reported last."""
        return self._device.sensitivity

    async def async_select_option(self, option: str) -> None:
        """Write the sensitivity; the select follows the readback."""
        self._run(self._device.set_sensitivity, option)


class ApproachDistanceSelect(Fp2Entity, SelectEntity):
    """How close a person must come before the FP2 reports an approach."""

    _attr_translation_key = "approach_distance"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(APPROACH_LABELS.values())

    def __init__(self, app: HassFp2Companion, device: Fp2Device) -> None:
        """Set up the select."""
        super().__init__(app, device, "select", "approach_distance")

    @property
    def current_option(self) -> str | None:
        """The approach distance the device reported last."""
        return self._device.approach_distance

    async def async_select_option(self, option: str) -> None:
        """Write the approach distance; the select follows the readback."""
        self._run(self._device.set_approach_distance, option)


class ZoneSensitivitySelect(Fp2Entity, SelectEntity):
    """One detection zone's sensitivity, keyed by the zone's rangeId so a
    rename in the Aqara app keeps the entity."""

    _attr_translation_key = "zone_sensitivity"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(SENSITIVITY_LABELS.values())

    def __init__(
        self, app: HassFp2Companion, device: Fp2Device, range_id: int, name: str
    ) -> None:
        """Set up the select. Its entity id carries the zone's name, which
        reads better than the rangeId; the unique id keeps the rangeId."""
        slug = slugify(name) or f"zone_{range_id}"
        super().__init__(
            app,
            device,
            "select",
            f"zone_{range_id}_sensitivity",
            entity_key=f"{slug}_sensitivity",
        )
        self.range_id = range_id
        self.zone_name = name
        self._attr_translation_placeholders = {"zone": name}

    @property
    def available(self) -> bool:
        """Only while the zone is there."""
        return self._device.zone_sensitivity(self.range_id) is not None

    @property
    def current_option(self) -> str | None:
        """The zone's sensitivity as the device reported it last."""
        return self._device.zone_sensitivity(self.range_id)

    async def async_select_option(self, option: str) -> None:
        """Write the zone's sensitivity; the select follows the readback."""
        self._run(self._device.set_zone_sensitivity, self.range_id, option)


class _ZoneSelects:
    """Adds a select for each detection zone that appears and removes the
    select of a zone that is gone, after every read of the device."""

    def __init__(
        self,
        app: HassFp2Companion,
        device: Fp2Device,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        self._app = app
        self._device = device
        self._add = async_add_entities
        self._shown: dict[int, ZoneSensitivitySelect] = {}
        self._pruned = False

    def start(self, entry: Fp2CompanionConfigEntry) -> None:
        entry.async_on_unload(
            async_dispatcher_connect(
                self._app.hass,
                signal_changed(self._app.entry_id, self._device.key),
                self._sync,
            )
        )
        self._sync()

    async def _async_replace(
        self, old: ZoneSensitivitySelect, new: ZoneSensitivitySelect
    ) -> None:
        await old.async_remove()
        self._add([new])

    @callback
    def _sync(self) -> None:
        device = self._device
        # Before the first read the zones are unknown, not gone.
        if device.last_read is None:
            return
        zones = {z["rangeId"]: z.get("name") or "" for z in device.detection_zones()}
        for rid, entity in list(self._shown.items()):
            if rid in zones and zones[rid] != entity.zone_name:
                # Renamed in the Aqara app. Home Assistant keeps an entity's
                # translated name, so the select is added again: the registry
                # keeps its entity id and every setting, by its unique id.
                renamed = ZoneSensitivitySelect(self._app, device, rid, zones[rid])
                self._shown[rid] = renamed
                self._app.hass.async_create_task(
                    self._async_replace(entity, renamed), "fp2_companion zone rename"
                )
        new = [
            ZoneSensitivitySelect(self._app, device, rid, name)
            for rid, name in zones.items()
            if rid not in self._shown
        ]
        for entity in new:
            self._shown[entity.range_id] = entity
        if new:
            self._add(new)
        registry = er.async_get(self._app.hass)
        gone = {
            self._shown.pop(rid).unique_id or ""
            for rid in list(self._shown)
            if rid not in zones
        }
        if not self._pruned:
            # A zone deleted while Home Assistant was down left its select in
            # the registry; the first read finds it.
            self._pruned = True
            mine = unique_id(device, "zone_")
            gone |= {
                e.unique_id
                for e in er.async_entries_for_config_entry(
                    registry, self._app.entry_id
                )
                if e.domain == "select"
                and e.unique_id.startswith(mine)
                and e.unique_id not in {s.unique_id for s in self._shown.values()}
            }
        for unique in gone:
            entity_id = registry.async_get_entity_id("select", DOMAIN, unique)
            if entity_id is not None:
                registry.async_remove(entity_id)
