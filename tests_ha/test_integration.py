"""The integration inside Home Assistant: setup, entities, the move from
MQTT, options, unload.

The cloud is never reached: there is no session, so every call stops at
"no session" before a request is sent.
"""
from datetime import timedelta
import json
import os
import time

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

import custom_components.fp2_companion as integration
from custom_components.fp2_companion.const import DOMAIN

SERIAL = "54EF44A1B2C3"
KEY = "54ef44a1b2c3"
OBJ = "fp2_companion_lumi1_54ef44a1b2c3"
PREFIX = "presence_sensor_fp2_b2c3"
HUB = "sensor.fp2_companion_devices"


async def wait_for_worker(hass: HomeAssistant, entry) -> None:
    """Let the worker finish what is queued, then the loop write the states."""
    await hass.async_block_till_done()
    await hass.async_add_executor_job(
        lambda: entry.runtime_data._worker.submit(lambda: None).result(5))
    await hass.async_block_till_done()


async def start(hass: HomeAssistant, entry) -> None:
    """Set up the entry and let the app start, which it does 5 s later."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for_worker(hass, entry)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await wait_for_worker(hass, entry)


async def run(hass: HomeAssistant, entry, func, *args) -> None:
    """One of the app's commands, on its worker, as an entity sends it."""
    entry.runtime_data.submit(func, *args)
    await wait_for_worker(hass, entry)


@pytest.fixture
def fp2_device(hass: HomeAssistant):
    """An FP2 paired over HomeKit, in the Living Room."""
    homekit = MockConfigEntry(domain="homekit_controller")
    homekit.add_to_hass(hass)
    area = ar.async_get(hass).async_create("Living Room")
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=homekit.entry_id,
        identifiers={("homekit_controller", "1")},
        manufacturer="Aqara", model="PS-S02D", serial_number=SERIAL,
        name="Presence-Sensor-FP2-B2C3", suggested_area=area.name)


@pytest.fixture
async def entry(hass: HomeAssistant, fp2_device):
    entry = MockConfigEntry(domain=DOMAIN, title="Aqara FP2 Companion")
    entry.add_to_hass(hass)
    await start(hass, entry)
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_creates_one_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
    for entry in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_entities_sit_on_a_settings_device(
        hass, entry, fp2_device) -> None:
    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    settings = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "lumi1.54ef44a1b2c3"), entry.entry_id)
    assert settings.name == "Presence-Sensor-FP2-B2C3 Settings"
    assert settings.via_device_id is None
    assert settings.config_entry_id == entry.entry_id
    assert settings.area_id == fp2_device.area_id
    for entity_id in (f"select.{PREFIX}_sensitivity",
                      f"select.{PREFIX}_approach_distance",
                      f"switch.{PREFIX}_ai_detection",
                      f"button.{PREFIX}_refresh",
                      f"button.{PREFIX}_ai_learning",
                      f"sensor.{PREFIX}_zones",
                      f"sensor.{PREFIX}_backup_preview",
                      f"sensor.{PREFIX}_cloud_status"):
        reg = registry.async_get(entity_id)
        assert reg is not None, entity_id
        assert reg.platform == DOMAIN
        assert reg.device_id == settings.id
    # The HomeKit device stays HomeKit Device's alone.
    assert dr.async_get(hass).async_get(fp2_device.id).config_entry_id \
        != entry.entry_id
    assert hass.states.get(f"select.{PREFIX}_sensitivity").name \
        == "Presence-Sensor-FP2-B2C3 Settings Sensitivity"
    hub = hass.states.get(HUB)
    assert hub.state == "1"
    assert [d["did"] for d in hub.attributes["devices"]] == ["lumi1.54ef44a1b2c3"]
    assert hub.attributes["signed_in"] is False
    # No session: the status says so instead of calling the cloud.
    assert hass.states.get(f"sensor.{PREFIX}_cloud_status").state == "no_session"


async def test_a_command_reaches_the_app(hass, entry) -> None:
    device = entry.runtime_data.devices[KEY]
    await run(hass, entry, device.show_preview, "no-such-backup")
    assert hass.states.get(f"sensor.{PREFIX}_cloud_status").state == "refused"


