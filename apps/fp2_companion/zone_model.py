"""The FP2 zone grid, zone validation, zone comparison and the zone backups.

Free of AppDaemon so every rule here can be tested without a Home Assistant or
a network.

The grid is 16 columns by 20 rows, and a zone is a list of block ids from 1 to
320 in row-major order, so a block id is `row * 16 + column + 1` with both
counted from 0. That was read off the live zones on 2026-10-30 and confirmed
two ways: the owner compared a render with the Aqara app, and the four zones
decompose exactly into rectangles and single blocks under the formula.

Zones may share a block. Block 140 belongs to both Kitchen Countertop and the
interference zone, so nothing here treats an overlap as an error.

The order of `blockList` carries no meaning. A read returns the blocks grouped
by column (Couch reads 43, 75, 107, 44, ...), and the official app appends newly
painted blocks at the end. Every comparison here is therefore on the SET of
blocks. A write keeps the order it was given, so rewriting a zone that was just
read sends exactly what was read.
"""

import json
import os
import re
import time

COLUMNS = 16
ROWS = 20
BLOCK_COUNT = COLUMNS * ROWS

REGION_DETECTION = 0
REGION_INTERFERENCE = 1
REGION_TYPES = (REGION_DETECTION, REGION_INTERFERENCE)

SENSITIVITIES = (1, 2, 3)

# The keys of one zone item, identical in a `range/list` read and in the
# `range/set` write the official app sends.
ZONE_FIELDS = ("deviceId", "blockList", "colorId", "name", "rangeId",
               "regionType", "sensitivity", "tagId", "tagName")

# A NEW zone is the same item without `rangeId`: the official app omits the
# field and the server assigns the next free id (captured 2026-10-30, a 2x2
# test zone came back as rangeId 4). The server also turns an empty `tagId`
# into "custom_Other", so a created zone is recognised by what it means and
# not by an exact copy of what was sent.
NEW_ZONE_FIELDS = tuple(k for k in ZONE_FIELDS if k != "rangeId")

# Why a zone was refused. A caller decides on these codes, never on the text
# beside them.
ERR_MISSING_FIELD = "missing_field"
ERR_BAD_BLOCK = "bad_block"
ERR_DUPLICATE_BLOCK = "duplicate_block"
ERR_NO_BLOCKS = "no_blocks"
ERR_BAD_REGION = "bad_region"
ERR_BAD_SENSITIVITY = "bad_sensitivity"
ERR_BAD_NAME = "bad_name"
ERR_WRONG_DEVICE = "wrong_device"
ERR_RANGE_ID = "range_id"           # a new zone with an id, or an edit without
# The interference zone may be redrawn but not created or deleted: neither
# move was captured from the official app (owner, 2026-10-30).
ERR_INTERFERENCE_LOCKED = "interference_locked"

BACKUP_PREFIX = "zones-"
# A backup name arrives from the card over MQTT, so it is matched against this
# pattern before it is ever joined to a path.
BACKUP_NAME = re.compile(r"^zones-\d{8}T\d{6}(-\d+)?\.json$")


class ZoneError(ValueError):
    """A zone the app refuses to write. `code` is one of the ERR_ constants."""

    def __init__(self, code, detail):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# ------------------------------------------------------------------- the grid
def block_id(row, column):
    """The block id of a cell, both counted from 0."""
    if not (0 <= row < ROWS and 0 <= column < COLUMNS):
        raise ValueError(f"cell ({row}, {column}) is outside the grid")
    return row * COLUMNS + column + 1


def cell(block):
    """The (row, column) of a block id, both counted from 0."""
    if not _is_block(block):
        raise ValueError(f"block {block!r} is outside 1..{BLOCK_COUNT}")
    return divmod(block - 1, COLUMNS)


def _is_block(value):
    # bool is an int in Python, and True would otherwise pass as block 1.
    return (isinstance(value, int) and not isinstance(value, bool)
            and 1 <= value <= BLOCK_COUNT)


