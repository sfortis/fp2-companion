"""Create and delete one test zone through the app's own cloud session.

It proves that zone writes work on an account before the app is trusted
with real zones (docs/DESIGN.md, decision 10). The calls are the ones the
official app makes: a create is `range/set` without a rangeId, and a delete
is `range/del` with a list of ids.

Run it inside the AppDaemon container (`$CONTAINER` below), in two steps
with a look at the Aqara app in between:

    docker exec $CONTAINER \\
        python3 /config/apps/fp2_companion/tools/zone_test.py --did <did> --create
    (look for the zone in the Aqara app)
    docker exec $CONTAINER \\
        python3 /config/apps/fp2_companion/tools/zone_test.py --did <did> --delete

Zones are matched by rangeId and never by name. `--create` backs up the
zones, sends the create, reads the list back and finds the new zone as the
rangeId that was not there before. It records that id in `TEST_ZONE_FILE`.
`--delete` takes no id from the command line: it deletes exactly the
recorded id, so a typed id can never reach a real zone. Nothing secret is
printed.
"""
import argparse
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from aqara_cloud import AqaraCloud, AqaraError, SessionStore  # noqa: E402
from login import SECRETS_FILE, SESSION_FILE, load_identity  # noqa: E402
from zone_model import (ascii_map, created_zone, new_zone,  # noqa: E402
                        validate_zone, wire_zone, write_backup)

BACKUP_ROOT = "/share/appdaemon/fp2_companion/backups"
TEST_ZONE_FILE = "/share/appdaemon/fp2_companion/test_zone.json"
TEST_NAME = "TestZone"
# The top right 2 by 2. Move it if a real zone uses these blocks: the tool
# refuses to create over a detection zone.
TEST_BLOCKS = [15, 16, 31, 32]


def show(zones, highlight=None):
    marks = {z["rangeId"]: "T" if z["rangeId"] == highlight else "o"
             for z in zones}
    print(ascii_map(zones, marks))
    for z in zones:
        print(f"  rangeId {z['rangeId']:>3}  {z['name']:<22} region "
              f"{z['regionType']}  sens {z['sensitivity']}  "
              f"{len(z['blockList'])} blocks")


def create(cloud, did):
    before = cloud.list_zones()
    taken = {b for z in before for b in z["blockList"]
             if z["regionType"] == 0}
    if taken & set(TEST_BLOCKS):
        raise SystemExit("refusing: a detection zone already uses a test block")
    if os.path.exists(TEST_ZONE_FILE):
        raise SystemExit("refusing: a test zone is already recorded; "
                         "run --delete first")
    name = write_backup(os.path.join(BACKUP_ROOT, did), before, time.time(),
                        "before-write")
    print(f"backup: {name}")
    zone = new_zone(did, TEST_BLOCKS, TEST_NAME)
    validate_zone(zone, did, new=True)
    cloud.set_zone(wire_zone(zone, new=True))
    time.sleep(2)
    after = cloud.list_zones()
    made = created_zone(zone, before, after)
    if made is None:
        print(json.dumps({"create": "sent", "landed": False,
                          "zones": len(after)}))
        return 1
    with open(TEST_ZONE_FILE, "w") as fh:
        json.dump({"rangeId": made["rangeId"], "did": did}, fh)
    print(json.dumps({"create": "ok", "rangeId": made["rangeId"],
                      "tagId": made.get("tagId"), "zones": len(after)}))
    show(after, made["rangeId"])
    return 0


def delete(cloud, did):
    try:
        with open(TEST_ZONE_FILE) as fh:
            recorded = json.load(fh)
    except FileNotFoundError:
        raise SystemExit("refusing: no test zone recorded by --create")
    range_id = recorded["rangeId"]
    if recorded.get("did") != did:
        raise SystemExit("refusing: the recorded test zone is on another device")
    before = cloud.list_zones()
    if all(z["rangeId"] != range_id for z in before):
        os.remove(TEST_ZONE_FILE)
        raise SystemExit(f"zone {range_id} is already gone; record cleared")
    name = write_backup(os.path.join(BACKUP_ROOT, did), before, time.time(),
                        "before-write")
    print(f"backup: {name}")
    cloud.delete_zones([range_id])
    time.sleep(2)
    after = cloud.list_zones()
    gone = all(z["rangeId"] != range_id for z in after)
    if gone:
        os.remove(TEST_ZONE_FILE)
    print(json.dumps({"delete": "ok" if gone else "sent", "gone": gone,
                      "rangeId": range_id,
                      "zones": len(after)}))
    show(after)
    return 0 if gone else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true")
    group.add_argument("--delete", action="store_true")
    parser.add_argument("--did", required=True,
                        help="the FP2, as lumi1.<serial in lower case>")
    parser.add_argument("--secrets", default=SECRETS_FILE)
    parser.add_argument("--session", default=SESSION_FILE)
    args = parser.parse_args()

    appid, appkey = load_identity(args.secrets)
    cloud = AqaraCloud(SessionStore(args.session), appid, appkey, args.did)
    try:
        if args.create:
            return create(cloud, args.did)
        return delete(cloud, args.did)
    except AqaraError as exc:
        print(json.dumps({"error": {"endpoint": exc.endpoint,
                                    "code": exc.code, "message": exc.message}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
