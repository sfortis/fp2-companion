"""The fp2_companion app: discovery, entities, writes, readback and backups.

Driven through the fake World and a fake cloud, so nothing here touches the
network. The fake World never fires timers, so a test calls a scheduled
callback itself.
"""
import json
import os

import pytest

from conftest import World, _bind, _load, mqtt_payload

from aqara_cloud import AqaraAuthError, AqaraError, AqaraRefused

DID = "lumi1.54ef44a1b2c3"
KEY = "54ef44a1b2c3"
PREFIX = "presence_sensor_fp2_b2c3"
OBJ = "fp2_companion_lumi1_54ef44a1b2c3"
T = f"fp2companion/{KEY}"

# What the discovery template returned live on 2026-10-30. The Office FP2
# was renamed in Home Assistant; its HomeKit entity ids kept the original name.
LIVING = {"serial": "54EF44A1B2C3", "original": "Presence-Sensor-FP2-B2C3",
          "name": "Presence-Sensor-FP2-B2C3", "area": "Living Room"}
OFFICE = {"serial": "54EF44D4E5F6", "original": "Presence-Sensor-FP2-E5F6",
           "name": "Motion Sensor - Office", "area": "Office"}
OFFICE_DID = "lumi1.54ef44d4e5f6"


def zone(range_id, name, region, sens, blocks):
    return {"deviceId": DID, "blockList": blocks, "colorId": 0, "name": name,
            "rangeId": range_id, "regionType": region, "sensitivity": sens,
            "tagId": "custom_", "tagName": ""}


ZONES = [zone(3, "Couch", 0, 2, [43, 44, 45]),
         zone(1, "Kitchen Countertop", 0, 3, [139, 140]),
         zone(101, "Interference source", 1, 1, [140, 273])]


# Two items of the zone type catalogue as the cloud returned it on 2026-10-31.
TAG_LIST = [
    {"extend": '{"sensitivity":"3"}', "tagId": "3.6", "tagName": "Bed",
     "type": 9, "parentType": "3"},
    {"extend": '{"sensitivity":"1"}', "tagId": "9.2", "tagName": "Corridor",
     "type": 9, "parentType": "9"},
]


class FakeCloud:
    def __init__(self):
        self.values = {"14.1.85": "1", "14.47.85": "0", "4.72.85": "0"}
        self.zones = json.loads(json.dumps(ZONES))
        self.tags = json.loads(json.dumps(TAG_LIST))
        self.tag_reads = []
        self.tag_fail = None
        self.writes = []
        self.zone_writes = []
        self.deletes = []
        self.logins = []
        self.login_error = None
        self.account_name = "j.walker@example.com"
        self.has_token = True
        self.lands = True          # False: acknowledged but ignored (AI mode)
        self.fail = None
        self.refreshes = 0
        self.due = False
        self.country = "GR"
        self.store = self

    def load(self):
        """The session file as the app reads it for the shared status."""
        if not self.has_token:
            return {}
        return {"country": self.country, "token": "not-a-jwt",
                "account": self.account_name}

    def query_rids(self, rids):
        if self.fail:
            raise self.fail
        return {r: self.values[r] for r in rids}

    def list_zones(self):
        if self.fail:
            raise self.fail
        return json.loads(json.dumps(self.zones))

    def list_zone_tags(self, tag_type):
        self.tag_reads.append(tag_type)
        if self.tag_fail:
            raise self.tag_fail
        return json.loads(json.dumps(self.tags))

    def write_rid(self, rid, value):
        self.writes.append((rid, value))
        if self.lands:
            self.values[rid] = value

    def set_zone(self, zone):
        """Like the server in the 2026-10-30 capture: a zone without a
        rangeId is created under the next free id and an empty tag becomes
        custom_Other. An edit keeps the fields it does not send, as the
        server kept the edge's sensitivity and tag on 2026-10-31."""
        self.zone_writes.append(json.loads(json.dumps(zone)))
        if not self.lands:
            return
        z = json.loads(json.dumps(zone))
        if "rangeId" not in z:
            z["rangeId"] = max([r["rangeId"] for r in self.zones
                                if r["rangeId"] < 100] + [0]) + 1
            z["tagId"] = z["tagId"] or "custom_Other"
            self.zones.append(z)
        else:
            self.zones = [{**r, **z} if r["rangeId"] == z["rangeId"] else r
                          for r in self.zones]

    def delete_zones(self, ids):
        if getattr(self, "refuse_delete", False):
            raise AqaraRefused("not a list of zone ids")
        self.deletes.append(list(ids))
        if self.lands:
            self.zones = [z for z in self.zones if z["rangeId"] not in ids]

    def ensure_fresh(self):
        if self.due:
            self.refreshes += 1
            return True
        return False

    def token_expires_at(self):
        return 1795940000.0

    def login(self, account, password=None, encrypted_password=None,
              country=None):
        self.logins.append((account, encrypted_password))
        self.login_country = country
        if self.login_error:
            raise self.login_error
        self.account_name = account
        self.has_token = True

    def logout(self):
        self.account_name = None
        self.has_token = False

    def account(self):
        return self.account_name if self.has_token else None

    def signed_in(self):
        return self.has_token


