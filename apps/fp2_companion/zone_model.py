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
# An entrance and exit zone, "Exits and Entrances" in the Aqara app. A PS-T02E
# reported one in PR #1 on 2026-10-31; a PS-S02D got one on 2026-11-01.
REGION_ENTRANCE = 2
# The room's edge, "Boundary" in the Aqara app: the blocks outside the room,
# which the FP2 does not report people in.
REGION_EDGE = 3
REGION_TYPES = (REGION_DETECTION, REGION_INTERFERENCE, REGION_ENTRANCE,
                REGION_EDGE)

# A device holds at most one zone of each of these kinds, and the server keeps
# it at the id 100 plus its regionType: interference 101, entrance 102, edge
# 103 (read 2026-11-01). Detection zones are numbered from 1 up. The Aqara app
# writes all three with the six fields below, never with a sensitivity or a
# tag, and the server keeps the values the zone already had for those.
SINGLE_REGIONS = (REGION_INTERFERENCE, REGION_ENTRANCE, REGION_EDGE)

# The attributes the Aqara app's zone editor offers. A zone changes between
# detection and one of the other two; entrance to edge directly was never
# captured, so it is not offered (owner, 2026-11-01). The interference zone
# keeps its attribute.
ATTRIBUTES = (REGION_DETECTION, REGION_ENTRANCE, REGION_EDGE)
# The name the Aqara app gives a zone that takes one of these attributes.
ATTRIBUTE_NAMES = {REGION_ENTRANCE: "Exits and Entrances",
                   REGION_EDGE: "Boundary"}

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

# The edge, the entrance zone and the interference zone are written with six
# fields and no sensitivity or tag, as the Aqara app sent each of them on
# 2026-10-31 and 2026-11-01. The server kept the sensitivity and the tag the
# zone already had.
SIX_FIELDS = ("deviceId", "blockList", "colorId", "name", "rangeId",
              "regionType")

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
# Only the blocks of the edge and of the entrance zone change, and their
# attribute. Neither is created, deleted, renamed or given a type or a
# sensitivity, because the Aqara app offers none of that.
ERR_EDGE_LOCKED = "edge_locked"
ERR_ENTRANCE_LOCKED = "entrance_locked"
# An attribute another zone of the device already holds.
ERR_ATTRIBUTE_TAKEN = "attribute_taken"
# A change of attribute that is not from or to detection.
ERR_ATTRIBUTE_CHANGE = "attribute_change"
# A zone type the card asked for that is not in the catalogue, or a type on
# a zone that has none (the interference zone).
ERR_BAD_TAG = "bad_tag"

# A zone's type is a tag. The catalogue the Aqara app offers comes from
# `app/tag/bottom/list?tagType=9` (read 2026-10-31: 30 tags such as "1.2"
# Sofa Area and "3.6" Bed, each with a suggested sensitivity). "Other" is
# not in that list: a zone created with an empty tagId comes back as
# "custom_Other" named "Other", so that pair is the type of an untyped zone.
TAG_TYPE_ZONE = 9
TAG_OTHER_ID = "custom_Other"
TAG_OTHER_NAME = "Other"

BACKUP_PREFIX = "zones-"
# A backup name arrives from the card over MQTT, so it is matched against this
# pattern before it is ever joined to a path.
BACKUP_NAME = re.compile(r"^zones-\d{8}T\d{6}(-\d+)?\.json$")