# ----------------------------------------------------------------- validation
def validate_zone(zone, device_id, new=False):
    """Raise ZoneError unless `zone` is safe to send to `device_id`.

    `new` is a zone to create, which must NOT carry a `rangeId`. An edit
    must, or the server would read it as a create and add a second zone.
    """
    if new and zone.get("rangeId") is not None:
        raise ZoneError(ERR_RANGE_ID, "a new zone carries a rangeId")
    if not new and not _is_range_id(zone.get("rangeId")):
        raise ZoneError(ERR_RANGE_ID, f"an edit needs a rangeId, "
                                      f"got {zone.get('rangeId')!r}")
    fields = NEW_ZONE_FIELDS if new else ZONE_FIELDS
    missing = [k for k in fields if k not in zone]
    if missing:
        raise ZoneError(ERR_MISSING_FIELD, ", ".join(missing))
    if zone["deviceId"] != device_id:
        raise ZoneError(ERR_WRONG_DEVICE, str(zone["deviceId"]))
    blocks = zone["blockList"]
    if not isinstance(blocks, list) or not blocks:
        raise ZoneError(ERR_NO_BLOCKS, "blockList is empty")
    bad = [b for b in blocks if not _is_block(b)]
    if bad:
        raise ZoneError(ERR_BAD_BLOCK, repr(bad[:5]))
    if len(set(blocks)) != len(blocks):
        raise ZoneError(ERR_DUPLICATE_BLOCK, "blockList repeats a block")
    if zone["regionType"] not in REGION_TYPES:
        raise ZoneError(ERR_BAD_REGION, repr(zone["regionType"]))
    if zone["sensitivity"] not in SENSITIVITIES:
        raise ZoneError(ERR_BAD_SENSITIVITY, repr(zone["sensitivity"]))
    name = zone["name"]
    if not isinstance(name, str) or not name.strip():
        raise ZoneError(ERR_BAD_NAME, repr(name))


def _is_range_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def wire_zone(zone, new=False):
    """The zone as it is sent to `range/set`: exactly the known fields."""
    out = {k: zone[k] for k in (NEW_ZONE_FIELDS if new else ZONE_FIELDS)}
    out["blockList"] = list(zone["blockList"])
    return out


def new_zone(device_id, blocks, name, sensitivity=1,
             region=REGION_DETECTION):
    """A zone to create, shaped like the official app's first `range/set`."""
    return {"deviceId": device_id, "blockList": list(blocks), "colorId": 0,
            "name": name, "regionType": region, "sensitivity": sensitivity,
            "tagId": "", "tagName": "Other"}


# ----------------------------------------------------------------- comparison
def canonical(zone):
    """A zone reduced to what it means, for comparison."""
    return {
        "rangeId": zone.get("rangeId"),
        "name": zone.get("name"),
        "regionType": zone.get("regionType"),
        "sensitivity": zone.get("sensitivity"),
        "colorId": zone.get("colorId"),
        "tagId": zone.get("tagId"),
        "tagName": zone.get("tagName"),
        "blocks": sorted(zone.get("blockList") or []),
    }


def zone_landed(written, readback_zones):
    """True when a read after a write holds the zone exactly as written."""
    want = canonical(written)
    for zone in readback_zones:
        if zone.get("rangeId") == want["rangeId"]:
            return canonical(zone) == want
    return False


def zones_differ(a, b):
    """True when two zone lists differ in anything but order."""
    def key(zones):
        return sorted((canonical(z) for z in zones),
                      key=lambda z: str(z["rangeId"]))
    return key(a) != key(b)


def created_zone(written, before, after):
    """The zone a create added, or None when no new zone matches it.

    A new zone is one whose rangeId was not in `before`. It must also hold
    the same blocks, name, region and sensitivity as `written`; the tag and
    the colour are left out, because the server rewrites an empty tag.
    """
    old_ids = {z.get("rangeId") for z in before}
    want = canonical(written)
    for zone in after:
        if zone.get("rangeId") in old_ids:
            continue
        got = canonical(zone)
        if all(got[k] == want[k] for k in
               ("name", "regionType", "sensitivity", "blocks")):
            return zone
    return None