async def test_options_are_validated_and_reload(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"devices": ["not-a-serial"]})
    assert result["errors"] == {"devices": "invalid_serial"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"devices": ["54EF44D4E5F6"],
                            "app_identities": ["id:key"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=12))
    await wait_for_worker(hass, entry)
    hub = hass.states.get(HUB)
    assert sorted(d["did"] for d in hub.attributes["devices"]) == [
        "lumi1.54ef44a1b2c3", "lumi1.54ef44d4e5f6"]
    assert entry.runtime_data.args["app_identities"] == [["id", "key"]]
    # An FP2 added by serial has no HomeKit device, so it gets one of ours.
    manual = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "lumi1.54ef44d4e5f6"), entry.entry_id)
    assert manual is not None and manual.name == "FP2 E5F6 Settings"
    assert manual.via_device_id is None
    assert hass.states.get("select.fp2_e5f6_sensitivity") is not None


async def test_the_appdaemon_state_is_copied_once(
        hass, fp2_device, tmp_path, monkeypatch) -> None:
    old = tmp_path / "appdaemon_state"
    (old / "backups" / "lumi1.54ef44a1b2c3").mkdir(parents=True)
    (old / "session.json").write_text(json.dumps({"phone_id": "kept"}))
    (old / "backups" / "lumi1.54ef44a1b2c3" / "zones.json").write_text("[]")
    monkeypatch.setattr(integration, "APPDAEMON_STATE_DIR", str(old))
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    state = hass.config.path(".storage", "fp2_companion")
    with open(os.path.join(state, "session.json")) as fh:
        assert json.load(fh) == {"phone_id": "kept"}
    assert os.path.isfile(os.path.join(
        state, "backups", "lumi1.54ef44a1b2c3", "zones.json"))
    assert os.path.isfile(os.path.join(state, "copied_from_appdaemon"))
    # The old token is not left behind; the backups are.
    assert not (old / "session.json").exists()
    assert (old / "backups" / "lumi1.54ef44a1b2c3" / "zones.json").exists()
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("folder", ["fp2-companion", "fp2_companion"])
async def test_it_waits_while_the_appdaemon_app_is_installed(
        hass, fp2_device, tmp_path, monkeypatch, folder) -> None:
    """HACS names the folder fp2-companion; a copy by hand, fp2_companion."""
    old = tmp_path / "appdaemon_state"
    old.mkdir()
    (old / "session.json").write_text(json.dumps({"phone_id": "kept"}))
    monkeypatch.setattr(integration, "APPDAEMON_STATE_DIR", str(old))
    old_app = hass.config.path("appdaemon", "apps", folder)
    await hass.async_add_executor_job(
        lambda: os.makedirs(old_app, exist_ok=True))
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, "appdaemon_app_installed")
    # Nothing was copied and nothing was published while it waits.
    state = hass.config.path(".storage", "fp2_companion", "session.json")
    assert not await hass.async_add_executor_job(os.path.exists, state)
    assert hass.states.get(HUB) is None

    # Once the folder is gone, the next retry starts it and clears the repair.
    await hass.async_add_executor_job(os.rmdir, old_app)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    await wait_for_worker(hass, entry)
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, "appdaemon_app_installed")
    assert await hass.async_add_executor_job(os.path.exists, state)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_stops_the_worker(hass, entry) -> None:
    worker = entry.runtime_data._worker
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert worker._shutdown