# The zones the official app was never seen to create or delete, with the code
# a refusal of either move carries. An entrance zone or an edge comes and goes
# only by changing a detection zone's attribute.
LOCKED_REGIONS = {REGION_INTERFERENCE: ERR_INTERFERENCE_LOCKED,
                  REGION_EDGE: ERR_EDGE_LOCKED,
                  REGION_ENTRANCE: ERR_ENTRANCE_LOCKED}


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
    Only a detection zone carries a sensitivity, so only its is checked.
    """
    region = zone.get("regionType")
    if new and region in LOCKED_REGIONS:
        raise ZoneError(LOCKED_REGIONS[region], "only detection zones are "
                                                "created")
    if new and zone.get("rangeId") is not None:
        raise ZoneError(ERR_RANGE_ID, "a new zone carries a rangeId")
    if not new and not _is_range_id(zone.get("rangeId")):
        raise ZoneError(ERR_RANGE_ID, f"an edit needs a rangeId, "
                                      f"got {zone.get('rangeId')!r}")
    fields = _wire_fields(zone, new)
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
    if (region == REGION_DETECTION
            and zone["sensitivity"] not in SENSITIVITIES):
        raise ZoneError(ERR_BAD_SENSITIVITY, repr(zone["sensitivity"]))
    name = zone["name"]
    if not isinstance(name, str) or not name.strip():
        raise ZoneError(ERR_BAD_NAME, repr(name))


def _is_range_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def wire_zone(zone, new=False):
    """The zone as it is sent to `range/set`: exactly the known fields."""
    out = {k: zone[k] for k in _wire_fields(zone, new)}
    out["blockList"] = list(zone["blockList"])
    return out


def stored_zone(zone):
    """The zone as a backup keeps it: every field a read returns, the
    sensitivity and tag of an edge or entrance zone included, although a
    write of one omits them."""
    out = {k: zone[k] for k in ZONE_FIELDS}
    out["blockList"] = list(zone["blockList"])
    return out


def _wire_fields(zone, new):
    if zone.get("regionType") in SINGLE_REGIONS:
        return SIX_FIELDS
    return NEW_ZONE_FIELDS if new else ZONE_FIELDS


# ------------------------------------------------------------- attributes
def check_attribute_change(zone, region, zones):
    """Raise ZoneError unless `zone` may take the attribute `region`.

    The change must be from or to detection, the interference zone keeps its
    attribute, and an entrance or edge attribute another zone holds is
    refused. The Aqara app offers that last one only as a jump to editing the
    zone that holds it, and never converts the zone being edited.
    """
    old = zone.get("regionType")
    if region not in ATTRIBUTES or old not in ATTRIBUTES:
        raise ZoneError(ERR_ATTRIBUTE_CHANGE,
                        f"{zone.get('name')} cannot become {region!r}")
    if REGION_DETECTION not in (old, region):
        raise ZoneError(ERR_ATTRIBUTE_CHANGE,
                        "an attribute changes only from or to detection")
    holder = next((z for z in zones if z.get("regionType") == region
                   and z.get("rangeId") != zone.get("rangeId")), None)
    if region in SINGLE_REGIONS and holder is not None:
        raise ZoneError(ERR_ATTRIBUTE_TAKEN,
                        f"{holder.get('name')} already has that attribute")


def with_attribute(zone, region, name=None, sensitivity=1):
    """The zone as the Aqara app sends it when its attribute becomes
    `region`. It keeps its rangeId, its blocks and its colour.

    To detection it carries every field, as the edge did on 2026-11-01: the
    name, the type Other as `custom_` named "Other", and the sensitivity. To
    an entrance or edge only the six fields go out, with the name the Aqara
    app gives that attribute.
    """
    out = {k: zone[k] for k in ("deviceId", "blockList", "colorId",
                                "rangeId")}
    out["blockList"] = list(zone["blockList"])
    out["regionType"] = region
    if region == REGION_DETECTION:
        out.update(name=name or zone.get("name"), sensitivity=sensitivity,
                   tagId="custom_", tagName=TAG_OTHER_NAME)
    else:
        out["name"] = ATTRIBUTE_NAMES[region]
    return out


def without_blocks_of(zone, region, zones):
    """The zone's blocks less those of the device's `region` zone. The
    Aqara app leaves the interference zone's blocks out of an entrance zone
    before it sends it (captured 2026-11-01)."""
    taken = {b for z in zones if z.get("regionType") == region
             and z.get("rangeId") != zone.get("rangeId")
             for b in z.get("blockList") or []}
    return [b for b in zone["blockList"] if b not in taken]


def new_zone(device_id, blocks, name, sensitivity=1,
             region=REGION_DETECTION):
    """A zone to create, shaped like the official app's first `range/set`."""
    return {"deviceId": device_id, "blockList": list(blocks), "colorId": 0,
            "name": name, "regionType": region, "sensitivity": sensitivity,
            "tagId": "", "tagName": "Other"}


