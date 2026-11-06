"""AI person detection, which the Aqara app calls the AI high-precision
mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import Fp2Entity, async_setup_devices

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
    """Add the switch of every FP2."""
    app = entry.runtime_data
    entry.async_on_unload(
        async_setup_devices(
            app, async_add_entities, lambda device: [AiDetectionSwitch(app, device)]
        )
    )


class AiDetectionSwitch(Fp2Entity, SwitchEntity):
    """AI person detection. While it is on, the FP2 sets its sensitivity
    itself and ignores a written one."""

    _attr_translation_key = "ai_detection"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, app: HassFp2Companion, device: Fp2Device) -> None:
        """Set up the switch."""
        super().__init__(app, device, "switch", "ai_detection")

    @property
    def is_on(self) -> bool | None:
        """As the device reported it last."""
        return self._device.ai_detection

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn it on; the switch follows the readback."""
        self._run(self._device.set_ai_detection, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn it off; the switch follows the readback."""
        self._run(self._device.set_ai_detection, False)