async def test_a_session_made_before_the_integration_is_not_overwritten(
        hass, fp2_device, tmp_path, monkeypatch) -> None:
    """`login.py` may have written session.json already; the copy then still
    brings the backups, and the newer session stays."""
    old = tmp_path / "appdaemon_state"
    (old / "backups" / "lumi1.54ef44a1b2c3").mkdir(parents=True)
    (old / "session.json").write_text(json.dumps({"phone_id": "old"}))
    (old / "backups" / "lumi1.54ef44a1b2c3" / "zones.json").write_text("[]")
    monkeypatch.setattr(integration, "APPDAEMON_STATE_DIR", str(old))
    state = hass.config.path(".storage", "fp2_companion")

    def make_new():
        os.makedirs(state)
        with open(os.path.join(state, "session.json"), "w") as fh:
            json.dump({"phone_id": "new"}, fh)

    await hass.async_add_executor_job(make_new)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    with open(os.path.join(state, "session.json")) as fh:
        assert json.load(fh) == {"phone_id": "new"}
    assert os.path.isfile(os.path.join(
        state, "backups", "lumi1.54ef44a1b2c3", "zones.json"))
    assert os.path.isfile(os.path.join(state, "copied_from_appdaemon"))
    assert await hass.config_entries.async_unload(entry.entry_id)




async def test_removing_the_entry_removes_the_repair(
        hass, fp2_device) -> None:
    old_app = hass.config.path("appdaemon", "apps", "fp2-companion")
    await hass.async_add_executor_job(
        lambda: os.makedirs(old_app, exist_ok=True))
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "appdaemon_app_installed")
    await hass.config_entries.async_remove(entry.entry_id)
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, "appdaemon_app_installed")


async def test_a_failed_start_stops_the_app(
        hass, fp2_device, monkeypatch) -> None:
    from custom_components.fp2_companion.runtime import HassFp2Companion

    stopped = []
    real_stop = HassFp2Companion.async_stop

    def boom(self):
        raise RuntimeError("no start")

    async def stop(self):
        stopped.append(True)
        await real_stop(self)

    monkeypatch.setattr(HassFp2Companion, "initialize", boom)
    monkeypatch.setattr(HassFp2Companion, "async_stop", stop)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert stopped == [True]


async def test_home_assistant_stopping_stops_the_app(hass, entry) -> None:
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    worker = entry.runtime_data._worker
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert worker._shutdown


async def test_stored_identities_are_not_sent_and_kept_when_empty(
        hass, fp2_device) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, options={"app_identities": ["id:key"]})
    entry.add_to_hass(hass)
    await start(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    field = next(k for k in schema if k == "app_identities")
    assert "id:key" not in str(field.description)
    # An empty submit keeps the stored pairs.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"backups_keep": 5})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["app_identities"] == ["id:key"]
    assert "clear_app_identities" not in entry.options
    await hass.async_block_till_done()
    # New pairs replace them, and the clear box empties them.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"app_identities": ["a:b"]})
    assert entry.options["app_identities"] == ["a:b"]
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"clear_app_identities": True})
    assert entry.options["app_identities"] == []
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)



async def test_clearing_and_replacing_identities_at_once_is_refused(
        hass, fp2_device) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, options={"app_identities": ["id:key"]})
    entry.add_to_hass(hass)
    await start(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"app_identities": ["a:b"], "clear_app_identities": True})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"clear_app_identities": "clear_and_replace"}
    assert entry.options["app_identities"] == ["id:key"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_rejected_submit_keeps_the_typed_identities(
        hass, fp2_device) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, options={"app_identities": ["id:key"]})
    entry.add_to_hass(hass)
    await start(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"devices": ["bad"], "app_identities": ["a:b"]})
    assert result["errors"] == {"devices": "invalid_serial"}
    field = next(
        k for k in result["data_schema"].schema if k == "app_identities")
    assert field.description == {"suggested_value": ["a:b"]}
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_terminate_before_initialize_does_not_fail() -> None:
    from custom_components.fp2_companion.companion import Fp2Companion

    Fp2Companion.terminate(object.__new__(Fp2Companion))


# ------------------------------------------------------- zone selects
def zones(*pairs):
    return [{"rangeId": rid, "name": name, "regionType": 0, "sensitivity": 2,
             "blockList": [1]} for rid, name in pairs]


async def show_zones(hass, entry, zone_list) -> None:
    """The zones a read would have found, without the cloud."""
    app = entry.runtime_data
    device = app.devices[KEY]

    def read():
        device.zones = zone_list
        device.last_read = time.time()
        device._changed()

    await run(hass, entry, read)


