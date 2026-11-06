"""Diagnostics: what a bug report needs, without the account or the FP2s'
identities.

The account, the app identity pairs, the device ids and serials, and every
part of the session are redacted. The zones stay, because most reports are
about them, and they hold only names and grid blocks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
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
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: Fp2CompanionConfigEntry
) -> dict[str, Any]:
    """The entry, the session's state and every FP2 the app serves."""
    app = entry.runtime_data
    devices = []
    for number, device in enumerate(list(app.devices.values()), start=1):
        devices.append(
            {
                "device": number,
                "info": async_redact_data(dict(device.info), TO_REDACT),
                "status": device.status,
                "status_detail": device.status_detail,
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
                "backups": len((device.zones_attrs or {}).get("backups", [])),
            }
        )
    hub = app.hub_attrs or {}
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "session": {
            "signed_in": hub.get("signed_in"),
            "country": hub.get("country"),
            "token_expires": hub.get("token_expires"),
        },
        "zone_types": len(app.zone_tags),
        "devices": devices,
    }
