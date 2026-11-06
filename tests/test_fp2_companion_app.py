"""The fp2_companion app: discovery, entities, writes, readback and backups.

Driven through the fake World and a fake cloud, so nothing here touches the
network. The fake World never fires timers, so a test calls a scheduled
callback itself.
"""
import json
import os

import pytest

from conftest import World, _bind, _load

from custom_components.fp2_companion.aqara_cloud import (AqaraAuthError,
                                                         AqaraError,
                                                         AqaraRefused)

DID = "lumi1.54ef44a1b2c3"
KEY = "54ef44a1b2c3"
PREFIX = "presence_sensor_fp2_b2c3"

# What the discovery template returned live on 2026-10-30. The Office FP2
# was renamed in Home Assistant; its HomeKit entity ids kept the original name.
LIVING = {"serial": "54EF44A1B2C3", "model": "PS-S02D",
          "original": "Presence-Sensor-FP2-B2C3",
          "name": "Presence-Sensor-FP2-B2C3", "area": "Living Room"}
OFFICE = {"serial": "54EF44D4E5F6", "model": "PS-T02E",
          "original": "Presence-Sensor-FP2-E5F6",
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
            old = next(r for r in self.zones if r["rangeId"] == z["rangeId"])
            new = {**old, **z}
            if old["regionType"] != z["regionType"]:
                # A change of attribute renumbers the zone, as on 2026-11-01:
                # 100 plus the regionType for an entrance or edge, the next
                # free id for detection. An entrance came back with an empty
                # tag.
                if z["regionType"] == 0:
                    new["rangeId"] = max([r["rangeId"] for r in self.zones
                                          if r["rangeId"] < 100] + [0]) + 1
                else:
                    new["rangeId"] = 100 + z["regionType"]
                    new.update(tagId="custom_", tagName="")
            self.zones = [new if r is old else r for r in self.zones]

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
    mod = _load("companion")

    def make(devices=None, world=None, cloud=None, clouds=None, args=None):
        """`devices` is what the discovery template returns. The session and
        the Living Room FP2 share `cloud`, as they share one session file;
        another device gets its own fake from `clouds`, keyed by did."""
        world = world or World()
        app = mod.Fp2Companion()
        _bind(app, world)
        world.app = app
        found = [LIVING] if devices is None else devices
        app.lookups = 0

        def find_devices():
            app.lookups += 1
            if isinstance(found, Exception):
                raise found
            return found
        app.find_devices = find_devices
        app.args = dict({"app_id": "id", "app_key": "key",
                         "state_dir": str(tmp_path)}, **(args or {}))
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


def dev(world, key=KEY):
    return world.app.devices[key]


def cmd(app, key, payload="", device=KEY):
    """One command, as the card or an entity sends it: an app command
    (login, logout, rescan) or one for a device."""
    if key == "login":
        app.login(_json(payload))
        return
    if key == "logout":
        app.logout()
        return
    if key == "rescan":
        app.rescan()
        return
    d = app.devices[device]
    if key.startswith("zone/") and key.endswith("/sensitivity"):
        d.set_zone_sensitivity(key[len("zone/"):-len("/sensitivity")], payload)
        return
    calls = {
        "refresh": lambda: d.refresh(),
        "sensitivity": lambda: d.set_sensitivity(payload),
        "approach_distance": lambda: d.set_approach_distance(payload),
        "ai_detection": lambda: d.set_ai_detection(payload.lower() in ("on", "1")),
        "ai_learning": lambda: d.start_ai_learning(),
        "zone_set": lambda: d.zone_set(_json(payload)),
        "zone_delete": lambda: d.zone_delete(payload),
        "restore": lambda: d.restore(payload),
        "preview": lambda: d.show_preview(payload),
    }
    calls[key]()


def _json(payload):
    """What the card's command carries, parsed; a payload that is not JSON
    goes through as is, so the app's own check sees it."""
    try:
        return json.loads(payload)
    except (TypeError, ValueError):
        return payload


def status(world, key=KEY):
    device = world.app.devices.get(key)
    return device.status if device else None


def zone_attrs(world):
    """What the zones sensor shows, as the worker built it last."""
    return dev(world).zones_attrs


def preview_name(world):
    return (dev(world).preview or {}).get("name")


# ----------------------------------------------------------------- startup
def test_a_device_found_is_announced_once(fp2):
    app, world = fp2()
    app.start()
    app.start()
    assert world.added == [KEY]


def test_the_device_is_found_in_home_assistant(fp2):
    app, world = fp2()
    app.start()
    assert DID in app.dids
    assert app.lookups == 1
    info = dev(world).info
    assert info["prefix"] == PREFIX
    assert info["area"] == "Living Room"
    assert info["name"] == "Presence-Sensor-FP2-B2C3"


def test_the_prefix_comes_from_the_homekit_name(fp2):
    app, world = fp2(devices=[LIVING, OFFICE])
    app.start()
    # The office was renamed in Home Assistant; its HomeKit entity ids, and
    # so this app's, keep the original name.
    assert dev(world, "54ef44d4e5f6").info["prefix"] == "presence_sensor_fp2_e5f6"
    assert dev(world, "54ef44d4e5f6").info["name"] == "Motion Sensor - Office"


def test_no_fp2_found_adds_no_device_and_retries(fp2):
    app, world = fp2(devices=[{"serial": None, "name": None, "area": None}])
    app.start()
    assert app.devices == {}
    assert world.added == []
    assert world.timers[-1]["callback"] == app.start


def test_startup_reads_and_publishes_the_values(fp2):
    app, world = fp2()
    app.start()
    assert dev(world).sensitivity == "Low"
    assert dev(world).approach_distance == "Far"
    assert dev(world).zone_sensitivity(3) == "Medium"
    assert status(world) == "ok"
    assert len(dev(world).zones) == 3


def test_only_detection_zones_get_a_select(fp2):
    app, world = fp2()
    app.start()
    zones = {z["rangeId"]: z["name"] for z in dev(world).detection_zones()}
    assert zones == {3: "Couch", 1: "Kitchen Countertop"}


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
    assert dev(world).sensitivity == "Medium"
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
    detail = attrs(world)["detail"]
    assert detail == {"rid": "14.47.85", "wrote": "2", "reads": "0"}
    assert dev(world).approach_distance == "Far"
    assert len(app.cloud_fake.writes) == 1


def test_an_unknown_option_is_refused_and_nothing_is_written(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "sensitivity", "Maximum")
    assert app.cloud_fake.writes == []
    assert status(world) == "refused"




def test_refresh_reads_again(fp2):
    app, world = fp2()
    app.start()
    app.cloud_fake.values["14.1.85"] = "3"
    cmd(app, "refresh", "PRESS")
    assert dev(world).sensitivity == "High"


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
    shown = attrs(world)
    assert shown["last_error"]["code"] == 302
    assert shown["last_error"]["endpoint"] == "devex/radar/range/list"


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
def test_a_zone_that_went_away_leaves_the_detection_zones(fp2):
    app, world = fp2()
    app.start()
    app.cloud_fake.zones = [z for z in app.cloud_fake.zones
                            if z["rangeId"] != 1]
    cmd(app, "refresh")
    assert [z["rangeId"] for z in dev(world).detection_zones()] == [3]




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
    assert dev(world).zone_sensitivity(3) == "High"


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
    return attrs(world)["detail"]["code"]


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
    app.cloud_fake.zones.append(zone(102, "Exits and Entrances", 2, 3,
                                     [300, 301]))
    app.start()
    return app, world


def test_an_entrance_zone_is_redrawn_with_the_six_fields_the_aqara_app_sends(fp2):
    """regionType 2. The Aqara app redraws it with the six fields it sends for
    the edge, and no sensitivity or tag (captured 2026-11-01)."""
    app, world = with_entrance(fp2)
    cmd(app, "zone_set", json.dumps({"rangeId": 102, "blockList": [300]}))
    sent = app.cloud_fake.zone_writes[-1]
    assert sorted(sent) == sorted(["deviceId", "blockList", "colorId", "name",
                                   "rangeId", "regionType"])
    assert sent["blockList"] == [300] and sent["regionType"] == 2
    fire(world)
    assert status(world) == "ok"


@pytest.mark.parametrize("change", [{"name": "Door"}, {"sensitivity": 1},
                                    {"tagId": "3.6"}])
def test_only_the_entrance_zones_blocks_change(fp2, change):
    app, world = with_entrance(fp2)
    cmd(app, "zone_set", json.dumps({"rangeId": 102, "blockList": [300],
                                     **change}))
    assert app.cloud_fake.zone_writes == []
    assert refusal_code(world) == "entrance_locked"


def test_an_entrance_zone_leaves_out_the_interference_zones_blocks(fp2):
    """The Aqara app dropped block 90, the interference zone's only block,
    from the entrance zone it sent on 2026-11-01."""
    app, world = with_entrance(fp2)
    cmd(app, "zone_set", json.dumps({"rangeId": 102,
                                     "blockList": [300, 273, 301]}))
    assert app.cloud_fake.zone_writes[-1]["blockList"] == [300, 301]
    app.cloud_fake.zone_writes.clear()
    cmd(app, "zone_set", json.dumps({"rangeId": 102, "blockList": [273]}))
    assert app.cloud_fake.zone_writes == []
    assert refusal_code(world) == "no_blocks"


def test_an_entrance_zone_is_never_deleted_or_created(fp2):
    app, world = with_entrance(fp2)
    cmd(app, "zone_delete", "102")
    assert app.cloud_fake.deletes == []
    assert refusal_code(world) == "entrance_locked"
    cmd(app, "zone_set", json.dumps({"name": "Door", "blockList": [1],
                                     "regionType": 2}))
    assert app.cloud_fake.zone_writes == []
    assert refusal_code(world) == "entrance_locked"


def test_a_restore_redraws_the_entrance_zone(fp2):
    app, world = with_entrance(fp2)
    first = zone_attrs(world)["backups"][0]["name"]
    cloud = app.cloud_fake
    for z in cloud.zones:
        if z["rangeId"] == 102:
            z["blockList"] = [310]
    cmd(app, "restore", first)
    sent = cloud.zone_writes[-1]
    assert sent["rangeId"] == 102 and sent["blockList"] == [300, 301]
    assert "sensitivity" not in sent
    fire(world)
    assert status(world) == "ok"


# ---------------------------------------------------------------- attributes
def test_a_detection_zone_becomes_the_entrance_zone(fp2):
    """As on 2026-11-01: six fields, the zone's own id, the name the Aqara app
    gives the attribute. The server moves it to 102."""
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"rangeId": 3, "regionType": 2,
                                     "blockList": [43, 44, 45]}))
    sent = app.cloud_fake.zone_writes[-1]
    assert sent == {"deviceId": DID, "blockList": [43, 44, 45], "colorId": 0,
                    "name": "Exits and Entrances", "rangeId": 3,
                    "regionType": 2}
    fire(world)
    assert status(world) == "ok"
    assert [z["rangeId"] for z in zone_attrs(world)["zones"]
            if z["regionType"] == 2] == [102]


