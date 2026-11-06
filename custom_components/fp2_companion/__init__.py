"""FP2 Companion: every Aqara FP2's settings and zones in Home Assistant.

The integration starts the app (`companion.py`) on a runtime of Home
Assistant's (`runtime.py`, decision 26). Its entities are native and keep the
entity ids of the MQTT discovery entities of versions up to 1.4.x, which the
first setup moves over (`migrate.py`, decision 27).
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import (
    APPDAEMON_APP_DIRS,
    APPDAEMON_STATE_DIR,
    CONF_APP_IDENTITIES,
    CONF_BACKUPS_KEEP,
    CONF_DEVICES,
    CONF_EXCLUDE,
    CONF_TOKEN_CHECK_TIME,
    COPIED_MARKER,
    DEFAULT_BACKUPS_KEEP,
    DEFAULT_TOKEN_CHECK_TIME,
    DOMAIN,
    ISSUE_APPDAEMON_APP,
    STATE_DIR,
)
from .migrate import async_migrate_mqtt_entities
from .runtime import HassFp2Companion
from .websocket_api import async_setup as async_setup_websocket_api

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the card's websocket commands."""
    async_setup_websocket_api(hass)
    return True

type Fp2CompanionConfigEntry = ConfigEntry[HassFp2Companion]


async def async_setup_entry(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry
) -> bool:
    """Start the app."""
    # While the AppDaemon app is installed it may be running: both would act
    # on every card command and refresh one session, signing each other out.
    # So the integration waits, and Home Assistant retries the setup until
    # the folder is gone. Only then is the app's session the newest one.
    old_app = await hass.async_add_executor_job(_installed_app, hass.config.path())
    if old_app is not None:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_APPDAEMON_APP,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_APPDAEMON_APP,
            translation_placeholders={"path": old_app},
        )
        raise ConfigEntryNotReady(
            f"The AppDaemon app of FP2 Companion is still in {old_app}"
        )
    ir.async_delete_issue(hass, DOMAIN, ISSUE_APPDAEMON_APP)
    state_dir = hass.config.path(STATE_DIR)
    await hass.async_add_executor_job(_prepare_state, state_dir)
    await async_migrate_mqtt_entities(hass, entry)

    app = HassFp2Companion(hass, entry.entry_id, _app_args(hass, entry, state_dir))
    try:
        await app.async_start()
    except BaseException:
        # The app's timers must not stay behind a failed start.
        await app.async_stop()
        raise
    entry.runtime_data = app
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _async_stop_app(_event: Event) -> None:
        # Home Assistant does not unload entries when it shuts down.
        await app.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop_app)
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry
) -> bool:
    """Remove the entities, then stop the app."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_stop()
    return True


async def async_remove_entry(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry
) -> None:
    """Take the repair with the entry."""
    ir.async_delete_issue(hass, DOMAIN, ISSUE_APPDAEMON_APP)


async def _async_options_updated(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _installed_app(config_dir: str) -> str | None:
    """The folder of the AppDaemon app, if it is still installed."""
    for folder in APPDAEMON_APP_DIRS:
        path = os.path.join(config_dir, folder)
        if os.path.isdir(path):
            return path
    return None


def _prepare_state(state_dir: str) -> None:
    """Create the state folder. On the first setup after the AppDaemon app,
    copy its session and backups, so nobody signs in again.

    A marker file says the copy went through, so a session made before the
    integration (with `tools/login.py`), or a copy that stopped halfway, does
    not decide it. A file that is already here is never overwritten: it is
    the newer one. Once everything is copied, the old session is deleted: its
    token would stay valid for weeks, as the refresh happens only three days
    before it expires, and it is the same session."""
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    marker = os.path.join(state_dir, COPIED_MARKER)
    if os.path.exists(marker) or not os.path.isfile(
        os.path.join(APPDAEMON_STATE_DIR, "session.json")
    ):
        return
    for name in ("session.json", "runtime.json"):
        source = os.path.join(APPDAEMON_STATE_DIR, name)
        target = os.path.join(state_dir, name)
        if os.path.isfile(source) and not os.path.exists(target):
            shutil.copy2(source, target)
    backups = os.path.join(APPDAEMON_STATE_DIR, "backups")
    if os.path.isdir(backups):
        shutil.copytree(
            backups,
            os.path.join(state_dir, "backups"),
            copy_function=_copy_missing,
            dirs_exist_ok=True,
        )
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("copied from the AppDaemon app\n")
    _LOGGER.info(
        "Copied the session and the zone backups of the AppDaemon app from %s",
        APPDAEMON_STATE_DIR,
    )
    try:
        os.remove(os.path.join(APPDAEMON_STATE_DIR, "session.json"))
    except OSError as exc:
        _LOGGER.warning(
            "Could not delete the old session in %s, delete it yourself: %s",
            APPDAEMON_STATE_DIR,
            exc,
        )


def _copy_missing(source: str, target: str) -> None:
    if not os.path.exists(target):
        shutil.copy2(source, target)


def _app_args(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry, state_dir: str
) -> dict[str, Any]:
    """The options, in the shape the AppDaemon app's yaml had."""
    options = entry.options
    return {
        "state_dir": state_dir,
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