@pytest.fixture
def fp2(tmp_path):
    mod = _load("fp2_companion", "fp2_companion/fp2_companion.py")
    mod.SESSION_FILE = str(tmp_path / "session.json")
    mod.BACKUP_ROOT = str(tmp_path / "backups")
    mod.RUNTIME_FILE = str(tmp_path / "runtime.json")

    def make(devices=None, world=None, cloud=None, clouds=None, args=None):
        """`devices` is what the discovery template returns. The session and
        the Living Room FP2 share `cloud`, as they share one session file;
        another device gets its own fake from `clouds`, keyed by did."""
        world = world or World()
        app = mod.Fp2Companion()
        _bind(app, world)
        found = [LIVING] if devices is None else devices
        app.templates = []

        def render(template):
            app.templates.append(template)
            if isinstance(found, Exception):
                raise found
            return found
        app.render_template = render
        app.args = dict({"app_id": "id", "app_key": "key"}, **(args or {}))
        app.cloud_fake = cloud or FakeCloud()
        app.clouds = clouds or {}
        app.dids = []

        def make_cloud(did):
            app.dids.append(did)
            if did in ("", DID):
                return app.cloud_fake
            return app.clouds.setdefault(did, FakeCloud())
        app._make_cloud = make_cloud

        # The fake World drops a timer's keyword arguments, and a zone
        # readback carries the zone and the list from before the write in
        # them, so the app's run_in is recorded here with its kwargs.
        def run_in(callback, delay=None, **kwargs):
            world.timers = getattr(world, "timers", [])
            world.timers.append({"callback": callback, "delay": delay,
                                 "kwargs": kwargs})
            return f"timer-{len(world.timers)}"
        app.run_in = run_in
        app.initialize()
        app._now = lambda: 1793350000.0
        return app, world

    make.mod = mod
    return make


def cmd(app, key, payload="", device=KEY):
    """A device command, or an app command (login, logout, rescan)."""
    topic = (f"fp2companion/cmd/{key}" if key in ("login", "logout", "rescan")
             else f"fp2companion/{device}/cmd/{key}")
    app._on_cmd("MQTT_MESSAGE", {"topic": topic, "payload": payload}, None)


def discovery(world, component, key):
    raw = mqtt_payload(world, f"homeassistant/{component}/{OBJ}_{key}/config")
    return json.loads(raw) if raw else raw


def status(world):
    return mqtt_payload(world, "fp2companion/54ef44a1b2c3/status/state")


def zone_attrs(world):
    return json.loads(mqtt_payload(world, "fp2companion/54ef44a1b2c3/zones/attrs"))


# ----------------------------------------------------------------- startup
def test_the_command_listener_is_in_the_mqtt_namespace(fp2):
    app, world = fp2()
    assert world.listeners[0]["namespace"] == "mqtt"


def test_the_device_is_found_in_home_assistant(fp2):
    app, world = fp2()
    app.start()
    assert DID in app.dids
    assert "PS-S02D" in app.templates[0]
    assert "PS-T02E" in app.templates[0]
    cfg = discovery(world, "select", "sensitivity")
    assert cfg["default_entity_id"] == f"select.{PREFIX}_sensitivity"
    assert cfg["device"]["suggested_area"] == "Living Room"
    assert cfg["device"]["name"] == "Presence-Sensor-FP2-B2C3 Settings"
    assert cfg["command_topic"] == f"{T}/cmd/sensitivity"


def test_every_entity_carries_the_homekit_prefix(fp2):
    app, world = fp2()
    app.start()
    ids = {discovery(world, c, k)["default_entity_id"] for c, k in (
        ("select", "approach_distance"), ("button", "refresh"),
        ("sensor", "zones"), ("sensor", "cloud_status"))}
    assert ids == {f"select.{PREFIX}_approach_distance",
                   f"button.{PREFIX}_refresh", f"sensor.{PREFIX}_zones",
                   f"sensor.{PREFIX}_cloud_status"}


def test_no_fp2_found_publishes_no_device_and_retries(fp2):
    app, world = fp2(devices=[{"serial": None, "name": None, "area": None}])
    app.start()
    assert app.devices == {}
    topics = [c[2]["topic"] for c in world.calls if c[0] == "mqtt/publish"]
    assert not [t for t in topics if OBJ in t]
    assert world.timers[-1]["callback"] == app.start


def test_startup_reads_and_publishes_the_values(fp2):
    app, world = fp2()
    app.start()
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/state/sensitivity") == "Low"
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/state/approach_distance") == "Far"
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/state/zone/3/sensitivity") == "Medium"
    assert status(world) == "ok"
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/zones/state") == "3"


def test_only_detection_zones_get_a_select(fp2):
    app, world = fp2()
    app.start()
    couch = discovery(world, "select", "zone_3_sensitivity")
    assert couch["default_entity_id"] == f"select.{PREFIX}_couch_sensitivity"
    assert couch["name"] == "Couch Sensitivity"
    kitchen = discovery(world, "select", "zone_1_sensitivity")
    assert kitchen["default_entity_id"] == \
        f"select.{PREFIX}_kitchen_countertop_sensitivity"
    assert discovery(world, "select", "zone_101_sensitivity") is None


def test_startup_checks_the_token(fp2):
    cloud = FakeCloud()
    cloud.due = True
    app, world = fp2(cloud=cloud)
    app.start()
    assert cloud.refreshes == 1


def test_the_card_gets_the_zones_and_the_grid(fp2):
    app, world = fp2()
    app.start()
    attrs = zone_attrs(world)
    assert attrs["grid"] == {"columns": 16, "rows": 20}
    assert attrs["sensor"] == {"col": 8, "row": 0}
    assert [z["rangeId"] for z in attrs["zones"]] == [3, 1, 101]
    assert attrs["backups"][0]["reason"] == "first-read"


# ------------------------------------------------------------------- writes
def test_a_setting_is_written_and_read_back(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "sensitivity", "Medium")
    assert app.cloud_fake.writes == [("14.1.85", "2")]
    timer = world.timers[-1]
    assert timer["callback"] == app.devices[KEY]._verify_write
    app.devices[KEY]._verify_write({"rid": "14.1.85", "value": "2"})
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/state/sensitivity") == "Medium"
    assert status(world) == "ok"


def test_a_write_the_device_ignored_is_reported_not_retried(fp2):
    # With the AI high-precision mode on, the cloud answers code 0 and the
    # device keeps its value. Retrying would change nothing.
    app, world = fp2()
    app.start()
    app.cloud_fake.lands = False
    cmd(app, "approach_distance", "Close")
    app.devices[KEY]._verify_write({"rid": "14.47.85", "value": "2"})
    assert status(world) == "write_not_applied"
    detail = json.loads(mqtt_payload(world, "fp2companion/54ef44a1b2c3/status/attrs"))["detail"]
    assert detail == {"rid": "14.47.85", "wrote": "2", "reads": "0"}
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/state/approach_distance") == "Far"
    assert len(app.cloud_fake.writes) == 1


