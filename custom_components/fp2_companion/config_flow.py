"""Config flow for FP2 Companion.

The FP2s are found in Home Assistant, so setup asks only for the Aqara
account, signs in with it and keeps the session, not the password (decision
28). Reauthentication runs when Aqara drops the session, and Reconfigure
changes the account. The options are what the AppDaemon app's yaml held.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)

from .aqara_cloud import did_from_serial
from .auth import SignInFailed, parse_identities, sign_in
from .const import (
    CONF_ACCOUNT,
    CONF_APP_IDENTITIES,
    CONF_APP_IDENTITY,
    CONF_COUNTRY,
    CONF_BACKUPS_KEEP,
    CONF_CLEAR_APP_IDENTITIES,
    CONF_DEVICES,
    CONF_EXCLUDE,
    CONF_TOKEN_CHECK_TIME,
    DEFAULT_BACKUPS_KEEP,
    DEFAULT_TOKEN_CHECK_TIME,
    DOMAIN,
    STATE_DIR,
)
from .regions import COUNTRIES

_LOGGER = logging.getLogger(__name__)

TITLE = "Aqara FP2 Companion"

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
            TextSelectorConfig(type=TextSelectorType.PASSWORD, multiple=True)
        ),
        vol.Optional(CONF_CLEAR_APP_IDENTITIES, default=False): BooleanSelector(),
    }
)


def _sign_in_schema(with_identity: bool) -> vol.Schema:
    """Account, password and country; the first form also takes an app
    identity pair, for a China region account."""
    fields: dict[Any, Any] = {
        vol.Required(CONF_ACCOUNT): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
        vol.Required(CONF_COUNTRY): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=code, label=name)
                    for code, name, _area in COUNTRIES
                ],
                mode=SelectSelectorMode.DROPDOWN,
                sort=False,
            )
        ),
    }
    if with_identity:
        fields[vol.Optional(CONF_APP_IDENTITY)] = TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    return vol.Schema(fields)


class Fp2CompanionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add FP2 Companion. The manifest allows one entry."""

    VERSION = 1

    async def _async_sign_in(
        self, user_input: dict[str, Any], identities: list[list[str]]
    ) -> dict[str, str]:
        """Sign in with what the form holds; the errors for the form."""
        try:
            await self.hass.async_add_executor_job(
                sign_in,
                self.hass.config.path(STATE_DIR),
                user_input[CONF_ACCOUNT],
                user_input[CONF_PASSWORD],
                user_input[CONF_COUNTRY],
                identities,
            )
        except SignInFailed as exc:
            return {"base": exc.reason.value}
        except Exception:
            _LOGGER.exception("Unexpected error while signing in to Aqara")
            return {"base": "unknown"}
        return {}

    def _defaults(self, entry: ConfigEntry | None = None) -> dict[str, Any]:
        """The account and country to suggest: the entry's, or the country
        Home Assistant is set to."""
        codes = {code for code, _name, _area in COUNTRIES}
        country = (entry.data.get(CONF_COUNTRY) if entry else None) or (
            self.hass.config.country if self.hass.config.country in codes else None
        )
        out: dict[str, Any] = {}
        if entry and entry.data.get(CONF_ACCOUNT):
            out[CONF_ACCOUNT] = entry.data[CONF_ACCOUNT]
        if country:
            out[CONF_COUNTRY] = country
        return out

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in to Aqara."""
        errors: dict[str, str] = {}
        if user_input is not None:
            identity = str(user_input.get(CONF_APP_IDENTITY) or "").strip()
            identities = parse_identities([identity]) if identity else []
            if identity and not identities:
                errors[CONF_APP_IDENTITY] = "invalid_identity"
            else:
                errors = await self._async_sign_in(user_input, identities)
            if not errors:
                return self.async_create_entry(
                    title=TITLE,
                    data=_account_data(user_input),
                    options={CONF_APP_IDENTITIES: [identity]} if identity else {},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _sign_in_schema(with_identity=True),
                _without_password(user_input) or self._defaults(),
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Aqara dropped the session."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in again."""
        return await self._async_sign_in_again(
            "reauth_confirm", self._get_reauth_entry(), user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in again, with this account or another."""
        return await self._async_sign_in_again(
            "reconfigure", self._get_reconfigure_entry(), user_input
        )

    async def _async_sign_in_again(
        self, step_id: str, entry: ConfigEntry, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            identities = parse_identities(entry.options.get(CONF_APP_IDENTITIES, []))
            errors = await self._async_sign_in(user_input, identities)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates=_account_data(user_input)
                )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                _sign_in_schema(with_identity=False),
                _without_password(user_input) or self._defaults(entry),
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """The options flow."""
        return Fp2CompanionOptionsFlow()


def _account_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """What the entry keeps of a sign-in: never the password."""
    return {
        CONF_ACCOUNT: str(user_input[CONF_ACCOUNT]).strip(),
        CONF_COUNTRY: user_input[CONF_COUNTRY],
    }


def _without_password(user_input: dict[str, Any] | None) -> dict[str, Any]:
    """A rejected form shows again with what was typed, but not the
    password."""
    return {k: v for k, v in (user_input or {}).items() if k != CONF_PASSWORD}


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
            # Clearing and replacing at once is ambiguous, so neither happens.
            if user_input.get(CONF_CLEAR_APP_IDENTITIES) and user_input.get(
                CONF_APP_IDENTITIES
            ):
                errors[CONF_CLEAR_APP_IDENTITIES] = "clear_and_replace"
            if not errors:
                data = dict(user_input)
                clear = data.pop(CONF_CLEAR_APP_IDENTITIES, False)
                # The pairs are never sent to the browser, so the field
                # starts empty: an empty submit keeps the stored pairs.
                if clear:
                    data[CONF_APP_IDENTITIES] = []
                elif not data.get(CONF_APP_IDENTITIES):
                    data[CONF_APP_IDENTITIES] = list(
                        self.config_entry.options.get(CONF_APP_IDENTITIES, [])
                    )
                return self.async_create_entry(data=data)
        if user_input is not None:
            # A rejected submit shows the form again with what was typed,
            # pairs included: they came from this browser, not from storage.
            suggested = user_input
        else:
            suggested = {
                key: value
                for key, value in self.config_entry.options.items()
                if key != CONF_APP_IDENTITIES
            }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, suggested
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