# ----------------------------------------------------------------- zone types
def parse_tags(result):
    """The tag catalogue as `[{tagId, tagName, sensitivity}]`.

    `result` is what `app/tag/bottom/list` returned. The suggested
    sensitivity sits in `extend`, a JSON string such as '{"sensitivity":"3"}';
    it is None when it is missing or not 1 to 3. An item without a tagId or a
    name is dropped, and so is a repeated tagId.
    """
    tags, seen = [], set()
    for item in result if isinstance(result, list) else []:
        if not isinstance(item, dict):
            continue
        tag_id = item.get("tagId")
        name = item.get("tagName")
        if not isinstance(tag_id, str) or not tag_id or tag_id in seen:
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        seen.add(tag_id)
        tags.append({"tagId": tag_id, "tagName": name.strip(),
                     "sensitivity": _suggested_sensitivity(item.get("extend"))})
    return tags


def _suggested_sensitivity(extend):
    try:
        value = int(json.loads(extend).get("sensitivity"))
    except (TypeError, ValueError, AttributeError):
        return None
    return value if value in SENSITIVITIES else None


def apply_tag(zone, tag_id, tags):
    """Set the zone's type to `tag_id`, with the name the catalogue gives.

    The card sends only the id. The name always comes from the catalogue, or
    is "Other" for TAG_OTHER_ID, so the card can never write a type the
    Aqara app does not know. Asking for the tag the zone already has changes
    nothing, which keeps a zone whose type is outside the catalogue editable.
    """
    if tag_id == zone.get("tagId"):
        return zone
    if zone.get("regionType") != REGION_DETECTION:
        raise ZoneError(ERR_BAD_TAG, "only a detection zone has a type")
    if tag_id == TAG_OTHER_ID:
        name = TAG_OTHER_NAME
    else:
        name = next((t["tagName"] for t in tags if t["tagId"] == tag_id), None)
        if name is None:
            raise ZoneError(ERR_BAD_TAG, f"{tag_id!r} is not a zone type")
    zone["tagId"] = tag_id
    zone["tagName"] = name
    return zone


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
    """True when a read after a write holds the zone as written.

    Only the fields the write carried are compared, because the server keeps
    the sensitivity and tag of an edge, entrance or interference zone that a
    six-field write leaves out.
    """
    want = canonical(written)
    keys = _written_keys(written)
    for zone in readback_zones:
        if zone.get("rangeId") == want["rangeId"]:
            got = canonical(zone)
            return all(got[k] == want[k] for k in keys)
    return False


def _written_keys(zone):
    keys = ["name", "regionType", "colorId", "blocks"]
    if "sensitivity" in zone:
        keys.append("sensitivity")
    if "tagId" in zone:
        keys += ["tagId", "tagName"]
    return keys