def same_layout(a, b):
    """True when two zone lists hold the same zones apart from their ids.

    The check after a restore: a zone the restore had to create comes back
    under a new rangeId and with whatever tag the server chose, so only the
    name, the region, the sensitivity and the blocks are compared.
    """
    def key(zones):
        return sorted(json.dumps({k: canonical(z)[k] for k in
                                  ("name", "regionType", "sensitivity",
                                   "blocks")}, sort_keys=True)
                      for z in zones)
    return key(a) == key(b)


def plan_restore(current, backup):
    """What restoring `backup` over `current` sends, as three lists.

    `update`: backup zones whose rangeId exists now and differs, rewritten in
    place. `create`: backup zones whose rangeId is gone, sent as new zones, so
    they come back under a NEW id the server assigns. `delete`: rangeIds that
    exist now and are not in the backup. Zones that already match are left
    alone, so restoring the state the device is in sends nothing.
    """
    now = {z.get("rangeId"): z for z in current}
    kept = {z.get("rangeId") for z in backup}
    update, create = [], []
    for zone in backup:
        rid = zone.get("rangeId")
        if rid in now:
            if canonical(now[rid]) != canonical(zone):
                update.append(zone)
        else:
            create.append({k: v for k, v in zone.items() if k != "rangeId"})
    delete = sorted(rid for rid in now if rid not in kept)
    return {"update": update, "create": create, "delete": delete}


# ------------------------------------------------------------------- backups
def backup_name(now):
    return time.strftime(f"{BACKUP_PREFIX}%Y%m%dT%H%M%S.json",
                         time.localtime(now))


def write_backup(directory, zones, now, reason):
    """Write one backup file and return its name.

    Written to a temporary file and renamed, so a crash part way leaves either
    the old set of backups or the new one and never a truncated file that a
    restore would later read as an empty zone list.
    """
    os.makedirs(directory, exist_ok=True)
    name = backup_name(now)
    path = os.path.join(directory, name)
    n = 1
    while os.path.exists(path):          # two backups inside one second
        name = backup_name(now).replace(".json", f"-{n}.json")
        path = os.path.join(directory, name)
        n += 1
    body = {"saved_at": now, "reason": reason,
            "zones": [wire_zone(z) for z in zones]}
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(body, fh, indent=1)
    os.replace(tmp, path)
    return name


def list_backups(directory):
    """Every backup, newest first, as the card shows them."""
    try:
        names = [n for n in os.listdir(directory) if BACKUP_NAME.match(n)]
    except FileNotFoundError:
        return []
    rows = []
    for name in sorted(names, reverse=True):
        try:
            body = read_backup(directory, name)
        except (OSError, ValueError):
            continue
        rows.append({
            "name": name,
            "saved_at": body.get("saved_at"),
            "reason": body.get("reason"),
            "zones": [z.get("name") for z in body["zones"]],
        })
    return rows


def read_backup(directory, name):
    if not BACKUP_NAME.match(name or ""):
        raise ValueError(f"not a backup name: {name!r}")
    with open(os.path.join(directory, name)) as fh:
        body = json.load(fh)
    if not isinstance(body, dict) or not isinstance(body.get("zones"), list):
        raise ValueError(f"{name} holds no zone list")
    return body


def prune_backups(directory, keep):
    """Delete all but the newest `keep` backups. Returns the names removed."""
    names = sorted((n for n in os.listdir(directory) if BACKUP_NAME.match(n)),
                   reverse=True)
    removed = names[keep:] if keep > 0 else []
    for name in removed:
        os.remove(os.path.join(directory, name))
    return removed


# -------------------------------------------------------------------- render
def ascii_map(zones, symbols=None):
    """The grid as text, one character per block, for logs and tools.

    `symbols` maps a rangeId to a character. A block in two zones is `*`.
    """
    symbols = symbols or {}
    owners = {}
    for i, zone in enumerate(zones):
        mark = symbols.get(zone.get("rangeId"), chr(ord("A") + i % 26))
        for block in zone.get("blockList") or []:
            owners.setdefault(block, []).append(mark)
    lines = []
    for row in range(ROWS):
        line = ""
        for column in range(COLUMNS):
            marks = owners.get(block_id(row, column))
            line += "." if not marks else (marks[0] if len(marks) == 1 else "*")
        lines.append(line)
    return "\n".join(lines)
