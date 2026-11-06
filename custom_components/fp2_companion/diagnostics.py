"""Diagnostics: what a bug report needs, without the account or the FP2s'
identities.

The account, the app identity pairs, the device ids and serials (also those
listed in the options), the areas and every part of the session are
redacted. A refusal keeps its code but not its text, which can name a path
or a device id. The zones stay, because most reports are about them, and
they hold only names and grid blocks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from . import Fp2CompanionConfigEntry

TO_REDACT = {
    "account",
    "app_identities",
    "did",
    "key",
    "serial",
    "prefix",
    "name",
    "token",
    "user_id",
    "phone_id",
    "appid",
    "appkey",
    "backup_dir",
    "devices",
    "exclude",
    "area",
}
# What a status detail keeps: codes and numbers, never free text.
DETAIL_KEPT = ("code", "kind", "rangeId", "rid", "wrote", "reads")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry
) -> dict[str, Any]:
    """The entry, the session's state and every FP2 the app serves."""
    entry_part = {
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "state": entry.state.value,
    }
    app = getattr(entry, "runtime_data", None)
    if app is None or entry.state is not ConfigEntryState.LOADED:
        # The setup failed (no session, or the AppDaemon app is installed).
        return {"entry": entry_part}
    devices = []
    for number, device in enumerate(list(app.devices.values()), start=1):
        devices.append(
            {
                "device": number,
                "info": async_redact_data(dict(device.info), TO_REDACT),
                "status": device.status,
                "status_detail": {
                    k: v
                    for k, v in (device.status_detail or {}).items()
                    if k in DETAIL_KEPT
                }
                or None,
                "last_error": device.last_error,
                "last_read": device.last_read,
                "values": dict(device.values),
                "zones": [
                    async_redact_data(
                        {k: v for k, v in zone.items() if k != "deviceId"},
                        TO_REDACT - {"name"},
                    )
                    for zone in device.zones
                ],
                "backups_shown": len((device.zones_attrs or {}).get("backups", [])),
            }
        )
    hub = app.hub_attrs or {}
    return {
        "entry": entry_part,
        "session": {
            "signed_in": hub.get("signed_in"),
            "country": hub.get("country"),
            "token_expires": hub.get("token_expires"),
        },
        "zone_types": len(app.zone_tags),
        "devices": devices,
    }