async def test_a_zone_gets_a_select_and_loses_it_when_gone(hass, entry) -> None:
    await show_zones(hass, entry, zones((3, "Couch"), (1, "Kitchen Countertop")))
    couch = hass.states.get(f"select.{PREFIX}_couch_sensitivity")
    assert couch.state == "Medium"
    assert couch.name == "Presence-Sensor-FP2-B2C3 Settings Couch sensitivity"
    assert hass.states.get(f"select.{PREFIX}_kitchen_countertop_sensitivity")
    await show_zones(hass, entry, zones((3, "Couch")))
    registry = er.async_get(hass)
    assert registry.async_get(f"select.{PREFIX}_kitchen_countertop_sensitivity") \
        is None
    assert registry.async_get(f"select.{PREFIX}_couch_sensitivity")


async def test_a_zone_deleted_while_home_assistant_was_down_is_removed(
        hass, fp2_device) -> None:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "select", DOMAIN, f"{OBJ}_zone_9_sensitivity",
        suggested_object_id=f"{PREFIX}_gone_sensitivity", config_entry=entry)
    await start(hass, entry)
    await show_zones(hass, entry, zones((3, "Couch")))
    assert registry.async_get(f"select.{PREFIX}_gone_sensitivity") is None
    assert registry.async_get(f"select.{PREFIX}_couch_sensitivity")
    assert await hass.config_entries.async_unload(entry.entry_id)


# ------------------------------------------------------- the move from MQTT
async def test_the_mqtt_entities_move_with_their_ids_and_settings(
        hass, fp2_device) -> None:
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    old_device = devices.async_get_or_create(
        config_entry_id=mqtt_entry.entry_id,
        identifiers={("mqtt", f"fp2_companion_lumi1.{KEY}")},
        name="Presence-Sensor-FP2-B2C3 Settings")
    hub_device = devices.async_get_or_create(
        config_entry_id=mqtt_entry.entry_id,
        identifiers={("mqtt", "fp2_companion")}, name="FP2 Companion")
    registry = er.async_get(hass)
    kitchen = ar.async_get(hass).async_create("Kitchen")
    old = registry.async_get_or_create(
        "select", "mqtt", f"{OBJ}_sensitivity",
        suggested_object_id=f"{PREFIX}_sensitivity",
        config_entry=mqtt_entry, device_id=old_device.id)
    registry.async_update_entity(
        old.entity_id, name="Living sensitivity", area_id=kitchen.id,
        icon="mdi:tune")
    # A person renamed this one; the new id is kept, not the suggested one.
    renamed = registry.async_get_or_create(
        "sensor", "mqtt", f"{OBJ}_zones",
        suggested_object_id=f"{PREFIX}_zones", config_entry=mqtt_entry,
        device_id=old_device.id)
    registry.async_update_entity(renamed.entity_id,
                                 new_entity_id="sensor.living_fp2_zones")
    registry.async_get_or_create(
        "sensor", "mqtt", "fp2_companion_devices",
        suggested_object_id="fp2_companion_devices", config_entry=mqtt_entry,
        device_id=hub_device.id)
    hidden = registry.async_get_or_create(
        "button", "mqtt", f"{OBJ}_ai_learning",
        suggested_object_id=f"{PREFIX}_ai_learning", config_entry=mqtt_entry,
        hidden_by=er.RegistryEntryHider.USER)

    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)

    moved = registry.async_get(f"select.{PREFIX}_sensitivity")
    assert moved.platform == DOMAIN
    assert moved.config_entry_id == entry.entry_id
    assert moved.name == "Living sensitivity"
    assert moved.area_id == kitchen.id
    assert moved.icon == "mdi:tune"
    assert dr.async_get(hass).async_get(moved.device_id).name \
        == "Presence-Sensor-FP2-B2C3 Settings"
    assert registry.async_get("sensor.living_fp2_zones").platform == DOMAIN
    assert registry.async_get(f"sensor.{PREFIX}_zones") is None
    assert registry.async_get(HUB).platform == DOMAIN
    assert registry.async_get(hidden.entity_id).hidden_by \
        is er.RegistryEntryHider.USER
    assert hass.states.get(f"select.{PREFIX}_sensitivity") is not None
    assert not [e for e in registry.entities.values()
                if e.platform == "mqtt"]
    assert devices.async_get(old_device.id) is None
    assert devices.async_get(hub_device.id) is None
    assert await hass.config_entries.async_unload(entry.entry_id)

    # A second setup finds nothing left to move.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(f"select.{PREFIX}_sensitivity").name \
        == "Living sensitivity"
    assert await hass.config_entries.async_unload(entry.entry_id)