def test_the_edge_becomes_a_detection_zone(fp2):
    """As on 2026-11-01: every detection field, the edge's own id 103, the
    type Other as custom_. The server gives it the next detection id."""
    app, world = with_edge(fp2)
    cmd(app, "zone_set", json.dumps({"rangeId": 103, "regionType": 0,
                                     "name": "Hall", "sensitivity": 2,
                                     "blockList": [1, 2, 3]}))
    sent = app.cloud_fake.zone_writes[-1]
    assert sent["rangeId"] == 103 and sent["regionType"] == 0
    assert (sent["name"], sent["sensitivity"]) == ("Hall", 2)
    assert (sent["tagId"], sent["tagName"]) == ("custom_", "Other")
    fire(world)
    assert status(world) == "ok"
    hall = [z for z in zone_attrs(world)["zones"] if z["name"] == "Hall"]
    assert [(z["rangeId"], z["regionType"]) for z in hall] == [(4, 0)]


def test_an_attribute_another_zone_holds_is_refused(fp2):
    app, world = with_entrance(fp2)
    cmd(app, "zone_set", json.dumps({"rangeId": 3, "regionType": 2,
                                     "blockList": [43]}))
    assert app.cloud_fake.zone_writes == []
    assert refusal_code(world) == "attribute_taken"