def test_an_unknown_option_is_refused_and_nothing_is_written(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "sensitivity", "Maximum")
    assert app.cloud_fake.writes == []
    assert status(world) == "refused"


def test_another_apps_command_is_ignored(fp2):
    app, world = fp2()
    app.start()
    app._on_cmd("MQTT_MESSAGE", {"topic": "lgoled/cmd/sensitivity",
                                 "payload": "High"}, None)
    assert app.cloud_fake.writes == []


def test_refresh_reads_again(fp2):
    app, world = fp2()
    app.start()
    app.cloud_fake.values["14.1.85"] = "3"
    cmd(app, "refresh", "PRESS")
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/state/sensitivity") == "High"


# ------------------------------------------------------------------- errors
def test_a_lost_session_says_so(fp2):
    cloud = FakeCloud()
    cloud.fail = AqaraAuthError("res/query/by/resourceId", 108, "expired")
    app, world = fp2(cloud=cloud)
    app.start()
    assert status(world) == "no_session"


def test_a_failed_call_keeps_its_code_and_endpoint(fp2):
    cloud = FakeCloud()
    cloud.fail = AqaraError("devex/radar/range/list", 302, "Params error")
    app, world = fp2(cloud=cloud)
    app.start()
    assert status(world) == "cloud_error"
    attrs = json.loads(mqtt_payload(world, "fp2companion/54ef44a1b2c3/status/attrs"))
    assert attrs["last_error"]["code"] == 302
    assert attrs["last_error"]["endpoint"] == "devex/radar/range/list"


# ------------------------------------------------------------------ backups
def test_an_unchanged_read_writes_no_new_backup(fp2, tmp_path):
    app, world = fp2()
    app.start()
    cmd(app, "refresh")
    assert len(os.listdir(tmp_path / "backups" / DID)) == 1


def test_a_changed_zone_is_backed_up(fp2, tmp_path):
    app, world = fp2()
    app.start()
    app.cloud_fake.zones[0]["blockList"].append(46)
    app._now = lambda: 1793350060.0
    cmd(app, "refresh")
    rows = zone_attrs(world)["backups"]
    assert [r["reason"] for r in rows] == ["changed", "first-read"]


def test_block_order_alone_is_not_a_change(fp2, tmp_path):
    app, world = fp2()
    app.start()
    app.cloud_fake.zones[0]["blockList"].reverse()
    cmd(app, "refresh")
    assert len(os.listdir(tmp_path / "backups" / DID)) == 1


# --------------------------------------------------------------- zone list
def test_a_zone_that_went_away_loses_its_select(fp2):
    app, world = fp2()
    app.start()
    app.cloud_fake.zones = [z for z in app.cloud_fake.zones
                            if z["rangeId"] != 1]
    cmd(app, "refresh")
    assert mqtt_payload(
        world, f"homeassistant/select/{OBJ}_zone_1_sensitivity/config") == ""


def test_a_zone_deleted_while_the_app_was_down_is_retired(fp2):
    app, world = fp2()
    app.start()
    cloud = FakeCloud()
    cloud.zones = [z for z in cloud.zones if z["rangeId"] != 3]
    app2, world2 = fp2(cloud=cloud)
    app2.start()
    assert mqtt_payload(
        world2, f"homeassistant/select/{OBJ}_zone_3_sensitivity/config") == ""


# -------------------------------------------------------------------- zones
def fire(world):
    """Run the readback the last write scheduled."""
    timer = world.timers[-1]
    timer["callback"](timer["kwargs"])


def backups(tmp_path):
    folder = tmp_path / "backups" / DID
    return sorted(json.loads(p.read_text())["reason"]
                  for p in folder.glob("zones-*.json"))


def test_a_zone_select_rewrites_only_that_zones_sensitivity(fp2, tmp_path):
    app, world = fp2()
    app.start()
    cmd(app, "zone/3/sensitivity", "High")
    sent = app.cloud_fake.zone_writes[-1]
    assert sent == dict(ZONES[0], sensitivity=3)
    assert "before-write" in backups(tmp_path)
    fire(world)
    assert status(world) == "ok"
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/state/zone/3/sensitivity") == "High"


def test_an_edit_keeps_every_field_the_card_does_not_send(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"rangeId": 1, "name": "Counter",
                                     "blockList": [139, 140, 141]}))
    sent = app.cloud_fake.zone_writes[-1]
    assert sent["rangeId"] == 1 and sent["name"] == "Counter"
    assert sent["blockList"] == [139, 140, 141]
    assert sent["tagId"] == "custom_" and sent["regionType"] == 0
    fire(world)
    assert status(world) == "ok"


def test_the_zone_types_are_published_beside_the_zones(fp2):
    app, world = fp2()
    app.start()
    assert app.cloud_fake.tag_reads == [9]
    assert zone_attrs(world)["tags"] == [
        {"tagId": "3.6", "tagName": "Bed", "sensitivity": 3},
        {"tagId": "9.2", "tagName": "Corridor", "sensitivity": 1}]


def test_the_zone_types_are_read_once_and_again_on_refresh(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone/3/sensitivity", "High")
    fire(world)
    assert app.cloud_fake.tag_reads == [9]
    cmd(app, "refresh")
    assert app.cloud_fake.tag_reads == [9, 9]


def test_unread_zone_types_fail_nothing_else(fp2):
    app, world = fp2()
    app.cloud_fake.tag_fail = AqaraError("app/tag/bottom/list", 500, "down")
    app.start()
    assert status(world) == "ok"
    assert zone_attrs(world)["tags"] == []
    assert len(zone_attrs(world)["zones"]) == 3


def test_an_edit_sets_the_type_with_the_catalogue_name(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"rangeId": 3, "tagId": "9.2",
                                     "tagName": "Anything", "sensitivity": 1}))
    sent = app.cloud_fake.zone_writes[-1]
    assert sent["tagId"] == "9.2" and sent["tagName"] == "Corridor"
    assert sent["sensitivity"] == 1
    fire(world)
    assert status(world) == "ok"