# ------------------------------------------------------- the card's commands
async def test_a_card_command_reaches_the_device(hass, entry, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({
        "type": "fp2_companion/backup/preview", "device": KEY,
        "name": "no-such-backup"})
    assert (await client.receive_json())["success"]
    await wait_for_worker(hass, entry)
    assert hass.states.get(f"sensor.{PREFIX}_cloud_status").state == "refused"


async def test_a_zone_is_checked_before_it_reaches_the_device(
        hass, entry, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({
        "type": "fp2_companion/zone/set", "device": KEY,
        "zone": {"name": "Desk", "blockList": [1], "sensitivity": 9}})
    reply = await client.receive_json()
    assert not reply["success"]
    assert reply["error"]["code"] == "invalid_format"


async def test_an_unknown_device_is_an_error(hass, entry, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({
        "type": "fp2_companion/zone/delete", "device": "54ef44d4e5f6",
        "range_id": 1})
    reply = await client.receive_json()
    assert reply["error"]["code"] == "unknown_device"


async def test_a_user_who_is_not_an_admin_is_refused(
        hass, entry, hass_ws_client, hass_read_only_access_token) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    for message in (
            {"type": "fp2_companion/backup/restore", "device": KEY, "name": "x"},
            {"type": "fp2_companion/logout"},
            {"type": "fp2_companion/live/subscribe", "device": KEY}):
        await client.send_json_auto_id(message)
        reply = await client.receive_json()
        assert reply["error"]["code"] == "unauthorized", message


async def test_the_live_view_holds_a_lease_while_it_is_open(
        hass, entry, hass_ws_client) -> None:
    app = entry.runtime_data
    device = app.devices[KEY]
    leases = []
    device.track = lambda client, on: leases.append((client, on))
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "fp2_companion/live/subscribe", "device": KEY})
    reply = await client.receive_json()
    assert reply["success"]
    sub_id = reply["id"]
    await wait_for_worker(hass, entry)
    assert [on for _c, on in leases] == [True]
    await hass.async_add_executor_job(
        app.live, KEY, {"state": "live", "targets": []})
    event = await client.receive_json()
    assert event["id"] == sub_id
    assert event["event"] == {"state": "live", "targets": []}
    # Renewed while it stays open, released when it closes.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await wait_for_worker(hass, entry)
    assert [on for _c, on in leases] == [True, True]
    await client.send_json_auto_id(
        {"type": "unsubscribe_events", "subscription": sub_id})
    assert (await client.receive_json())["success"]
    await wait_for_worker(hass, entry)
    assert [on for _c, on in leases] == [True, True, False]
    assert len({c for c, _on in leases}) == 1


async def test_a_renamed_zone_keeps_its_select_and_takes_the_name(
        hass, entry) -> None:
    await show_zones(hass, entry, zones((3, "Couch")))
    registry = er.async_get(hass)
    kitchen = ar.async_get(hass).async_create("Kitchen")
    registry.async_update_entity(f"select.{PREFIX}_couch_sensitivity",
                                 area_id=kitchen.id)
    await show_zones(hass, entry, zones((3, "Sofa")))
    await hass.async_block_till_done()
    assert registry.async_get(f"select.{PREFIX}_couch_sensitivity").area_id \
        == kitchen.id
    state = hass.states.get(f"select.{PREFIX}_couch_sensitivity")
    assert state.name == "Presence-Sensor-FP2-B2C3 Settings Sofa sensitivity"
    assert hass.states.get(f"select.{PREFIX}_sofa_sensitivity") is None


async def test_an_mqtt_duplicate_does_not_take_over_a_moved_entity(
        hass, fp2_device) -> None:
    """A broker that was never cleared brings the MQTT entity back as _2; the
    next start removes it and the native entity keeps its id and name."""
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    native = registry.async_get_or_create(
        "select", DOMAIN, f"{OBJ}_sensitivity",
        suggested_object_id=f"{PREFIX}_sensitivity", config_entry=entry)
    registry.async_update_entity(native.entity_id, name="Living sensitivity")
    duplicate = registry.async_get_or_create(
        "select", "mqtt", f"{OBJ}_sensitivity",
        suggested_object_id=f"{PREFIX}_sensitivity", config_entry=mqtt_entry)
    assert duplicate.entity_id == f"select.{PREFIX}_sensitivity_2"
    await start(hass, entry)
    assert registry.async_get(f"select.{PREFIX}_sensitivity_2") is None
    kept = registry.async_get(f"select.{PREFIX}_sensitivity")
    assert kept.platform == DOMAIN and kept.name == "Living sensitivity"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_only_what_a_person_disabled_stays_disabled(hass, fp2_device) -> None:
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "select", "mqtt", f"{OBJ}_sensitivity",
        suggested_object_id=f"{PREFIX}_sensitivity", config_entry=mqtt_entry,
        disabled_by=er.RegistryEntryDisabler.CONFIG_ENTRY)
    registry.async_get_or_create(
        "select", "mqtt", f"{OBJ}_approach_distance",
        suggested_object_id=f"{PREFIX}_approach_distance",
        config_entry=mqtt_entry, disabled_by=er.RegistryEntryDisabler.USER)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    assert registry.async_get(f"select.{PREFIX}_sensitivity").disabled_by is None
    assert registry.async_get(f"select.{PREFIX}_approach_distance").disabled_by \
        is er.RegistryEntryDisabler.USER
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.fixture
async def mqtt(hass: HomeAssistant, mqtt_mock):
    """MQTT, unloaded after the test so that its client leaves no timer."""
    yield mqtt_mock
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    for mqtt_entry in hass.config_entries.async_entries("mqtt"):
        await hass.config_entries.async_unload(mqtt_entry.entry_id)
    await hass.async_block_till_done()