@pytest.mark.parametrize("rid, region", [(102, 3), (101, 0), (3, 1)])
def test_an_attribute_changes_only_from_or_to_detection(fp2, rid, region):
    """Entrance to edge was never captured, and the interference zone keeps
    its attribute."""
    app, world = with_entrance(fp2)
    cmd(app, "zone_set", json.dumps({"rangeId": rid, "regionType": region,
                                     "blockList": [300]}))
    assert app.cloud_fake.zone_writes == []
    assert refusal_code(world) == "attribute_change"


def test_a_restore_turns_an_entrance_zone_back_into_its_detection_zone(fp2):
    app, world = fp2()
    app.start()
    first = zone_attrs(world)["backups"][0]["name"]
    cmd(app, "zone_set", json.dumps({"rangeId": 3, "regionType": 2,
                                     "blockList": [43, 44, 45]}))
    fire(world)
    cloud = app.cloud_fake
    assert 102 in [z["rangeId"] for z in cloud.zones]
    cloud.zone_writes.clear()
    cmd(app, "restore", first)
    sent = cloud.zone_writes[0]
    assert (sent["rangeId"], sent["regionType"], sent["name"]) == (102, 0, "Couch")
    assert sent["sensitivity"] == 2 and cloud.deletes == []
    fire(world)
    assert status(world) == "ok"
    assert not [z for z in cloud.zones if z["regionType"] == 2]


