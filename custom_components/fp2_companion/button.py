"""Refresh, and the start of an AI learning run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
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
    """Add the buttons of every FP2."""
    app = entry.runtime_data
    entry.async_on_unload(
        async_setup_devices(
            app,
            async_add_entities,
            lambda device: [RefreshButton(app, device), AiLearningButton(app, device)],
        )
    )


class RefreshButton(Fp2Entity, ButtonEntity):
    """Read the settings, the zones and the zone types again. The app never
    polls the cloud, so this is how a change made in the Aqara app shows."""

    _attr_translation_key = "refresh"

    def __init__(self, app: HassFp2Companion, device: Fp2Device) -> None:
        """Set up the button."""
        super().__init__(app, device, "button", "refresh")

    async def async_press(self) -> None:
        """Read the device again."""
        self._run(self._device.refresh)


class AiLearningButton(Fp2Entity, ButtonEntity):
    """Start the AI learning run the Aqara app offers; it lasts a minute."""

    _attr_translation_key = "ai_learning"

    def __init__(self, app: HassFp2Companion, device: Fp2Device) -> None:
        """Set up the button."""
        super().__init__(app, device, "button", "ai_learning")

    async def async_press(self) -> None:
        """Start the run."""
        self._run(self._device.start_ai_learning)