def test_a_type_outside_the_catalogue_is_refused(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"rangeId": 3, "tagId": "99.9"}))
    assert app.cloud_fake.zone_writes == []
    assert status(world) == "refused"


def test_a_create_with_a_type_sends_it(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"name": "Bed", "blockList": [15, 16],
                                     "sensitivity": 3, "tagId": "3.6"}))
    sent = app.cloud_fake.zone_writes[-1]
    assert "rangeId" not in sent
    assert sent["tagId"] == "3.6" and sent["tagName"] == "Bed"
    fire(world)
    assert status(world) == "ok"


def test_a_create_as_other_is_sent_like_the_official_app(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"name": "Desk", "blockList": [15, 16],
                                     "tagId": "custom_Other"}))
    sent = app.cloud_fake.zone_writes[-1]
    assert sent["tagId"] == "" and sent["tagName"] == "Other"


def with_edge(fp2):
    app, world = fp2()
    app.cloud_fake.zones.append(zone(103, "Edge", 3, 1, [1, 2, 3]))
    app.start()
    return app, world


def refusal_code(world):
    attrs = mqtt_payload(world, "fp2companion/54ef44a1b2c3/status/attrs")
    return json.loads(attrs)["detail"]["code"]


def test_the_room_edge_is_redrawn_with_the_six_fields_the_aqara_app_sends(fp2):
    """regionType 3 is the space outside the room. The Aqara app redraws it
    with six fields and no sensitivity or tag (captured 2026-10-31)."""
    app, world = with_edge(fp2)
    cmd(app, "zone_set", json.dumps({"rangeId": 103, "blockList": [1, 2]}))
    sent = app.cloud_fake.zone_writes[-1]
    assert sorted(sent) == sorted(["deviceId", "blockList", "colorId", "name",
                                   "rangeId", "regionType"])
    assert sent["blockList"] == [1, 2] and sent["name"] == "Edge"
    assert sent["regionType"] == 3
    fire(world)
    assert status(world) == "ok"


@pytest.mark.parametrize("change", [{"name": "Outside"}, {"sensitivity": 3},
                                    {"tagId": "3.6"}])
def test_only_the_edges_blocks_change(fp2, change):
    app, world = with_edge(fp2)
    cmd(app, "zone_set", json.dumps({"rangeId": 103, "blockList": [1, 2],
                                     **change}))
    assert app.cloud_fake.zone_writes == []
    assert refusal_code(world) == "edge_locked"


def test_the_card_resending_the_edges_own_values_is_not_a_change(fp2):
    app, world = with_edge(fp2)
    cmd(app, "zone_set", json.dumps({"rangeId": 103, "blockList": [1, 2],
                                     "name": "Edge", "sensitivity": 1}))
    assert app.cloud_fake.zone_writes[-1]["blockList"] == [1, 2]


def test_the_room_edge_is_never_deleted(fp2):
    app, world = with_edge(fp2)
    cmd(app, "zone_delete", "103")
    assert app.cloud_fake.deletes == []
    assert refusal_code(world) == "edge_locked"


def test_a_room_edge_is_never_created(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"name": "Edge", "blockList": [1],
                                     "regionType": 3}))
    assert app.cloud_fake.zone_writes == []
    assert refusal_code(world) == "edge_locked"


def test_a_restore_redraws_the_edge_and_refuses_to_remove_it(fp2):
    app, world = with_edge(fp2)
    first = zone_attrs(world)["backups"][0]["name"]
    cloud = app.cloud_fake
    for z in cloud.zones:
        if z["rangeId"] == 103:
            z["blockList"] = [1, 2]
    cmd(app, "restore", first)
    assert cloud.zone_writes[-1]["blockList"] == [1, 2, 3]
    assert "sensitivity" not in cloud.zone_writes[-1]
    fire(world)
    assert status(world) == "ok"

    cloud.zone_writes.clear()
    cloud.zones = [z for z in cloud.zones if z["rangeId"] != 103]
    cmd(app, "restore", first)
    assert cloud.zone_writes == [] and cloud.deletes == []
    assert refusal_code(world) == "edge_locked"


def with_entrance(fp2):
    app, world = fp2()
    app.cloud_fake.zones.append(zone(4, "Front door", 2, 1, [300, 301]))
    app.start()
    return app, world


def test_an_entrance_zone_is_never_edited(fp2):
    """regionType 2, reported on a PS-T02E in PR #1. No write of it was
    captured, so the app reads it and never writes it."""
    app, world = with_entrance(fp2)
    cmd(app, "zone_set", json.dumps({"rangeId": 4, "blockList": [300]}))
    assert app.cloud_fake.zone_writes == []
    assert refusal_code(world) == "entrance_locked"


def test_an_entrance_zone_is_never_deleted_or_created(fp2):
    app, world = with_entrance(fp2)
    cmd(app, "zone_delete", "4")
    assert app.cloud_fake.deletes == []
    assert refusal_code(world) == "entrance_locked"
    cmd(app, "zone_set", json.dumps({"name": "Door", "blockList": [1],
                                     "regionType": 2}))
    assert app.cloud_fake.zone_writes == []
    assert refusal_code(world) == "entrance_locked"


