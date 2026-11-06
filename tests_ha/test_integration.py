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
async def entry(hass: HomeAssistant, fp2_device, session):
    entry = MockConfigEntry(domain=DOMAIN, title="Aqara FP2 Companion")
    entry.add_to_hass(hass)
    await start(hass, entry)
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(entry.entry_id)


SIGN_IN = {"account": "a@example.com", "password": "secret", "country": "GR"}


@pytest.fixture
def signed_in(monkeypatch):
    """The config flow's sign-in, without the cloud: it records each call
    and fails with `signed_in.fail` when that is set."""
    from custom_components.fp2_companion import auth, config_flow

    calls = []

    def sign_in(state_dir, account, password, country, identities):
        calls.append((account, password, country, identities))
        if calls_fail[0] is not None:
            raise auth.SignInFailed(calls_fail[0])

    calls_fail = [None]
    monkeypatch.setattr(config_flow, "sign_in", sign_in)

    class Recorder(list):
        def fail(self, reason):
            calls_fail[0] = reason

    recorder = Recorder()
    recorder.calls = calls
    return recorder


async def test_setup_signs_in_and_keeps_no_password(hass, signed_in) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(SIGN_IN, app_identity="id:key"))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert signed_in.calls == [("a@example.com", "secret", "GR", [["id", "key"]])]
    assert result["data"] == {"account": "a@example.com", "country": "GR"}
    assert result["options"] == {"app_identities": ["id:key"]}
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
    assert hub.attributes["signed_in"] is True
    # The tests have no cloud: the status says so, and the settings wait.
    assert hass.states.get(f"sensor.{PREFIX}_cloud_status").state == "cloud_error"
    assert hass.states.get(f"select.{PREFIX}_sensitivity").state == "unavailable"
    assert hass.states.get(f"button.{PREFIX}_refresh").state != "unavailable"


async def test_a_command_reaches_the_app(hass, entry) -> None:
    device = entry.runtime_data.devices[KEY]
    await run(hass, entry, device.restore, "no-such-backup")
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
    (old / "session.json").write_text(json.dumps({"phone_id": "kept", "token": "t", "user_id": "u"}))
    (old / "backups" / "lumi1.54ef44a1b2c3" / "zones.json").write_text("[]")
    monkeypatch.setattr(integration, "APPDAEMON_STATE_DIR", str(old))
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    state = hass.config.path(".storage", "fp2_companion")
    with open(os.path.join(state, "session.json")) as fh:
        assert json.load(fh) == {"phone_id": "kept", "token": "t", "user_id": "u"}
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
    (old / "session.json").write_text(json.dumps({"phone_id": "kept", "token": "t", "user_id": "u"}))
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
    (old / "session.json").write_text(json.dumps({"phone_id": "old", "token": "o", "user_id": "u"}))
    (old / "backups" / "lumi1.54ef44a1b2c3" / "zones.json").write_text("[]")
    monkeypatch.setattr(integration, "APPDAEMON_STATE_DIR", str(old))
    state = hass.config.path(".storage", "fp2_companion")

    def make_new():
        os.makedirs(state)
        with open(os.path.join(state, "session.json"), "w") as fh:
            json.dump({"phone_id": "new", "token": "n", "user_id": "u"}, fh)

    await hass.async_add_executor_job(make_new)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    with open(os.path.join(state, "session.json")) as fh:
        assert json.load(fh) == {"phone_id": "new", "token": "n", "user_id": "u"}
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
        hass, fp2_device, session, monkeypatch) -> None:
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
        hass, fp2_device, session) -> None:
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
        hass, fp2_device, session) -> None:
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
        hass, fp2_device, session) -> None:
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
        device.values = {"14.1.85": "2", "14.47.85": "0", "4.72.85": "0"}
        device.zones = zone_list
        device.last_read = time.time()
        device._set_status("ok")

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
        hass, fp2_device, session) -> None:
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
        hass, fp2_device, session) -> None:
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
        "type": "fp2_companion/backup/restore", "device": KEY,
        "name": "no-such-backup"})
    assert (await client.receive_json())["success"]
    await wait_for_worker(hass, entry)
    assert hass.states.get(f"sensor.{PREFIX}_cloud_status").state == "refused"