def test_a_restore_turns_a_detection_zone_back_into_the_entrance_zone(fp2):
    app, world = with_entrance(fp2)
    first = zone_attrs(world)["backups"][0]["name"]
    cmd(app, "zone_set", json.dumps({"rangeId": 102, "regionType": 0,
                                     "name": "Door", "sensitivity": 3,
                                     "blockList": [300, 301]}))
    fire(world)
    cloud = app.cloud_fake
    door = next(z for z in cloud.zones if z["name"] == "Door")
    cloud.zone_writes.clear()
    cmd(app, "restore", first)
    sent = cloud.zone_writes[0]
    assert (sent["rangeId"], sent["regionType"]) == (door["rangeId"], 2)
    assert cloud.deletes == []
    fire(world)
    assert status(world) == "ok"
    assert [z["rangeId"] for z in cloud.zones if z["regionType"] == 2] == [102]


def test_a_restore_that_would_delete_the_entrance_zone_is_refused(fp2):
    app, world = fp2()
    app.start()
    first = zone_attrs(world)["backups"][0]["name"]
    app.cloud_fake.zones.append(zone(102, "Exits and Entrances", 2, 3, [300]))
    cmd(app, "restore", first)
    assert app.cloud_fake.zone_writes == [] and app.cloud_fake.deletes == []
    assert refusal_code(world) == "entrance_locked"


def test_the_interference_zone_can_be_redrawn(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"rangeId": 101,
                                     "blockList": [273, 274, 275]}))
    sent = app.cloud_fake.zone_writes[-1]
    assert sent["regionType"] == 1
    # Six fields, as the Aqara app sent the interference zone on 2026-11-01.
    assert sorted(sent) == sorted(["deviceId", "blockList", "colorId", "name",
                                   "rangeId", "regionType"])
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
    assert {z["rangeId"]: z["name"] for z in dev(world).detection_zones()}[4] \
        == "Desk"


def test_an_interference_zone_is_never_created(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"name": "Fan", "blockList": [1],
                                     "regionType": 1}))
    assert app.cloud_fake.zone_writes == []
    assert status(world) == "refused"
    detail = attrs(world)["detail"]
    assert detail["code"] == "interference_locked"


def test_an_edit_of_a_zone_that_is_gone_is_refused(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"rangeId": 9, "name": "Ghost",
                                     "blockList": [1]}))
    assert app.cloud_fake.zone_writes == []
    detail = attrs(world)["detail"]
    assert detail["code"] == "range_id"