def test_a_restore_leaves_an_entrance_zone_as_the_device_holds_it(fp2):
    app, world = with_entrance(fp2)
    first = zone_attrs(world)["backups"][0]["name"]
    cloud = app.cloud_fake
    for z in cloud.zones:
        if z["rangeId"] == 4:
            z["blockList"] = [310]
        if z["rangeId"] == 3:
            z["blockList"] = [43]
    cmd(app, "restore", first)
    assert [w["rangeId"] for w in cloud.zone_writes] == [3]
    fire(world)
    assert status(world) == "ok"
    cloud.zone_writes.clear()
    cloud.zones = [z for z in cloud.zones if z["rangeId"] != 4]
    cmd(app, "restore", first)
    assert cloud.zone_writes == [] and cloud.deletes == []
    assert status(world) == "ok"


def test_the_interference_zone_can_be_redrawn(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"rangeId": 101,
                                     "blockList": [273, 274, 275]}))
    assert app.cloud_fake.zone_writes[-1]["regionType"] == 1
    fire(world)
    assert status(world) == "ok"


def test_a_create_sends_no_id_and_finds_the_new_zone(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"name": "Desk", "blockList": [15, 16],
                                     "sensitivity": 2}))
    sent = app.cloud_fake.zone_writes[-1]
    assert "rangeId" not in sent and sent["tagId"] == ""
    fire(world)
    assert status(world) == "ok"
    desk = discovery(world, "select", "zone_4_sensitivity")
    assert desk["default_entity_id"] == f"select.{PREFIX}_desk_sensitivity"


def test_an_interference_zone_is_never_created(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"name": "Fan", "blockList": [1],
                                     "regionType": 1}))
    assert app.cloud_fake.zone_writes == []
    assert status(world) == "refused"
    detail = json.loads(mqtt_payload(world, "fp2companion/54ef44a1b2c3/status/attrs"))["detail"]
    assert detail["code"] == "interference_locked"


def test_an_edit_of_a_zone_that_is_gone_is_refused(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"rangeId": 9, "name": "Ghost",
                                     "blockList": [1]}))
    assert app.cloud_fake.zone_writes == []
    detail = json.loads(mqtt_payload(world, "fp2companion/54ef44a1b2c3/status/attrs"))["detail"]
    assert detail["code"] == "range_id"


def test_a_bad_zone_is_refused_before_any_backup_or_write(fp2, tmp_path):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"rangeId": 3, "blockList": [0, 321]}))
    assert app.cloud_fake.zone_writes == []
    assert backups(tmp_path) == ["first-read"]
    detail = json.loads(mqtt_payload(world, "fp2companion/54ef44a1b2c3/status/attrs"))["detail"]
    assert detail["code"] == "bad_block"


def test_a_delete_goes_by_id_and_retires_the_select(fp2, tmp_path):
    app, world = fp2()
    app.start()
    cmd(app, "zone_delete", "1")
    assert app.cloud_fake.deletes == [[1]]
    assert "before-write" in backups(tmp_path)
    fire(world)
    assert status(world) == "ok"
    assert mqtt_payload(
        world, f"homeassistant/select/{OBJ}_zone_1_sensitivity/config") == ""