async def test_a_preview_answers_with_the_backup(hass, entry, hass_ws_client) -> None:
    folder = entry.runtime_data.devices[KEY].info["backup_dir"]
    name = "zones-20261030T090122.json"

    def write():
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "w") as fh:
            json.dump({"saved_at": 1793343682, "reason": "first-read",
                       "zones": [{"rangeId": 3, "name": "Couch"}]}, fh)

    await hass.async_add_executor_job(write)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({
        "type": "fp2_companion/backup/preview", "device": KEY, "name": name})
    reply = await client.receive_json()
    assert reply["success"]
    assert reply["result"] == {"name": name, "saved_at": 1793343682,
                               "reason": "first-read",
                               "zones": [{"rangeId": 3, "name": "Couch"}]}
    # A name that is not a backup reads nothing outside the folder.
    await client.send_json_auto_id({
        "type": "fp2_companion/backup/preview", "device": KEY,
        "name": "../session.json"})
    reply = await client.receive_json()
    assert reply["error"]["code"] == "no_backup"


async def test_the_retired_preview_sensor_is_removed(hass, fp2_device, session) -> None:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{OBJ}_backup_preview",
        suggested_object_id=f"{PREFIX}_backup_preview", config_entry=entry)
    await start(hass, entry)
    assert registry.async_get(f"sensor.{PREFIX}_backup_preview") is None
    assert await hass.config_entries.async_unload(entry.entry_id)


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
            {"type": "fp2_companion/zone/delete", "device": KEY, "range_id": 1},
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
        hass, fp2_device, session) -> None:
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


async def test_only_what_a_person_disabled_stays_disabled(hass, fp2_device, session) -> None:
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
        hass, mqtt, fp2_device, session, monkeypatch) -> None:
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


@pytest.mark.parametrize("reason", ["invalid_auth", "cannot_connect",
                                    "invalid_identity_region"])
async def test_a_failed_sign_in_shows_why_and_recovers(
        hass, signed_in, reason) -> None:
    from custom_components.fp2_companion.auth import SignInError

    signed_in.fail(SignInError(reason))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], SIGN_IN)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}
    # The form comes back with what was typed, but not the password.
    fields = {k: k.description for k in result["data_schema"].schema}
    assert fields["account"] == {"suggested_value": "a@example.com"}
    assert not (fields["password"] or {}).get("suggested_value")
    signed_in.fail(None)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], SIGN_IN)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_malformed_identity_is_refused_before_signing_in(
        hass, signed_in) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(SIGN_IN, app_identity="nokey"))
    assert result["errors"] == {"app_identity": "invalid_identity"}
    assert signed_in.calls == []


async def test_no_session_asks_for_a_sign_in_before_starting(
        hass, fp2_device) -> None:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_a_session_lost_while_running_asks_for_a_sign_in(
        hass, entry) -> None:
    from custom_components.fp2_companion.aqara_cloud import AqaraAuthError

    app = entry.runtime_data
    device = app.devices[KEY]
    await run(hass, entry, device._cloud_failed,
              AqaraAuthError("res/write", 108, "expired"))
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_reauth_signs_in_again_and_reloads(hass, entry, signed_in) -> None:
    flow = await entry.start_reauth_flow(hass)
    assert flow["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], SIGN_IN)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {"account": "a@example.com", "country": "GR"}
    await hass.async_block_till_done()


async def test_reconfigure_changes_the_account(hass, entry, signed_in) -> None:
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(SIGN_IN, account="b@example.com", country="US"))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"account": "b@example.com", "country": "US"}
    await hass.async_block_till_done()