def attribute_landed(written, readback_zones):
    """The zone a change of attribute left, or None.

    The server gives the zone a new id, 100 plus the regionType for an
    entrance or edge and a free detection id for detection, so it is found
    by its attribute, name and blocks instead. A tag sent as `custom_` may
    come back as the server's own spelling of Other, so the tag is not
    compared.
    """
    want = canonical(written)
    for zone in readback_zones:
        got = canonical(zone)
        if all(got[k] == want[k] for k in ("regionType", "name", "blocks")):
            if (want["regionType"] != REGION_DETECTION
                    or got["sensitivity"] == want["sensitivity"]):
                return zone
    return None


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

    The check after a restore. A zone the restore created or whose attribute
    it changed comes back under a new rangeId and with whatever tag the
    server chose, so ids and tags are not compared. A detection zone is
    compared on its name, sensitivity and blocks. An entrance, edge or
    interference zone only on its blocks, because a six-field write keeps
    the sensitivity it had and the Aqara app names those zones itself.
    """
    def key(zones):
        out = []
        for z in zones:
            c = canonical(z)
            fields = (("regionType", "name", "sensitivity", "blocks")
                      if c["regionType"] == REGION_DETECTION
                      else ("regionType", "blocks"))
            out.append(json.dumps({k: c[k] for k in fields}, sort_keys=True))
        return sorted(out)
    return key(a) == key(b)


def plan_restore(current, backup):
    """What restoring `backup` over `current` sends.

    `to_detection`: (rangeId, zone) pairs that turn an entrance or edge the
    backup lacks into one of the backup's detection zones. `to_single`:
    (rangeId, zone) pairs that turn a detection zone the backup lacks into
    the backup's entrance or edge. Each pairs the zones that share the most
    blocks. `update`: zones rewritten in place. `create`: backup detection
    zones that are gone, sent as new zones, so they come back under a NEW
    id. `delete`: rangeIds that exist now and are not in the backup.

    Zones that already match are left alone, so restoring the state the
    device is in sends nothing. An entrance or edge that has nothing to be
    paired with is a ZoneError, because neither is created or deleted.
    """
    def by_id(zones, regions):
        return {z.get("rangeId"): z for z in zones
                if z.get("regionType") in regions}

    others = tuple(r for r in REGION_TYPES if r not in (REGION_ENTRANCE,
                                                        REGION_EDGE))
    now, kept = by_id(current, others), by_id(backup, others)
    gone = [z for rid, z in kept.items() if rid not in now
            and z.get("regionType") == REGION_DETECTION]
    spare = [z for rid, z in now.items() if rid not in kept
             and z.get("regionType") == REGION_DETECTION]
    plan = {"to_detection": [], "to_single": [], "update": [], "create": [],
            "delete": []}
    for region in (REGION_ENTRANCE, REGION_EDGE):
        has = next((z for z in current if z.get("regionType") == region), None)
        want = next((z for z in backup if z.get("regionType") == region), None)
        if has and want:
            if sorted(has["blockList"]) != sorted(want["blockList"]):
                plan["update"].append(dict(has, blockList=list(want["blockList"])))
        elif has:
            match = _most_shared(has, gone)
            if match is None:
                raise ZoneError(LOCKED_REGIONS[region],
                                f"the backup has no zone for {has.get('name')} "
                                "to become, and it is not deleted")
            gone.remove(match)
            plan["to_detection"].append((has.get("rangeId"), dict(
                with_attribute(has, REGION_DETECTION, match.get("name"),
                               match.get("sensitivity", 1)),
                blockList=list(match["blockList"]),
                tagId=match.get("tagId"), tagName=match.get("tagName"))))
        elif want:
            match = _most_shared(want, spare)
            if match is None:
                raise ZoneError(LOCKED_REGIONS[region],
                                f"no zone can become {want.get('name')}, and "
                                "it is not created")
            spare.remove(match)
            plan["to_single"].append((match.get("rangeId"), dict(
                with_attribute(match, region),
                blockList=list(want["blockList"]))))
    paired = {rid for rid, _z in plan["to_single"]}
    for rid, zone in kept.items():
        if rid in now:
            if canonical(now[rid]) != canonical(zone):
                plan["update"].append(zone)
        elif zone in gone or zone.get("regionType") != REGION_DETECTION:
            # A detection zone not in `gone` was paired above; any other kind
            # missing now is created, which the app refuses as locked.
            plan["create"].append({k: v for k, v in zone.items()
                                   if k != "rangeId"})
    plan["delete"] = sorted(rid for rid in now
                            if rid not in kept and rid not in paired)
    return plan


def _most_shared(zone, candidates):
    """The candidate sharing the most blocks with `zone`, the first on a tie,
    or None when there is no candidate."""
    mine = set(zone.get("blockList") or [])
    best = None
    for other in candidates:
        shared = len(mine & set(other.get("blockList") or []))
        if best is None or shared > best[0]:
            best = (shared, other)
    return best[1] if best else None


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
            "zones": [stored_zone(z) for z in zones]}
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