def test_the_interference_zone_is_never_deleted(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_delete", "101")
    assert app.cloud_fake.deletes == []
    assert status(world) == "refused"


def test_a_zone_write_that_did_not_land_is_reported(fp2):
    app, world = fp2()
    app.start()
    app.cloud_fake.lands = False
    cmd(app, "zone_set", json.dumps({"name": "Desk", "blockList": [15]}))
    fire(world)
    assert status(world) == "zone_write_failed"
    detail = json.loads(mqtt_payload(world, "fp2companion/54ef44a1b2c3/status/attrs"))["detail"]
    assert detail["kind"] == "create"


def test_a_restore_puts_every_zone_back(fp2, tmp_path):
    app, world = fp2()
    app.start()
    first = zone_attrs(world)["backups"][0]["name"]
    cloud = app.cloud_fake
    cloud.zones[0]["sensitivity"] = 3                       # Couch changed
    cloud.zones = [z for z in cloud.zones if z["rangeId"] != 1]  # Kitchen gone
    cloud.zones.append(zone(4, "Desk", 0, 1, [15, 16]))      # Desk added
    cmd(app, "restore", first)
    assert cloud.deletes == [[4]]
    assert [z.get("rangeId") for z in cloud.zone_writes] == [3, None]
    assert "before-restore" in backups(tmp_path)
    fire(world)
    assert status(world) == "ok"
    names = sorted(z["name"] for z in cloud.zones)
    assert names == ["Couch", "Interference source", "Kitchen Countertop"]


def test_a_restore_that_needs_a_new_interference_zone_is_refused(fp2):
    app, world = fp2()
    app.start()
    first = zone_attrs(world)["backups"][0]["name"]
    app.cloud_fake.zones = [z for z in app.cloud_fake.zones
                            if z["rangeId"] != 101]
    cmd(app, "restore", first)
    assert app.cloud_fake.zone_writes == [] and app.cloud_fake.deletes == []
    detail = json.loads(mqtt_payload(world, "fp2companion/54ef44a1b2c3/status/attrs"))["detail"]
    assert detail["code"] == "interference_locked"


def test_restoring_the_current_state_sends_nothing(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "restore", zone_attrs(world)["backups"][0]["name"])
    assert app.cloud_fake.zone_writes == [] and app.cloud_fake.deletes == []
    assert status(world) == "ok"


def test_a_restore_name_cannot_reach_another_file(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "restore", "../session.json")
    assert status(world) == "refused"


def test_a_backup_can_be_previewed_before_a_restore(fp2):
    app, world = fp2()
    app.start()
    name = zone_attrs(world)["backups"][0]["name"]
    cmd(app, "preview", name)
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/preview/state") == name
    attrs = json.loads(mqtt_payload(world, "fp2companion/54ef44a1b2c3/preview/attrs"))
    assert [z["rangeId"] for z in attrs["zones"]] == [3, 1, 101]
    assert discovery(world, "sensor", "backup_preview")["default_entity_id"] \
        == f"sensor.{PREFIX}_backup_preview"


def test_a_preview_of_no_backup_is_refused(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "preview", "../session.json")
    assert status(world) == "refused"
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/preview/state") is None


# -------------------------------------------------------------- signing in
BLOB = "QkxPQg==" * 20


def attrs(world):
    return json.loads(mqtt_payload(world, "fp2companion/54ef44a1b2c3/status/attrs"))


def test_the_account_is_shown_partly_hidden(fp2):
    app, world = fp2()
    app.start()
    assert attrs(world)["account"] == "j.w*****@example.com"


def test_a_sign_in_from_the_card_passes_the_blob_and_reads(fp2):
    app, world = fp2()
    app.start()
    app.cloud_fake.account_name = None
    app.cloud_fake.has_token = False
    cmd(app, "login", json.dumps({"account": "new.user@example.com",
                                  "password_rsa": BLOB}))
    assert app.cloud_fake.logins == [("new.user@example.com", BLOB)]
    assert status(world) == "ok"
    assert attrs(world)["account"] == "new*****@example.com"
    assert not any(BLOB in line for line in world.logs)
    assert not any("new.user@example.com" in line for line in world.logs)


def test_a_refused_sign_in_says_so(fp2):
    app, world = fp2()
    app.start()
    app.cloud_fake.login_error = AqaraError("user/login", 10012,
                                            "password error")
    cmd(app, "login", json.dumps({"account": "a@example.com",
                                  "password_rsa": BLOB}))
    assert status(world) == "login_failed"
    assert attrs(world)["detail"]["code"] == 10012
    # The session that was working still is.
    assert attrs(world)["account"] == "j.w*****@example.com"


def test_a_sign_in_that_is_not_json_is_refused(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "login", "user:password")
    assert app.cloud_fake.logins == []
    assert status(world) == "refused"


def test_a_sign_out_forgets_the_session(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "logout")
    assert status(world) == "no_session"
    assert attrs(world)["account"] is None
    assert attrs(world)["signed_in"] is False


def test_a_session_from_before_the_card_counts_as_signed_in(fp2):
    # Live on 2026-10-30: the session tools/login.py made that morning has a
    # token and no account, and card 1.3.0 read that as signed out.
    app, world = fp2()
    app.cloud_fake.account_name = None
    app.start()
    assert attrs(world)["signed_in"] is True
    assert attrs(world)["account"] is None


@pytest.mark.parametrize("account, shown", [
    ("j.walker@example.com", "j.w*****@example.com"),
    ("ab@x.gr", "ab*@x.gr"), ("plainname", "pla******"), (None, None)])
def test_mask_account(fp2, account, shown):
    assert fp2.mod.mask_account(account) == shown


# ---------------------------------------------------------------------- AI
def test_the_ai_switch_reflects_the_device(fp2):
    app, world = fp2()
    app.start()
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/state/ai_detection") == "off"
    cfg = discovery(world, "switch", "ai_detection")
    assert cfg["default_entity_id"] == f"switch.{PREFIX}_ai_detection"


def test_the_ai_switch_writes_and_reads_back(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "ai_detection", "on")
    assert app.cloud_fake.writes == [("4.72.85", "1")]
    app.cloud_fake.values["14.1.85"] = "3"          # the device masks it
    world.timers[-1]["callback"](world.timers[-1]["kwargs"])
    assert mqtt_payload(world, "fp2companion/54ef44a1b2c3/state/ai_detection") == "on"
    assert status(world) == "ok"


def test_sensitivity_is_refused_while_ai_is_on(fp2):
    app, world = fp2()
    app.cloud_fake.values.update({"4.72.85": "1", "14.1.85": "3"})
    app.start()
    cmd(app, "sensitivity", "Low")
    assert app.cloud_fake.writes == []
    assert status(world) == "refused"
    assert attrs(world)["detail"]["code"] == "ai_mode_on"


def test_ai_learning_sends_what_the_aqara_app_sends_and_counts_down(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "ai_learning", "PRESS")
    assert app.cloud_fake.writes == [("4.66.85", "1")]
    assert attrs(world)["learning_until"] is not None
    timer = world.timers[-1]
    assert timer["delay"] == 60
    timer["callback"](timer["kwargs"])
    assert attrs(world)["learning_until"] is None


# ---------------------------------------------------------- several devices
def office_zone():
    return [dict(zone(1, "Desk", 0, 2, [20, 21]), deviceId=OFFICE_DID)]


def two(fp2):
    clouds = {OFFICE_DID: FakeCloud()}
    clouds[OFFICE_DID].zones = office_zone()
    app, world = fp2(devices=[LIVING, OFFICE], clouds=clouds)
    app.start()
    return app, world, clouds[OFFICE_DID]


def test_every_fp2_found_gets_its_own_entities(fp2):
    app, world, _ = two(fp2)
    raw = mqtt_payload(world, "homeassistant/select/"
                              "fp2_companion_lumi1_54ef44d4e5f6_sensitivity/config")
    cfg = json.loads(raw)
    # The id follows the HomeKit entities, built from the original name; the
    # device shows the name the owner gave it.
    assert cfg["default_entity_id"] == "select.presence_sensor_fp2_e5f6_sensitivity"
    assert cfg["device"]["name"] == "Motion Sensor - Office Settings"
    assert cfg["device"]["suggested_area"] == "Office"
    assert cfg["command_topic"] == "fp2companion/54ef44d4e5f6/cmd/sensitivity"
    assert discovery(world, "select", "sensitivity")["default_entity_id"] == \
        f"select.{PREFIX}_sensitivity"


def test_a_command_reaches_only_its_device(fp2):
    app, world, office = two(fp2)
    cmd(app, "approach_distance", "Close", device="54ef44d4e5f6")
    assert office.writes == [("14.47.85", "2")]
    assert app.cloud_fake.writes == []


def test_the_hub_lists_the_devices_and_the_countries(fp2):
    app, world, _ = two(fp2)
    hub = json.loads(mqtt_payload(world, "fp2companion/hub/attrs"))
    # In serial order.
    assert [(d["key"], d["prefix"]) for d in hub["devices"]] == [
        (KEY, PREFIX), ("54ef44d4e5f6", "presence_sensor_fp2_e5f6")]
    assert ["GR", "Greece"] in hub["countries"]
    assert hub["signed_in"] is True
    assert mqtt_payload(world, "fp2companion/hub/state") == "2"


def test_a_device_on_another_account_says_so_and_the_other_works(fp2):
    clouds = {OFFICE_DID: FakeCloud()}
    clouds[OFFICE_DID].fail = AqaraError("res/query/by/resourceId", 755,
                                          "You do not have permission")
    app, world = fp2(devices=[LIVING, OFFICE], clouds=clouds)
    app.start()
    assert mqtt_payload(world, "fp2companion/54ef44d4e5f6/status/state") == \
        "not_on_account"
    assert status(world) == "ok"


def test_a_lost_session_reaches_every_device(fp2):
    app, world, office = two(fp2)
    app.cloud_fake.fail = AqaraAuthError("res/write", 108, "expired")
    cmd(app, "refresh")
    assert status(world) == "no_session"
    assert mqtt_payload(world, "fp2companion/54ef44d4e5f6/status/state") == \
        "no_session"


def test_a_sign_in_carries_the_country(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "login", json.dumps({"account": "a@example.com",
                                  "password_rsa": BLOB, "country": "US"}))
    assert app.cloud_fake.login_country == "US"


def test_rescan_adds_a_device_that_appeared(fp2):
    found = [LIVING]
    app, world = fp2(devices=found)
    app.start()
    found.append(OFFICE)
    cmd(app, "rescan")
    assert set(app.devices) == {KEY, "54ef44d4e5f6"}


def test_a_yaml_serial_adds_a_device_and_exclude_drops_one(fp2):
    app, world = fp2(devices=[LIVING, OFFICE],
                     args={"devices": ["54EF44AABBCC"],
                           "exclude": ["54ef44d4e5f6"]})
    app.start()
    assert set(app.devices) == {KEY, "54ef44aabbcc"}
    assert app.devices["54ef44aabbcc"].info["prefix"] == "fp2_bbcc"


def test_parse_discovery_skips_what_is_not_an_fp2(fp2):
    rows = [{"serial": None}, {"serial": "not a serial"}, LIVING]
    assert [d["key"] for d in fp2.mod.parse_discovery(rows)] == [KEY]


# ------------------------------------------------------------ review fixes
def test_an_earlier_learning_timer_does_not_end_a_later_run(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "ai_learning", "PRESS")
    first = world.timers[-1]
    app._now = lambda: 1793350030.0
    cmd(app, "ai_learning", "PRESS")
    second = world.timers[-1]
    first["callback"](first["kwargs"])
    assert attrs(world)["learning_until"] is not None
    second["callback"](second["kwargs"])
    assert attrs(world)["learning_until"] is None


def test_a_refusal_partway_through_a_restore_is_read_back(fp2):
    app, world = fp2()
    app.start()
    first = zone_attrs(world)["backups"][0]["name"]
    app.cloud_fake.zones.append(zone(4, "Desk", 0, 1, [15, 16]))
    app.cloud_fake.refuse_delete = True
    cmd(app, "restore", first)
    assert status(world) == "refused"
    assert "backup" not in attrs(world)["detail"]["reason"]
    assert world.timers[-1]["callback"] == app.devices[KEY].read_all


def test_a_discovery_that_raises_still_retries(fp2):
    app, world = fp2(devices=RuntimeError("Home Assistant is restarting"))
    app.start()
    assert app.devices == {}
    assert world.timers[-1]["callback"] == app.start


def test_rescans_while_nothing_is_found_keep_one_retry(fp2):
    app, world = fp2(devices=[])
    app.start()
    cmd(app, "rescan")
    cmd(app, "rescan")
    # The fixture's run_in returns "timer-<position>". The first start timer
    # is initialize's; every retry after it must cancel the one before.
    retries = [f"timer-{i + 1}" for i, t in enumerate(world.timers)
               if t["callback"] == app.start][1:]
    assert len(retries) == 3
    assert world.cancelled == retries[:-1]


def test_a_retry_that_fires_cancels_its_own_handle_silently(fp2):
    """AppDaemon removes a one-shot timer before its callback runs, so the
    retry's own handle is gone by the time start() cancels it."""
    app, world = fp2(devices=[])
    app.start()
    retry = world.timers[-1]
    retry["callback"](retry["kwargs"])
    assert world.silent_cancels == [True]


def test_a_token_refresh_republishes_the_hub(fp2):
    app, world = fp2()
    app.start()
    world.calls.clear()
    app.cloud_fake.due = True
    app.token_check()
    assert mqtt_payload(world, "fp2companion/hub/attrs") is not None


# ----------------------------------------------------------------- live positions
class FakeTracker:
    def __init__(self):
        self.calls = []

    def lease(self, did, client):
        self.calls.append(("lease", did, client))

    def release(self, did, client):
        self.calls.append(("release", did, client))

    def shutdown(self):
        self.calls.append(("shutdown",))


def test_a_card_lease_reaches_the_tracker_per_device_and_tab(fp2):
    app, world = fp2(devices=[LIVING, OFFICE])
    app.start()
    app.tracker = FakeTracker()
    cmd(app, "track", json.dumps({"client": "tab-1", "on": True}))
    cmd(app, "track", json.dumps({"client": "tab-2", "on": True}),
        device="54ef44d4e5f6")
    cmd(app, "track", json.dumps({"client": "tab-1", "on": False}))
    cmd(app, "track", "on")                     # no client: ignored
    assert app.tracker.calls == [("lease", DID, "tab-1"),
                                 ("lease", OFFICE_DID, "tab-2"),
                                 ("release", DID, "tab-1")]


def test_live_positions_go_straight_to_the_broker_unretained(fp2):
    app, world = fp2()
    app.start()
    before = len(world.calls)
    app._live_pub(DID, {"state": "live", "targets": []})
    app._live_pub("lumi1.unknown", {"state": "live"})
    calls = world.calls[before:]
    assert len(calls) == 1
    service, _entity, data = calls[0]
    assert service == "mqtt/publish"
    assert data["namespace"] == "mqtt" and data["retain"] is False
    assert data["topic"] == f"{T}/live"
    assert json.loads(data["payload"]) == {"state": "live", "targets": []}


def test_terminate_shuts_the_tracker_down_once(fp2):
    app, world = fp2()
    tracker = app.tracker = FakeTracker()
    app.terminate()
    app.terminate()
    assert tracker.calls == [("shutdown",)]


# ------------------------------------------------------------ card install
class CardWs:
    def __init__(self, resources=(), fail=None):
        self.resources = list(resources)
        self.sent = []
        self.fail = fail

    def __call__(self, messages):
        from card_install import CardInstallError
        out = []
        for m in messages:
            self.sent.append(m)
            if m["type"] == self.fail:
                raise CardInstallError(f"{m['type']}: unknown_command")
            out.append(list(self.resources) if m["type"] == "lovelace/resources" else {})
        return out


def card_app(fp2, tmp_path, ws, **args):
    (tmp_path / "ha" / "www").mkdir(parents=True)
    app, world = fp2(args={"card_dir": str(tmp_path / "ha" / "www" / "fp2"), **args})
    app._make_card_ws = lambda: ws
    return app, world


def test_startup_installs_the_card(fp2):
    app, world = fp2()
    assert app._install_card in [t["callback"] for t in world.timers]


def test_the_card_is_copied_and_its_resource_created(fp2, tmp_path):
    ws = CardWs()
    app, world = card_app(fp2, tmp_path, ws)
    app._install_card()
    copy = tmp_path / "ha" / "www" / "fp2" / "fp2-companion-card.js"
    with open(fp2.mod.CARD_SOURCE, "rb") as handle:
        assert copy.read_bytes() == handle.read()
    assert [m["type"] for m in ws.sent] == ["lovelace/resources",
                                            "lovelace/resources/create"]
    assert any("resource created" in line for line in world.logs)


def test_a_refused_resource_logs_the_manual_step(fp2, tmp_path):
    app, world = card_app(fp2, tmp_path, CardWs(fail="lovelace/resources"))
    app._install_card()
    assert (tmp_path / "ha" / "www" / "fp2" / "fp2-companion-card.js").exists()
    assert any("resource not set" in line and "add the resource" in line
               for line in world.logs)


def test_another_resource_loading_the_card_is_reported(fp2, tmp_path):
    ws = CardWs([{"id": "a", "url": "/local/hvac/fp2-companion-card.js", "type": "module"},
                 {"id": "b", "url": "/local/fp2-companion-card.js", "type": "module"}])
    app, world = card_app(fp2, tmp_path, ws)
    app._install_card()
    assert ws.sent[-1]["type"] == "lovelace/resources/update"
    assert ws.sent[-1]["resource_id"] == "a"
    assert any("ids b" in line for line in world.logs)


def test_without_a_home_assistant_config_folder_nothing_is_written(fp2, tmp_path):
    ws = CardWs()
    app, world = fp2(args={"card_dir": str(tmp_path / "nowhere" / "www" / "fp2")})
    app._make_card_ws = lambda: ws
    app._install_card()
    assert not (tmp_path / "nowhere").exists() and ws.sent == []
    assert any("card not installed" in line for line in world.logs)


def test_a_new_www_folder_asks_for_one_restart(fp2, tmp_path):
    (tmp_path / "ha").mkdir()
    app, world = fp2(args={"card_dir": str(tmp_path / "ha" / "www" / "fp2")})
    app._make_card_ws = lambda: CardWs()
    app._install_card()
    assert any("restart Home Assistant once" in line for line in world.logs)


def test_the_install_can_be_turned_off(fp2, tmp_path):
    ws = CardWs()
    app, world = card_app(fp2, tmp_path, ws, card_install=False)
    app._install_card()
    assert ws.sent == [] and not (tmp_path / "ha" / "www" / "fp2").exists()


# ------------------------------------------------------------ command topics
def test_startup_probes_the_command_topics(fp2):
    app, world = fp2()
    probes = [d["topic"] for s, _e, d in world.calls
              if s == "mqtt/publish" and d.get("namespace") == "mqtt"]
    assert sorted(probes) == ["fp2companion/cmd/probe",
                              "fp2companion/probe/cmd/probe"]


def test_a_probe_is_not_taken_for_a_command(fp2):
    app, world = fp2()
    app.start()
    before = list(world.logs)
    app._on_cmd("MQTT_MESSAGE", {"topic": "fp2companion/cmd/probe",
                                 "payload": app.commands.nonce}, None)
    app._on_cmd("MQTT_MESSAGE", {"topic": "fp2companion/probe/cmd/probe",
                                 "payload": app.commands.nonce}, None)
    assert world.logs == before
    assert app.commands.heard == {"fp2companion/cmd/#", "fp2companion/+/cmd/#"}


def test_a_changed_card_file_is_installed_again(fp2, tmp_path):
    ws = CardWs()
    app, world = card_app(fp2, tmp_path, ws)
    source = tmp_path / "card.js"
    source.write_bytes(b"const CARD_VERSION = '9.0.0';")
    fp2.mod.CARD_SOURCE = str(source)
    try:
        app._install_card()
        sent = len(ws.sent)
        app._watch_card()
        assert len(ws.sent) == sent, "an unchanged card is left alone"
        source.write_bytes(b"const CARD_VERSION = '9.0.1';")
        app._watch_card()
        assert len(ws.sent) > sent
        copy = tmp_path / "ha" / "www" / "fp2" / "fp2-companion-card.js"
        assert copy.read_bytes() == b"const CARD_VERSION = '9.0.1';"
    finally:
        fp2.mod.CARD_SOURCE = __import__("card_install").CARD_SOURCE
