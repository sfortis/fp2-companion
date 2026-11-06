"""The card's commands and its live view, as websocket commands (decision 27).

Up to version 1.4.x the card sent its commands with the `mqtt.publish` action,
which every Home Assistant user may call, and read the live positions with
an MQTT subscription. Every command here requires an admin user: a zone
write, a restore or a sign-in changes the FP2 and the Aqara account.

A command is queued on the app's worker and answered at once; what it came
to shows on the device's cloud status sensor, as before. A backup preview is
the exception: it is answered with the backup itself, since nothing about
it needs to stay on an entity.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .zone_model import read_backup

if TYPE_CHECKING:
    from .companion import Fp2Device
    from .runtime import HassFp2Companion

# A live view renews its lease this often; the tracker drops a lease that
# missed two renewals (live_track.LEASE_SECONDS).
LEASE_RENEW = timedelta(seconds=30)

ERR_NOT_LOADED = "not_loaded"
ERR_UNKNOWN_DEVICE = "unknown_device"
ERR_NO_BACKUP = "no_backup"

# What the card sends for a zone. The app takes only these fields and reads
# everything else from the zone as the cloud holds it.
ZONE_SCHEMA = vol.Schema(
    {
        vol.Optional("rangeId"): vol.All(int, vol.Range(min=0)),
        vol.Optional("name"): vol.All(str, vol.Length(max=64)),
        vol.Optional("blockList"): [vol.All(int, vol.Range(min=0))],
        vol.Optional("sensitivity"): vol.All(int, vol.Range(min=1, max=3)),
        vol.Optional("tagId"): vol.Any(None, vol.All(str, vol.Length(max=64))),
        vol.Optional("regionType"): vol.All(int, vol.Range(min=0)),
    }
)
DEVICE = vol.All(str, vol.Length(min=1, max=32))
BACKUP_NAME = vol.All(str, vol.Length(min=1, max=128))


@callback
def async_setup(hass: HomeAssistant) -> None:
    """Register the commands, once for the integration."""
    for command in (
        ws_zone_set,
        ws_zone_delete,
        ws_backup_preview,
        ws_backup_restore,
        ws_login,
        ws_logout,
        ws_live_subscribe,
    ):
        websocket_api.async_register_command(hass, command)


def _app(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> HassFp2Companion | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], ERR_NOT_LOADED, "FP2 Companion is not loaded")
        return None
    return entries[0].runtime_data


def _device(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> tuple[HassFp2Companion, Fp2Device] | None:
    app = _app(hass, connection, msg)
    if app is None:
        return None
    device = app.devices.get(msg["device"])
    if device is None:
        connection.send_error(
            msg["id"], ERR_UNKNOWN_DEVICE, f"No FP2 {msg['device']} is served"
        )
        return None
    return app, device


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "fp2_companion/zone/set",
        vol.Required("device"): DEVICE,
        vol.Required("zone"): ZONE_SCHEMA,
    }
)
@callback
def ws_zone_set(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Create a zone, or edit one when the zone carries its rangeId."""
    if found := _device(hass, connection, msg):
        app, device = found
        app.submit(device.zone_set, dict(msg["zone"]))
        connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "fp2_companion/zone/delete",
        vol.Required("device"): DEVICE,
        vol.Required("range_id"): vol.All(int, vol.Range(min=0)),
    }
)
@callback
def ws_zone_delete(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete a zone by its rangeId."""
    if found := _device(hass, connection, msg):
        app, device = found
        app.submit(device.zone_delete, msg["range_id"])
        connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "fp2_companion/backup/preview",
        vol.Required("device"): DEVICE,
        vol.Required("name"): BACKUP_NAME,
    }
)
@websocket_api.async_response
async def ws_backup_preview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """One backup's zones, so the card can draw them before a restore."""
    found = _device(hass, connection, msg)
    if found is None:
        return
    device = found[1]
    try:
        body = await hass.async_add_executor_job(
            read_backup, device.info["backup_dir"], msg["name"]
        )
    except (OSError, ValueError):
        connection.send_error(msg["id"], ERR_NO_BACKUP, f"No backup {msg['name']}")
        return
    connection.send_result(
        msg["id"],
        {
            "name": msg["name"],
            "saved_at": body.get("saved_at"),
            "reason": body.get("reason"),
            "zones": body.get("zones", []),
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "fp2_companion/backup/restore",
        vol.Required("device"): DEVICE,
        vol.Required("name"): BACKUP_NAME,
    }
)
@callback
def ws_backup_restore(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Put the zones back as one backup holds them."""
    if found := _device(hass, connection, msg):
        app, device = found
        app.submit(device.restore, msg["name"])
        connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "fp2_companion/login",
        vol.Required("account"): vol.All(str, vol.Length(min=1, max=128)),
        vol.Required("password_rsa"): vol.All(str, vol.Length(min=1, max=4096)),
        vol.Required("country"): vol.All(str, vol.Length(min=2, max=8)),
    }
)
@callback
def ws_login(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Sign in to Aqara with the password the card encrypted."""
    if app := _app(hass, connection, msg):
        app.submit(
            app.login,
            {
                "account": msg["account"],
                "password_rsa": msg["password_rsa"],
                "country": msg["country"],
            },
        )
        connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "fp2_companion/logout"})
@callback
def ws_logout(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Sign out of Aqara."""
    if app := _app(hass, connection, msg):
        app.submit(app.logout)
        connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "fp2_companion/live/subscribe", vol.Required("device"): DEVICE}
)
@callback
def ws_live_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The live positions of one FP2, for as long as the subscription is
    open. The subscription is the card's lease: the FP2 streams positions
    only while one is open (live_track.py)."""
    found = _device(hass, connection, msg)
    if found is None:
        return
    app, device = found
    msg_id = msg["id"]
    client = f"ws-{id(connection):x}-{msg_id}"

    @callback
    def forward(message: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg_id, message))

    @callback
    def renew(_now: Any) -> None:
        app.submit(device.track, client, True)

    remove_listener = app.async_listen_live(device.key, forward)
    cancel_renew = async_track_time_interval(hass, renew, LEASE_RENEW)
    app.submit(device.track, client, True)

    @callback
    def unsubscribe() -> None:
        remove_listener()
        cancel_renew()
        app.submit(device.track, client, False)

    connection.subscriptions[msg_id] = unsubscribe
    connection.send_result(msg_id)
