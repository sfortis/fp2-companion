"""The FP2 zone grid, validation, comparison and backups.

The zones below are the live read of the Living Room FP2 on 2026-10-30, block
lists in the order the cloud returned them. The render in
`test_the_live_zones_render_as_the_owner_confirmed` is the one the owner held
against the Aqara app that day and confirmed for shape and orientation, so a
change to the grid arithmetic fails here rather than drawing a mirrored map on
the card.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "apps" / "fp2_companion"))
from zone_model import (  # noqa: E402
    BLOCK_COUNT, ERR_BAD_BLOCK, ERR_BAD_NAME, ERR_BAD_REGION,
    ERR_BAD_SENSITIVITY, ERR_DUPLICATE_BLOCK, ERR_MISSING_FIELD,
    ERR_NO_BLOCKS, ERR_RANGE_ID, ERR_WRONG_DEVICE, ZoneError, ascii_map,
    block_id, cell, created_zone, list_backups, new_zone, plan_restore,
    prune_backups, read_backup, validate_zone, wire_zone, write_backup,
    zone_landed, zones_differ)

DID = "lumi1.54ef44a1b2c3"


def zone(range_id, name, region, sens, color, tag_id, tag_name, blocks):
    return {"deviceId": DID, "blockList": blocks, "colorId": color,
            "name": name, "rangeId": range_id, "regionType": region,
            "sensitivity": sens, "tagId": tag_id, "tagName": tag_name}


LIVE = [
    zone(3, "Couch", 0, 2, 4, "1.2", "Sofa Area",
         [43, 75, 107, 44, 76, 108, 45, 77, 109, 59, 91, 60, 92, 61, 93]),
    zone(2, "Dinner Table", 0, 3, 0, "custom_Other", "Other",
         [168, 200, 232, 169, 201, 233, 170, 202, 234, 184, 216, 248, 185, 217,
          249, 186, 218, 250]),
    zone(1, "Kitchen Countertop", 0, 3, 0, "4.1", "Dining table",
         [139, 140, 172, 204, 236, 141, 173, 205, 237, 156, 188, 220, 157, 189,
          221]),
    zone(101, "Interference source", 1, 1, 6, "custom_", "",
         [273, 274, 275, 276, 277, 278, 279, 280, 281, 282, 283, 284, 285, 286,
          287, 288, 289, 290, 291, 292, 293, 294, 295, 296, 297, 42, 298, 299,
          300, 301, 302, 303, 304, 49, 305, 50, 306, 51, 307, 52, 308, 309,
          310, 311, 312, 313, 314, 315, 316, 317, 318, 319, 320, 65, 66, 67,
          68, 81, 82, 83, 84, 97, 98, 99, 100, 113, 114, 115, 116, 129, 130,
          131, 132, 140, 145, 146, 147, 148, 161, 162, 163, 164, 174, 177, 178,
          179, 180, 193, 194, 195, 196, 209, 210, 211, 212, 225, 226, 227, 228,
          238, 241, 242, 243, 244]),
]

CONFIRMED_RENDER = """\
................
................
.........#CCC...
####......CCC...
####......CCC...
####......CCC...
####......CCC...
####............
####......K*K...
####.......KK...
####...DDD.KK#..
####...DDD.KK...
####...DDD.KK...
####...DDD.KK...
####...DDD.KK#..
####...DDD......
................
################
################
################"""


def fresh(z):
    return json.loads(json.dumps(z))


# --------------------------------------------------------------------- grid
def test_the_corners_of_the_grid():
    assert cell(1) == (0, 0)
    assert cell(16) == (0, 15)
    assert cell(17) == (1, 0)
    assert cell(BLOCK_COUNT) == (19, 15)


def test_every_block_round_trips():
    for b in range(1, BLOCK_COUNT + 1):
        assert block_id(*cell(b)) == b


@pytest.mark.parametrize("bad", [0, BLOCK_COUNT + 1, -3, "5", True, None])
def test_a_block_outside_the_grid_is_refused(bad):
    with pytest.raises(ValueError):
        cell(bad)