def test_a_bad_zone_is_refused_before_any_backup_or_write(fp2, tmp_path):
    app, world = fp2()
    app.start()
    cmd(app, "zone_set", json.dumps({"rangeId": 3, "blockList": [0, 321]}))
    assert app.cloud_fake.zone_writes == []
    assert backups(tmp_path) == ["first-read"]
    detail = attrs(world)["detail"]
    assert detail["code"] == "bad_block"


def test_a_delete_goes_by_id(fp2, tmp_path):
    app, world = fp2()
    app.start()
    cmd(app, "zone_delete", "1")
    assert app.cloud_fake.deletes == [[1]]
    assert "before-write" in backups(tmp_path)
    fire(world)
    assert status(world) == "ok"
    assert [z["rangeId"] for z in dev(world).detection_zones()] == [3]


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
    detail = attrs(world)["detail"]
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
    detail = attrs(world)["detail"]
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
    assert preview_name(world) == name
    attrs = dev(world).preview
    assert [z["rangeId"] for z in attrs["zones"]] == [3, 1, 101]


def test_a_preview_of_no_backup_is_refused(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "preview", "../session.json")
    assert status(world) == "refused"
    assert preview_name(world) is None


# -------------------------------------------------------------- signing in
BLOB = "QkxPQg==" * 20


def attrs(world):
    """What the cloud status sensor shows, as the worker built it last."""
    return dev(world).status_attrs


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
    assert dev(world).ai_detection is False


def test_the_ai_switch_writes_and_reads_back(fp2):
    app, world = fp2()
    app.start()
    cmd(app, "ai_detection", "on")
    assert app.cloud_fake.writes == [("4.72.85", "1")]
    app.cloud_fake.values["14.1.85"] = "3"          # the device masks it
    world.timers[-1]["callback"](world.timers[-1]["kwargs"])
    assert dev(world).ai_detection is True
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


def test_every_fp2_found_is_served(fp2):
    app, world, _ = two(fp2)
    assert world.added == [KEY, "54ef44d4e5f6"]
    assert dev(world, "54ef44d4e5f6").sensitivity == "Low"


def test_a_command_reaches_only_its_device(fp2):
    app, world, office = two(fp2)
    cmd(app, "approach_distance", "Close", device="54ef44d4e5f6")
    assert office.writes == [("14.47.85", "2")]
    assert app.cloud_fake.writes == []


def test_the_hub_lists_the_devices_and_the_countries(fp2):
    app, world, _ = two(fp2)
    hub = app.hub_attrs
    # In serial order.
    assert [(d["key"], d["prefix"]) for d in hub["devices"]] == [
        (KEY, PREFIX), ("54ef44d4e5f6", "presence_sensor_fp2_e5f6")]
    assert ["GR", "Greece"] in hub["countries"]
    assert hub["signed_in"] is True
    assert len(app.devices) == 2


def test_a_device_on_another_account_says_so_and_the_other_works(fp2):
    clouds = {OFFICE_DID: FakeCloud()}
    clouds[OFFICE_DID].fail = AqaraError("res/query/by/resourceId", 755,
                                          "You do not have permission")
    app, world = fp2(devices=[LIVING, OFFICE], clouds=clouds)
    app.start()
    assert dev(world, "54ef44d4e5f6").status == \
        "not_on_account"
    assert status(world) == "ok"


def test_a_lost_session_reaches_every_device(fp2):
    app, world, office = two(fp2)
    app.cloud_fake.fail = AqaraAuthError("res/write", 108, "expired")
    cmd(app, "refresh")
    assert status(world) == "no_session"
    assert dev(world, "54ef44d4e5f6").status == \
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


def test_parse_discovery_serves_only_the_fp2_models(fp2):
    rows = [dict(LIVING, model="PS-S02E"), dict(OFFICE, model="PS-S03D"),
            {k: v for k, v in LIVING.items() if k != "model"}]
    assert [d["key"] for d in fp2.mod.parse_discovery(rows)] == [KEY]
    assert [d["key"] for d in fp2.mod.parse_discovery([OFFICE])] == [
        "54ef44d4e5f6"]