async def test_an_unknown_refusal_shows_aqaras_code(hass, signed_in) -> None:
    from custom_components.fp2_companion import auth, config_flow

    def refuse(*_args):
        raise auth.SignInFailed(auth.SignInError.REFUSED, 1001, "account locked")

    config_flow.sign_in = refuse
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], SIGN_IN)
    assert result["errors"] == {"base": "refused"}
    assert result["description_placeholders"] == {
        "code": "1001", "message": "account locked"}


async def test_reauth_with_the_same_account_reloads_once(
        hass, fp2_device, session, signed_in, caplog) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={"account": "a@example.com", "country": "GR"})
    entry.add_to_hass(hass)
    await start(hass, entry)
    flow = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], SIGN_IN)
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert "should use it for scheduling a reload" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_removing_the_integration_signs_out(hass, fp2_device, session) -> None:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    state = hass.config.path(".storage", "fp2_companion")

    def write():
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "session.json"), "w") as fh:
            json.dump({"token": "t", "user_id": "u", "phone_id": "P"}, fh)

    await hass.async_add_executor_job(write)
    await start(hass, entry)
    await hass.config_entries.async_remove(entry.entry_id)

    def read():
        with open(os.path.join(state, "session.json")) as fh:
            return json.load(fh)

    kept = await hass.async_add_executor_job(read)
    assert "token" not in kept and kept["phone_id"] == "P"


# ------------------------------------------------------- Gold rules
async def test_diagnostics_leave_out_the_account_and_the_serials(
        hass, entry) -> None:
    from custom_components.fp2_companion.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    hass.config_entries.async_update_entry(
        entry, data={"account": "a@example.com", "country": "GR"},
        options={"app_identities": ["id:key"]})
    # The update reloads the entry, and the app looks for the FP2s 5 s on.
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await wait_for_worker(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag)
    for secret in ("a@example.com", "id:key", "54ef44a1b2c3", "j.walker"):
        assert secret not in text.lower(), secret
    assert diag["entry"]["data"]["country"] == "GR"
    assert diag["session"]["signed_in"] is True
    assert diag["devices"][0]["status"] == "cloud_error"


async def test_an_fp2_paired_after_setup_is_served_without_a_reload(
        hass, entry, fp2_device) -> None:
    homekit_entry = hass.config_entries.async_entries("homekit_controller")[0]
    dr.async_get(hass).async_get_or_create(
        config_entry_id=homekit_entry.entry_id,
        identifiers={("homekit_controller", "2")},
        manufacturer="Aqara", model="PS-S02D", serial_number="54EF44D4E5F6",
        name="Presence-Sensor-FP2-E5F6")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await wait_for_worker(hass, entry)
    assert "54ef44d4e5f6" in entry.runtime_data.devices
    assert hass.states.get("select.presence_sensor_fp2_e5f6_sensitivity") is not None


async def test_only_a_device_no_longer_served_can_be_removed(
        hass, entry) -> None:
    devices = dr.async_get(hass)
    served = devices.async_get_device_by_identifier(
        (DOMAIN, "lumi1.54ef44a1b2c3"), entry.entry_id)
    hub = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id),
                                                 entry.entry_id)
    gone = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "lumi1.54ef44d4e5f6")}, name="Old FP2 Settings")
    assert not await integration.async_remove_config_entry_device(hass, entry, served)
    assert not await integration.async_remove_config_entry_device(hass, entry, hub)
    assert await integration.async_remove_config_entry_device(hass, entry, gone)


