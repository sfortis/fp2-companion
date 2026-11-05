"""FP2 Companion: every Aqara FP2's settings and zones in Home Assistant.

The integration starts the app (`companion.py`) on a runtime of Home
Assistant's (`runtime.py`). Its entities are MQTT discovery entities, as they
were under AppDaemon, so entity ids, the card and its commands are unchanged
(decision 26).
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir

from .const import (
    APPDAEMON_APP_DIR,
    APPDAEMON_STATE_DIR,
    BASE_TOPIC,
    CONF_APP_IDENTITIES,
    CONF_BACKUPS_KEEP,
    CONF_DEVICES,
    CONF_EXCLUDE,
    CONF_TOKEN_CHECK_TIME,
    DEFAULT_BACKUPS_KEEP,
    DEFAULT_TOKEN_CHECK_TIME,
    DOMAIN,
    ISSUE_APPDAEMON_APP,
    STATE_DIR,
)
from .runtime import HassFp2Companion

_LOGGER = logging.getLogger(__name__)

type Fp2CompanionConfigEntry = ConfigEntry[HassFp2Companion]


async def async_setup_entry(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry
) -> bool:
    """Start the app."""
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT is not available")
    state_dir = hass.config.path(STATE_DIR)
    old_app = hass.config.path(APPDAEMON_APP_DIR)
    app_still_there = await hass.async_add_executor_job(
        _prepare_state, state_dir, old_app
    )
    _async_report_old_app(hass, app_still_there, old_app)

    app = HassFp2Companion(hass, _app_args(hass, entry, state_dir))
    await app.async_start()
    entry.runtime_data = app
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry
) -> bool:
    """Stop the app."""
    await entry.runtime_data.async_stop()
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _prepare_state(state_dir: str, old_app: str) -> bool:
    """Create the state folder. On the first setup after the AppDaemon app,
    copy its session and backups, so nobody signs in again; the originals
    stay. Returns whether the AppDaemon app is still installed."""
    first = not os.path.exists(os.path.join(state_dir, "session.json"))
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    if first and os.path.isfile(os.path.join(APPDAEMON_STATE_DIR, "session.json")):
        for name in ("session.json", "runtime.json"):
            source = os.path.join(APPDAEMON_STATE_DIR, name)
            if os.path.isfile(source):
                shutil.copy2(source, os.path.join(state_dir, name))
        backups = os.path.join(APPDAEMON_STATE_DIR, "backups")
        if os.path.isdir(backups):
            shutil.copytree(
                backups, os.path.join(state_dir, "backups"), dirs_exist_ok=True
            )
        _LOGGER.info(
            "Copied the session and the zone backups of the AppDaemon app "
            "from %s",
            APPDAEMON_STATE_DIR,
        )
    return os.path.isdir(old_app)


@callback
def _async_report_old_app(hass: HomeAssistant, still_there: bool, path: str) -> None:
    """A repair while the AppDaemon app is installed: if AppDaemon runs it,
    the two refresh one session and revoke each other's token."""
    if still_there:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_APPDAEMON_APP,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_APPDAEMON_APP,
            translation_placeholders={"path": path},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_APPDAEMON_APP)


def _app_args(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry, state_dir: str
) -> dict[str, Any]:
    """The options, in the shape the AppDaemon app's yaml had."""
    options = entry.options
    return {
        "state_dir": state_dir,
        "base_topic": BASE_TOPIC,
        "discovery_prefix": _discovery_prefix(hass),
        "devices": list(options.get(CONF_DEVICES, [])),
        "exclude": list(options.get(CONF_EXCLUDE, [])),
        "backups_keep": int(options.get(CONF_BACKUPS_KEEP, DEFAULT_BACKUPS_KEEP)),
        "token_check_time": options.get(
            CONF_TOKEN_CHECK_TIME, DEFAULT_TOKEN_CHECK_TIME
        ),
        "app_identities": parse_identities(options.get(CONF_APP_IDENTITIES, [])),
        "card_dir": hass.config.path("www", "fp2-companion"),
    }


def parse_identities(values: list[str]) -> list[list[str]]:
    """`app_id:app_key` per entry, as [app_id, app_key] pairs."""
    pairs = []
    for value in values:
        app_id, sep, app_key = str(value).strip().partition(":")
        if sep and app_id.strip() and app_key.strip():
            pairs.append([app_id.strip(), app_key.strip()])
    return pairs


def _discovery_prefix(hass: HomeAssistant) -> str:
    """The MQTT integration's discovery prefix, so the entities appear even
    where it is not the default."""
    for entry in hass.config_entries.async_entries(mqtt.DOMAIN):
        conf = {**entry.data, **entry.options}
        return str(conf.get(mqtt.CONF_DISCOVERY_PREFIX, "homeassistant"))
    return "homeassistant"