def test_the_live_zones_render_as_the_owner_confirmed():
    marks = {3: "C", 2: "D", 1: "K", 101: "#"}
    assert ascii_map(LIVE, marks) == CONFIRMED_RENDER


def test_a_shared_block_renders_as_an_overlap():
    # Block 140 is in both Kitchen Countertop and the interference zone.
    assert cell(140) == (8, 11)
    assert ascii_map(LIVE).splitlines()[8][11] == "*"


# --------------------------------------------------------------- validation
def test_every_live_zone_is_valid():
    for z in LIVE:
        validate_zone(z, DID)


@pytest.mark.parametrize("mutate, code", [
    (lambda z: z.pop("tagName"), ERR_MISSING_FIELD),
    (lambda z: z.update(deviceId="lumi1.54ef44d4e5f6"), ERR_WRONG_DEVICE),
    (lambda z: z.update(blockList=[]), ERR_NO_BLOCKS),
    (lambda z: z.update(blockList=[43, 321]), ERR_BAD_BLOCK),
    (lambda z: z.update(blockList=[43, "44"]), ERR_BAD_BLOCK),
    (lambda z: z.update(blockList=[43, 43]), ERR_DUPLICATE_BLOCK),
    (lambda z: z.update(regionType=2), ERR_BAD_REGION),
    (lambda z: z.update(sensitivity=4), ERR_BAD_SENSITIVITY),
    (lambda z: z.update(sensitivity="2"), ERR_BAD_SENSITIVITY),
    (lambda z: z.update(name="  "), ERR_BAD_NAME),
])
def test_a_bad_zone_is_refused_with_its_code(mutate, code):
    z = fresh(LIVE[0])
    mutate(z)
    with pytest.raises(ZoneError) as exc:
        validate_zone(z, DID)
    assert exc.value.code == code


def test_a_rewrite_sends_exactly_what_was_read():
    z = dict(LIVE[0], extra="dropped")
    out = wire_zone(z)
    assert out["blockList"] == LIVE[0]["blockList"]
    assert "extra" not in out


# --------------------------------------------------------------- comparison
def test_block_order_is_not_a_difference():
    shuffled = [dict(z, blockList=sorted(z["blockList"])) for z in LIVE]
    assert not zones_differ(LIVE, list(reversed(shuffled)))


def test_a_moved_block_is_a_difference():
    changed = [fresh(z) for z in LIVE]
    changed[0]["blockList"][-1] = 94
    assert zones_differ(LIVE, changed)


def test_a_write_landed_only_when_the_readback_matches():
    written = fresh(LIVE[0])
    written["sensitivity"] = 3
    assert not zone_landed(written, LIVE)
    readback = [fresh(z) for z in LIVE]
    readback[0]["sensitivity"] = 3
    readback[0]["blockList"].reverse()
    assert zone_landed(written, readback)


def test_a_write_did_not_land_when_the_zone_is_missing():
    assert not zone_landed(dict(LIVE[0], rangeId=7), LIVE)


# ------------------------------------------------------------------ backups
def test_a_backup_round_trips(tmp_path):
    name = write_backup(str(tmp_path), LIVE, 1793350000, "changed")
    body = read_backup(str(tmp_path), name)
    assert body["reason"] == "changed"
    assert not zones_differ(body["zones"], LIVE)
    assert not list(tmp_path.glob("*.tmp"))


def test_two_backups_in_one_second_do_not_overwrite(tmp_path):
    a = write_backup(str(tmp_path), LIVE, 1793350000, "changed")
    b = write_backup(str(tmp_path), LIVE[:1], 1793350000, "before-write")
    assert a != b
    rows = list_backups(str(tmp_path))
    assert {r["name"] for r in rows} == {a, b}


def test_backups_are_listed_newest_first(tmp_path):
    old = write_backup(str(tmp_path), LIVE, 1793350000, "changed")
    new = write_backup(str(tmp_path), LIVE[:2], 1793360000, "changed")
    rows = list_backups(str(tmp_path))
    assert [r["name"] for r in rows] == [new, old]
    assert rows[0]["zones"] == ["Couch", "Dinner Table"]