# ------------------------------------------------------- entity commands
async def test_the_buttons_and_the_switch_send_their_commands(hass, entry) -> None:
    await show_zones(hass, entry, zones((3, "Couch")))
    device = entry.runtime_data.devices[KEY]
    calls = []
    device.refresh = lambda: calls.append("refresh")
    device.start_ai_learning = lambda: calls.append("ai_learning")
    device.set_ai_detection = lambda on: calls.append(("ai_detection", on))
    for domain, service, entity_id in (
            ("button", "press", f"button.{PREFIX}_refresh"),
            ("button", "press", f"button.{PREFIX}_ai_learning"),
            ("switch", "turn_on", f"switch.{PREFIX}_ai_detection"),
            ("switch", "turn_off", f"switch.{PREFIX}_ai_detection")):
        await hass.services.async_call(domain, service, {"entity_id": entity_id},
                                       blocking=True)
    await wait_for_worker(hass, entry)
    assert calls == ["refresh", "ai_learning", ("ai_detection", True),
                     ("ai_detection", False)]


# ------------------------------------------------------- the card's commands
async def test_a_zone_write_and_a_delete_are_queued_for_the_device(
        hass, entry, hass_ws_client) -> None:
    device = entry.runtime_data.devices[KEY]
    calls = []
    device.zone_set = lambda zone: calls.append(("set", zone))
    device.zone_delete = lambda range_id: calls.append(("delete", range_id))
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({
        "type": "fp2_companion/zone/set", "device": KEY,
        "zone": {"rangeId": 3, "name": "Sofa", "tagId": None}})
    assert (await client.receive_json())["success"]
    await client.send_json_auto_id({
        "type": "fp2_companion/zone/delete", "device": KEY, "range_id": 3})
    assert (await client.receive_json())["success"]
    await wait_for_worker(hass, entry)
    assert calls == [("set", {"rangeId": 3, "name": "Sofa", "tagId": None}),
                     ("delete", 3)]


async def test_a_preview_and_a_live_view_need_a_served_device(
        hass, entry, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    for message in (
            {"type": "fp2_companion/backup/preview", "device": "54ef44d4e5f6",
             "name": "zones-20261030T090122.json"},
            {"type": "fp2_companion/live/subscribe", "device": "54ef44d4e5f6"}):
        await client.send_json_auto_id(message)
        reply = await client.receive_json()
        assert reply["error"]["code"] == "unknown_device", message


async def test_a_command_while_the_integration_is_unloaded_is_refused(
        hass, entry, hass_ws_client) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({
        "type": "fp2_companion/backup/restore", "device": KEY, "name": "x"})
    reply = await client.receive_json()
    assert reply["error"]["code"] == "not_loaded"


# ------------------------------------------------------- the runtime
async def test_registry_changes_that_bring_no_new_fp2_start_no_rescan(
        hass, entry, fp2_device) -> None:
    app = entry.runtime_data
    rescans = []
    app.rescan = lambda: rescans.append(True)
    devices = dr.async_get(hass)
    homekit_entry = hass.config_entries.async_entries("homekit_controller")[0]
    other = MockConfigEntry(domain="zha")
    other.add_to_hass(hass)
    # An FP2 that another integration added, an FP2 whose serial is not one,
    # a rename of the FP2 already served, and a device that was removed.
    devices.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("zha", "1")},
        manufacturer="Aqara", model="PS-S02D", serial_number="54EF44D4E5F6",
        name="Presence-Sensor-FP2-E5F6")
    bad = devices.async_get_or_create(
        config_entry_id=homekit_entry.entry_id,
        identifiers={("homekit_controller", "3")}, manufacturer="Aqara",
        model="PS-S02D", serial_number="not-a-serial", name="Odd FP2")
    devices.async_update_device(fp2_device.id, name_by_user="Living FP2")
    devices.async_remove_device(bad.id)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await wait_for_worker(hass, entry)
    assert rescans == []


async def test_a_device_entry_with_only_config_entries_is_matched(
        hass, entry) -> None:
    """A device entry without `config_entry_id` is matched by the set of
    config entries it belongs to."""
    from types import SimpleNamespace

    app = entry.runtime_data
    homekit_entry = hass.config_entries.async_entries("homekit_controller")[0]
    assert app._is_homekit(
        SimpleNamespace(config_entries={homekit_entry.entry_id, "other"}))
    assert not app._is_homekit(SimpleNamespace(config_entries={"other"}))


