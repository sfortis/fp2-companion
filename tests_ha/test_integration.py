"""The integration inside Home Assistant: setup, MQTT, commands, unload.

The cloud is never reached: there is no session, so every call stops at
"no session" before a request is sent.
"""
from datetime import timedelta
import json
import os

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)

import custom_components.fp2_companion as integration
from custom_components.fp2_companion.const import DOMAIN

SERIAL = "54EF44A1B2C3"
OBJ = "fp2_companion_lumi1_54ef44a1b2c3"
PREFIX = "presence_sensor_fp2_b2c3"


def published(mqtt):
    """{topic: payload} of everything published so far, the last one wins."""
    return {c.args[0]: c.args[1] for c in mqtt.async_publish.call_args_list}


async def wait_for_worker(hass: HomeAssistant, entry) -> None:
    """Let the worker finish what is queued, then the loop publish it."""
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


@pytest.fixture
async def mqtt(hass: HomeAssistant, mqtt_mock):
    """MQTT, unloaded after the test so that its client leaves no timer.
    The mocked client's one-second loop ends the first time it runs."""
    yield mqtt_mock
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries("mqtt"):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


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
async def entry(hass: HomeAssistant, mqtt, fp2_device):
    entry = MockConfigEntry(domain=DOMAIN, title="FP2 Companion")
    entry.add_to_hass(hass)
    await start(hass, entry)
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_needs_mqtt(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_required"


async def test_setup_creates_one_entry(hass: HomeAssistant, mqtt) -> None:
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


async def test_the_fp2_is_found_and_published(hass, mqtt, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    topics = published(mqtt)
    cfg = json.loads(topics[f"homeassistant/select/{OBJ}_sensitivity/config"])
    assert cfg["default_entity_id"] == f"select.{PREFIX}_sensitivity"
    assert cfg["device"]["suggested_area"] == "Living Room"
    assert cfg["command_topic"] == "fp2companion/54ef44a1b2c3/cmd/sensitivity"
    hub = json.loads(topics["fp2companion/hub/attrs"])
    assert [d["did"] for d in hub["devices"]] == ["lumi1.54ef44a1b2c3"]
    assert hub["signed_in"] is False
    # No session: the status says so instead of calling the cloud.
    assert topics["fp2companion/54ef44a1b2c3/status/state"] == "no_session"


async def test_a_card_command_reaches_the_app(hass, mqtt, entry) -> None:
    mqtt.async_publish.reset_mock()
    async_fire_mqtt_message(hass, "fp2companion/54ef44a1b2c3/cmd/preview",
                            "no-such-backup")
    await wait_for_worker(hass, entry)
    topics = published(mqtt)
    assert topics["fp2companion/54ef44a1b2c3/status/state"] == "refused"


async def test_options_are_validated_and_reload(hass, mqtt, entry) -> None:
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
    hub = json.loads(published(mqtt)["fp2companion/hub/attrs"])
    assert sorted(d["did"] for d in hub["devices"]) == [
        "lumi1.54ef44a1b2c3", "lumi1.54ef44d4e5f6"]
    assert entry.runtime_data.args["app_identities"] == [["id", "key"]]


async def test_the_appdaemon_state_is_copied_once(
        hass, mqtt, fp2_device, tmp_path, monkeypatch) -> None:
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
    assert (old / "session.json").exists()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_it_waits_while_the_appdaemon_app_is_installed(
        hass, mqtt, fp2_device, tmp_path, monkeypatch) -> None:
    old = tmp_path / "appdaemon_state"
    old.mkdir()
    (old / "session.json").write_text(json.dumps({"phone_id": "kept"}))
    monkeypatch.setattr(integration, "APPDAEMON_STATE_DIR", str(old))
    old_app = hass.config.path("appdaemon", "apps", "fp2_companion")
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
    assert "fp2companion/hub/attrs" not in published(mqtt)

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


async def test_unload_stops_the_worker(hass, mqtt, entry) -> None:
    worker = entry.runtime_data._worker
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert worker._shutdown