def test_an_unknown_fp2_variant_is_logged_once(fp2):
    variant = dict(OFFICE, model="PS-S02F")
    app, world = fp2(devices=[LIVING, variant])
    app.start()
    assert set(app.devices) == {KEY}
    cmd(app, "rescan")
    warned = [line for line in world.logs if "PS-S02F" in line]
    assert len(warned) == 1
    assert "Motion Sensor - Office" in warned[0]


def test_other_aqara_ps_sensors_are_skipped_quietly(fp2):
    fp1e = dict(OFFICE, model="PS-S03D")
    fp300 = dict(OFFICE, serial="54EF44AABBCC", model="PS-S04D")
    app, world = fp2(devices=[LIVING, fp1e, fp300])
    app.start()
    assert set(app.devices) == {KEY}
    assert not [line for line in world.logs if "HomeKit model" in line]


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
    """A one-shot timer has fired by the time its callback runs, so the
    retry's own handle is gone by the time start() cancels it."""
    app, world = fp2(devices=[])
    app.start()
    retry = world.timers[-1]
    retry["callback"](retry["kwargs"])
    assert world.silent_cancels == [True]


def test_a_token_refresh_updates_the_hub(fp2):
    app, world = fp2()
    app.start()
    world.changes.clear()
    app.cloud_fake.due = True
    app.token_check()
    assert None in world.changes and KEY in world.changes


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
    dev(world).track("tab-1", True)
    dev(world, "54ef44d4e5f6").track("tab-2", True)
    dev(world).track("tab-1", False)
    dev(world).track("", True)                  # no client: ignored
    assert app.tracker.calls == [("lease", DID, "tab-1"),
                                 ("lease", OFFICE_DID, "tab-2"),
                                 ("release", DID, "tab-1")]


def test_live_positions_go_to_the_device_that_sent_them(fp2):
    app, world = fp2()
    app.start()
    app._live_out(DID, {"state": "live", "targets": []})
    app._live_out("lumi1.unknown", {"state": "live"})
    assert world.live == [(KEY, {"state": "live", "targets": []})]


def test_terminate_shuts_the_tracker_down_once(fp2):
    app, world = fp2()
    tracker = app.tracker = FakeTracker()
    app.terminate()
    app.terminate()
    assert tracker.calls == [("shutdown",)]


# ------------------------------------------------------------ old card copy
def test_startup_looks_for_the_card_an_older_version_installed(fp2):
    app, world = fp2()
    assert app._check_old_card in [t["callback"] for t in world.timers]


def test_an_old_card_copy_is_reported_and_left_in_place(fp2, tmp_path):
    old = tmp_path / "www" / "fp2-companion"
    old.mkdir(parents=True)
    (old / "fp2-companion-card.js").write_text("old")
    app, world = fp2(args={"card_dir": str(old)})
    app._check_old_card()
    assert any("no longer updates" in line and "/local/fp2-companion/" in line
               for line in world.logs)
    assert (old / "fp2-companion-card.js").read_text() == "old"


def test_no_old_card_copy_logs_nothing(fp2, tmp_path):
    app, world = fp2(args={"card_dir": str(tmp_path / "www" / "fp2-companion")})
    before = list(world.logs)
    app._check_old_card()
    assert world.logs == before


def test_a_session_file_that_does_not_parse_reads_as_signed_out(fp2, tmp_path):
    app, world = fp2()
    app.start()
    (tmp_path / "session.json").write_text("{not json")
    app.session.store.load = lambda: json.loads("{not json")
    app.token_check()
    assert app.hub_attrs["signed_in"] is False
    assert any("session file not readable" in line for line in world.logs)


def test_the_card_sees_only_the_newest_backups(fp2, tmp_path):
    app, world = fp2()
    app.start()
    mod = fp2.mod
    folder = tmp_path / "backups" / DID
    for i in range(mod.BACKUPS_SHOWN + 5):
        (folder / f"zones-2026110{i // 10}T0000{i % 10:02d}.json").write_text(
            json.dumps({"zones": [], "saved_at": i, "reason": "changed"}))
    cmd(app, "refresh")
    assert len(zone_attrs(world)["backups"]) == mod.BACKUPS_SHOWN