async def test_an_error_in_a_command_is_logged_and_the_next_one_runs(
        hass, entry, caplog) -> None:
    def broken():
        raise RuntimeError("broken command")

    ran = []
    entry.runtime_data.submit(broken)
    await run(hass, entry, lambda: ran.append(True))
    assert "Error in" in caplog.text and "broken command" in caplog.text
    assert ran == [True]


async def test_a_daily_timer_fires_until_it_is_cancelled(
        hass, entry, caplog) -> None:
    app = entry.runtime_data
    fired = []
    target = (dt_util.now() + timedelta(hours=1)).replace(microsecond=0)
    handle = app.run_daily(fired.append, target.strftime("%H:%M:%S"))
    await hass.async_block_till_done()
    async_fire_time_changed(hass, target)
    await wait_for_worker(hass, entry)
    assert fired == [{}]
    app.cancel_timer(handle)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, target + timedelta(days=1))
    await wait_for_worker(hass, entry)
    assert fired == [{}]
    # A timer that is gone is reported, unless the caller expects it gone.
    app.cancel_timer(handle, silent=True)
    await hass.async_block_till_done()
    assert "No timer" not in caplog.text
    app.cancel_timer(handle)
    await hass.async_block_till_done()
    assert f"No timer {handle} to cancel" in caplog.text


async def test_after_the_stop_no_timer_is_armed_and_no_sign_in_is_asked(
        hass, entry) -> None:
    app = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    app.run_in(lambda kwargs: None, 1)
    app.run_daily(lambda kwargs: None, "05:00")
    app.signed_out()
    await hass.async_block_till_done()
    assert app._timers == {}
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_other_homekit_devices_are_not_served(
        hass, fp2_device, session) -> None:
    homekit_entry = hass.config_entries.async_entries("homekit_controller")[0]
    dr.async_get(hass).async_get_or_create(
        config_entry_id=homekit_entry.entry_id,
        identifiers={("homekit_controller", "9")}, manufacturer="Signify",
        model="LCA001", name="Hue bulb")
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    assert [d["did"] for d in hass.states.get(HUB).attributes["devices"]] \
        == ["lumi1.54ef44a1b2c3"]
    assert await hass.config_entries.async_unload(entry.entry_id)