async def test_the_broker_is_cleared_and_a_failed_clear_is_retried(
        hass, mqtt, fp2_device, monkeypatch) -> None:
    from homeassistant.components import mqtt as mqtt_component

    from custom_components.fp2_companion import migrate

    monkeypatch.setattr(migrate, "RETAINED_WAIT", 0)
    registry = er.async_get(hass)
    mqtt_entry = hass.config_entries.async_entries("mqtt")[0]
    registry.async_get_or_create(
        "select", "mqtt", f"{OBJ}_sensitivity",
        suggested_object_id=f"{PREFIX}_sensitivity", config_entry=mqtt_entry)
    pending = hass.config.path(".storage", "fp2_companion",
                               "mqtt_cleanup_pending.json")

    # The broker is unreachable on the first start: the cleanup waits.
    async def no_client(_hass):
        return False

    monkeypatch.setattr(mqtt_component, "async_wait_for_mqtt_client", no_client)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await hass.async_add_executor_job(os.path.exists, pending)
    assert await hass.config_entries.async_unload(entry.entry_id)

    # The next start reaches it and clears the old discovery config.
    monkeypatch.undo()
    monkeypatch.setattr(migrate, "RETAINED_WAIT", 0)
    mqtt.async_publish.reset_mock()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    cleared = {c.args[0] for c in mqtt.async_publish.call_args_list
               if c.args[1] == "" and c.args[3] is True}
    assert f"homeassistant/select/{OBJ}_sensitivity/config" in cleared
    assert not await hass.async_add_executor_job(os.path.exists, pending)
    assert await hass.config_entries.async_unload(entry.entry_id)
