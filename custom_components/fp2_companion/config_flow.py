"""Config flow for FP2 Companion.

There is nothing to ask at setup: the FP2s are found in Home Assistant and
the Aqara sign-in happens on the card (decision 15). The options are what the
AppDaemon app's yaml held.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TimeSelector,
)

from .aqara_cloud import did_from_serial
from .const import (
    CONF_APP_IDENTITIES,
    CONF_BACKUPS_KEEP,
    CONF_DEVICES,
    CONF_EXCLUDE,
    CONF_TOKEN_CHECK_TIME,
    DEFAULT_BACKUPS_KEEP,
    DEFAULT_TOKEN_CHECK_TIME,
    DOMAIN,
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_DEVICES, default=[]): TextSelector(
            TextSelectorConfig(multiple=True)
        ),
        vol.Optional(CONF_EXCLUDE, default=[]): TextSelector(
            TextSelectorConfig(multiple=True)
        ),
        vol.Optional(CONF_BACKUPS_KEEP, default=DEFAULT_BACKUPS_KEEP): NumberSelector(
            NumberSelectorConfig(min=1, max=1000, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(
            CONF_TOKEN_CHECK_TIME, default=DEFAULT_TOKEN_CHECK_TIME
        ): TimeSelector(),
        vol.Optional(CONF_APP_IDENTITIES, default=[]): TextSelector(
            TextSelectorConfig(multiple=True)
        ),
    }
)


class Fp2CompanionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add FP2 Companion. The manifest allows one entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, once MQTT is set up."""
        if not self.hass.config_entries.async_entries(mqtt.DOMAIN):
            return self.async_abort(reason="mqtt_required")
        if user_input is not None:
            return self.async_create_entry(title="FP2 Companion", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """The options flow."""
        return Fp2CompanionOptionsFlow()


class Fp2CompanionOptionsFlow(OptionsFlow):
    """Extra FP2s, excluded FP2s, backups, the token check and app pairs."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            for key in (CONF_DEVICES, CONF_EXCLUDE):
                if not all(_is_serial(s) for s in user_input.get(key, [])):
                    errors[key] = "invalid_serial"
            if not all(
                _is_identity(v) for v in user_input.get(CONF_APP_IDENTITIES, [])
            ):
                errors[CONF_APP_IDENTITIES] = "invalid_identity"
            if not errors:
                return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, user_input or self.config_entry.options
            ),
            errors=errors,
        )


def _is_serial(value: str) -> bool:
    try:
        did_from_serial(value)
    except ValueError:
        return False
    return True


def _is_identity(value: str) -> bool:
    app_id, sep, app_key = str(value).partition(":")
    return bool(sep and app_id.strip() and app_key.strip())