# ------------------------------------------------------- the move from MQTT
async def test_a_retired_mqtt_entity_is_removed_and_not_moved(
        hass, fp2_device, session) -> None:
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", "mqtt", f"{OBJ}_backup_preview",
        suggested_object_id=f"{PREFIX}_backup_preview", config_entry=mqtt_entry)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    assert registry.async_get(f"sensor.{PREFIX}_backup_preview") is None
    assert registry.async_get_entity_id(
        "sensor", DOMAIN, f"{OBJ}_backup_preview") is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_display_options_of_an_mqtt_entity_move_with_it(
        hass, fp2_device, session) -> None:
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", "mqtt", f"{OBJ}_zones", suggested_object_id=f"{PREFIX}_zones",
        config_entry=mqtt_entry)
    registry.async_update_entity_options(
        old.entity_id, "sensor", {"display_precision": 0})
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    moved = registry.async_get(f"sensor.{PREFIX}_zones")
    assert moved.platform == DOMAIN
    assert moved.options["sensor"]["display_precision"] == 0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_mqtt_entity_whose_state_stays_moves_to_a_free_id(
        hass, fp2_device, session, monkeypatch, caplog) -> None:
    from custom_components.fp2_companion import migrate

    monkeypatch.setattr(migrate, "STATE_GONE_TIMEOUT", 0.1)
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "select", "mqtt", f"{OBJ}_sensitivity",
        suggested_object_id=f"{PREFIX}_sensitivity", config_entry=mqtt_entry)
    hass.states.async_set(f"select.{PREFIX}_sensitivity", "Low")
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    assert "Old FP2 Companion entities are still loaded" in caplog.text
    assert f"select.{PREFIX}_sensitivity is taken" in caplog.text
    assert registry.async_get_entity_id(
        "select", DOMAIN, f"{OBJ}_sensitivity") \
        == f"select.{PREFIX}_sensitivity_2"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_moved_entity_takes_the_mqtt_id_over_a_removed_native_one(
        hass, fp2_device, session) -> None:
    """The registry remembers a native entity removed earlier and offers its
    old id again; the moved entity keeps the id of the MQTT entity."""
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    removed = registry.async_get_or_create(
        "select", DOMAIN, f"{OBJ}_sensitivity",
        suggested_object_id="living_sensitivity", config_entry=entry)
    registry.async_remove(removed.entity_id)
    registry.async_get_or_create(
        "select", "mqtt", f"{OBJ}_sensitivity",
        suggested_object_id=f"{PREFIX}_sensitivity", config_entry=mqtt_entry)
    await start(hass, entry)
    assert registry.async_get_entity_id(
        "select", DOMAIN, f"{OBJ}_sensitivity") == f"select.{PREFIX}_sensitivity"
    assert registry.async_get("select.living_sensitivity") is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_pending_cleanup_that_does_not_parse_is_dropped_without_mqtt(
        hass, fp2_device, session) -> None:
    pending = hass.config.path(".storage", "fp2_companion",
                               "mqtt_cleanup_pending.json")

    def write():
        with open(pending, "w") as fh:
            fh.write("{not json")

    await hass.async_add_executor_job(write)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    await start(hass, entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not await hass.async_add_executor_job(os.path.exists, pending)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_retained_topics_of_the_old_versions_are_cleared(
        hass, mqtt, fp2_device, session, monkeypatch) -> None:
    """The broker sends its retained messages on subscribe; here the
    subscription hands them to the callback at once."""
    from homeassistant.components import mqtt as mqtt_component
    from homeassistant.components.mqtt.models import ReceiveMessage

    from custom_components.fp2_companion import migrate

    monkeypatch.setattr(migrate, "RETAINED_WAIT", 0)
    legacy = f"fp2companion/lumi1.{KEY}"
    retained = [
        (f"{legacy}/state", "{}", True),
        (f"homeassistant/select/{OBJ}_approach_distance/config", "{}", True),
        ("homeassistant/select/other_device/config", "{}", True),
        (f"{legacy}/zones", "", True),
        (f"{legacy}/live", "{}", False),
    ]
    real_subscribe = mqtt_component.async_subscribe

    async def subscribe(hass, topic, msg_callback, *args, **kwargs):
        unsubscribe = await real_subscribe(hass, topic, msg_callback,
                                           *args, **kwargs)
        for name, payload, retain in retained:
            if name.split("/")[0] == topic.split("/")[0]:
                msg_callback(ReceiveMessage(name, payload, 0, retain, topic, 0.0))
        return unsubscribe

    monkeypatch.setattr(mqtt_component, "async_subscribe", subscribe)
    registry = er.async_get(hass)
    mqtt_entry = hass.config_entries.async_entries("mqtt")[0]
    registry.async_get_or_create(
        "select", "mqtt", f"{OBJ}_sensitivity",
        suggested_object_id=f"{PREFIX}_sensitivity", config_entry=mqtt_entry)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    mqtt.async_publish.reset_mock()
    await start(hass, entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    cleared = {c.args[0] for c in mqtt.async_publish.call_args_list
               if c.args[1] == "" and c.args[3] is True}
    assert cleared == {
        f"homeassistant/select/{OBJ}_sensitivity/config",
        f"homeassistant/select/{OBJ}_approach_distance/config",
        f"{legacy}/state"}
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_removing_a_pending_cleanup_that_is_gone_is_not_an_error(tmp_path) -> None:
    from custom_components.fp2_companion import migrate

    migrate._remove_pending(str(tmp_path / "mqtt_cleanup_pending.json"))