@pytest.mark.parametrize("name", ["../session.json", "zones-x.json",
                                  "/share/appdaemon/fp2_companion/session.json",
                                  "zones-20261030T083000.json/../../x", ""])
def test_a_name_from_the_card_cannot_reach_another_file(tmp_path, name):
    with pytest.raises(ValueError):
        read_backup(str(tmp_path), name)


def test_pruning_keeps_the_newest(tmp_path):
    names = [write_backup(str(tmp_path), LIVE, 1793350000 + i * 60, "changed")
             for i in range(5)]
    removed = prune_backups(str(tmp_path), 3)
    assert sorted(removed) == sorted(names[:2])
    assert sorted(os.listdir(tmp_path)) == sorted(names[2:])


def test_no_backup_folder_lists_nothing(tmp_path):
    assert list_backups(str(tmp_path / "missing")) == []


# ------------------------------------------------------- create and restore
# The official app's create, captured 2026-10-30: no rangeId, empty tagId.
# The server answered with rangeId 4 and tagId "custom_Other".
TEST_BLOCKS = [15, 16, 31, 32]


def test_the_test_zone_is_the_top_right_two_by_two():
    assert [cell(b) for b in TEST_BLOCKS] == [(0, 14), (0, 15), (1, 14),
                                             (1, 15)]


def test_a_new_zone_is_shaped_like_the_apps_create():
    z = new_zone(DID, TEST_BLOCKS, "TestZone")
    validate_zone(z, DID, new=True)
    assert "rangeId" not in wire_zone(z, new=True)
    assert z["tagId"] == "" and z["tagName"] == "Other"


def test_a_new_zone_with_an_id_is_refused():
    z = dict(new_zone(DID, TEST_BLOCKS, "TestZone"), rangeId=4)
    with pytest.raises(ZoneError) as exc:
        validate_zone(z, DID, new=True)
    assert exc.value.code == ERR_RANGE_ID


def test_an_edit_without_an_id_is_refused():
    # Sent as it is, the server would read it as a create and add a zone.
    z = fresh(LIVE[0])
    del z["rangeId"]
    with pytest.raises(ZoneError) as exc:
        validate_zone(z, DID)
    assert exc.value.code == ERR_RANGE_ID


def test_the_created_zone_is_found_despite_the_rewritten_tag():
    written = new_zone(DID, TEST_BLOCKS, "TestZone")
    server = dict(written, rangeId=4, tagId="custom_Other",
                  blockList=[16, 32, 15, 31])
    assert created_zone(written, LIVE, LIVE + [server]) == server


def test_no_new_zone_means_the_create_did_not_land():
    written = new_zone(DID, TEST_BLOCKS, "TestZone")
    assert created_zone(written, LIVE, LIVE) is None


def test_restoring_the_current_state_sends_nothing():
    assert plan_restore(LIVE, [fresh(z) for z in LIVE]) == {
        "update": [], "create": [], "delete": []}


def test_a_restore_rewrites_changed_zones_in_place():
    now = [fresh(z) for z in LIVE]
    now[0]["sensitivity"] = 3
    plan = plan_restore(now, LIVE)
    assert [z["rangeId"] for z in plan["update"]] == [3]
    assert plan["create"] == [] and plan["delete"] == []


def test_a_restore_removes_a_zone_the_backup_does_not_have():
    extra = dict(new_zone(DID, TEST_BLOCKS, "TestZone"), rangeId=4)
    plan = plan_restore(LIVE + [extra], LIVE)
    assert plan["delete"] == [4]


def test_a_restore_recreates_a_deleted_zone_without_its_old_id():
    now = [z for z in LIVE if z["rangeId"] != 2]
    plan = plan_restore(now, LIVE)
    assert [z["name"] for z in plan["create"]] == ["Dinner Table"]
    assert "rangeId" not in plan["create"][0]
