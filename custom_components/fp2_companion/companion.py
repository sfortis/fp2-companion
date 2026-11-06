"""fp2_companion: every Aqara FP2's settings and zones in Home Assistant.

The FP2 reports presence locally over HomeKit and has no local configuration
channel, so its settings go through the Aqara cloud app API (`aqara_cloud.py`).
The integration's entities read their state from the objects here, and the
card's commands call their methods. Plan and every owner decision:
docs/DESIGN.md

It runs inside Home Assistant as the fp2_companion integration (decision 26).
What Home Assistant provides, the log, timers, the device registry and the
entities, comes from a runtime (`runtime.py`) that subclasses `Fp2Companion`;
the tests give it a fake one. The code here blocks on the cloud, so the runtime calls it
on a worker thread of its own, one callback at a time.

One app serves every FP2 (decision 17). It finds them in Home Assistant as
HomeKit devices of model PS-S02D, PS-S02E or PS-T02E, so nothing about a device is
written in the yaml: the cloud device id comes from the serial number, the
entity id prefix from the device's original name (which its HomeKit entity ids
were built from), and the area from the device. A yaml list of serials covers
an FP2 that is not paired over HomeKit. `Fp2Device` holds everything about one
FP2; `Fp2Companion` holds what they share: the discovery, the one cloud
session and the sign-in.

The app never polls the cloud. It reads at startup, after every write and when
Refresh is pressed, which keeps the calls on the account to what a person
actually did (owner's decision, 2026-10-30). The one stream it does take is
the live target positions, and only while a card holds a lease on them
(`live_track.py`, owner's decision, 2026-10-31).
"""

import os
import re
import threading
import time
import unicodedata

from .aqara_cloud import (RID_AI_LEARNING, RID_AI_MODE, RID_APPROACH_DISTANCE,
                         RID_SENSITIVITY, AqaraAuthError, AqaraCloud,
                         AqaraError, AqaraRefused, SessionStore,
                         did_from_serial, jwt_exp)
from .live_track import LiveTracker, to_grid
from .zone_model import (COLUMNS, ERR_BAD_REGION, ERR_EDGE_LOCKED,
                        ERR_ENTRANCE_LOCKED, ERR_NO_BLOCKS, ERR_RANGE_ID,
                        LOCKED_REGIONS, REGION_DETECTION, REGION_EDGE,
                        REGION_ENTRANCE, REGION_INTERFERENCE, ROWS,
                        TAG_OTHER_ID, TAG_TYPE_ZONE, ZoneError, apply_tag,
                        attribute_landed, check_attribute_change,
                        created_zone, list_backups, new_zone, parse_tags,
                        plan_restore, prune_backups, read_backup, same_layout,
                        validate_zone, wire_zone, with_attribute,
                        without_blocks_of, write_backup, zone_landed,
                        zones_differ)

# What the app keeps in the folder the runtime names in args["state_dir"]:
# the one session and the zone backups.
SESSION_NAME = "session.json"
BACKUP_FOLDER = "backups"

# What a zone write did, carried to its readback. Zones are matched by
# rangeId and never by name (owner, 2026-10-30).
KIND_EDIT = "edit"
KIND_CREATE = "create"
KIND_DELETE = "delete"
KIND_RESTORE = "restore"
# An edit that changes the zone's attribute, after which the server gives it a
# new rangeId, so its readback finds it by attribute and blocks.
KIND_ATTRIBUTE = "attribute"

SENSITIVITY_LABELS = {"1": "Low", "2": "Medium", "3": "High"}
APPROACH_LABELS = {"0": "Far", "1": "Middle", "2": "Close"}

# How long after a write the readback waits. The research waited 2 seconds and
# every readback it made after that matched.
WRITE_SETTLE_SECONDS = 3
DISCOVERY_RETRY_SECONDS = 60
BACKUPS_SHOWN = 20

# The status sensor's state. A consumer decides on these codes and never on
# the message beside them.
STATUS_STARTING = "starting"
STATUS_OK = "ok"
STATUS_NOT_ON_ACCOUNT = "not_on_account"
STATUS_NO_SESSION = "no_session"
STATUS_CLOUD_ERROR = "cloud_error"
STATUS_WRITE_NOT_APPLIED = "write_not_applied"
STATUS_ZONE_WRITE_FAILED = "zone_write_failed"
STATUS_REFUSED = "refused"

# What the cloud answers for a device id the signed-in account does not hold,
# measured on 2026-10-30 with a made-up id: 755 ("You do not have permission")
# from a rid query and 706 ("This device is deleted or you do not have
# permission") from the zone list.
CODES_NOT_ON_ACCOUNT = (706, 755)

# Why a command was refused, beside the free text of the refusal. The card
# decides on the code (owner's rule: no app decides on a bare string).
REFUSED_AI_MODE_ON = "ai_mode_on"

# How long an AI learning run lasts in the Aqara app. Nothing on the cloud
# reports progress, so the end is this many seconds after the start.
AI_LEARNING_SECONDS = 60

STATUS_TEXT = {
    STATUS_STARTING: "Starting.",
    STATUS_OK: "Settings and zones read from the cloud.",
    STATUS_NOT_ON_ACCOUNT: "This FP2 is not on the signed-in Aqara account.",
    STATUS_NO_SESSION: "Not signed in to Aqara. Sign in from the "
                       "integration in Settings, Devices & services.",
    STATUS_CLOUD_ERROR: "The last cloud call failed.",
    STATUS_WRITE_NOT_APPLIED: "The cloud accepted the write but the device "
                              "still reports the old value. The AI "
                              "high-precision mode in the Aqara app ignores "
                              "sensitivity writes; check that it is off.",
    STATUS_ZONE_WRITE_FAILED: "The cloud accepted the zone change but the "
                              "zones read back afterwards do not show it.",
    STATUS_REFUSED: "The command was refused.",
}

# The HomeKit models of an FP2. Aqara's spec page lists PS-S02D and PS-S02E,
# which differ only in what is in the box, and a user reported PS-T02E.
FP2_MODELS = ("PS-S02D", "PS-S02E", "PS-T02E")
# A model in these families that is not in FP2_MODELS is probably an FP2
# variant nobody has reported yet, so the app says it skipped one. Other
# Aqara PS models (PS-S03D is the FP1E, PS-S04D the FP300) can reach
# HomeKit through an Aqara hub and are skipped without a word.
FP2_MODEL_FAMILIES = ("PS-S02", "PS-T02")


def slugify(text):
    """Close to Home Assistant's slugify for the names this app meets."""
    folded = unicodedata.normalize("NFKD", str(text or ""))
    folded = folded.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", folded).strip("_")


def parse_discovery(found, manual=(), exclude=()):
    """The devices to serve, from the discovery rows and the yaml.

    Each is {key, did, name, prefix, area}. The key is the serial in lower
    case and names the device to the card. A row whose model is not an
    FP2 model or whose serial is not an FP2 serial is skipped, an excluded
    serial is dropped, and a manual serial already found is not added twice.
    """
    skip = {str(s).strip().lower() for s in exclude}
    out = {}
    for row in found or []:
        if row.get("model") not in FP2_MODELS:
            continue
        try:
            did = did_from_serial(row.get("serial"))
        except ValueError:
            continue
        key = did.split(".", 1)[1]
        original = row.get("original") or row.get("name") or did
        out[key] = {"key": key, "did": did,
                    "name": row.get("name") or original,
                    "prefix": slugify(original), "area": row.get("area")}
    for serial in manual or []:
        try:
            did = did_from_serial(serial)
        except ValueError:
            continue
        key = did.split(".", 1)[1]
        name = f"FP2 {key[-4:].upper()}"
        out.setdefault(key, {"key": key, "did": did, "name": name,
                             "prefix": slugify(name), "area": None})
    return [d for k, d in sorted(out.items()) if k not in skip]


def unknown_fp2_models(found):
    """The rows whose model looks like an FP2 variant that is not in
    FP2_MODELS, as {model: device name}, so the app can say what it skipped."""
    out = {}
    for row in found or []:
        model = row.get("model")
        if (isinstance(model, str) and model not in FP2_MODELS
                and model.startswith(FP2_MODEL_FAMILIES)):
            out.setdefault(model, row.get("name") or row.get("original"))
    return out


class Fp2Device:
    """One FP2: its values, zones, backups, status and the commands on it.

    Shared services (logging, timers, the cloud lock, the clock) come from
    the app, so a device never talks to Home Assistant by itself. After any
    change it calls `_changed`, and the runtime updates its entities.
    """

    def __init__(self, app, info):
        self.app = app
        self.info = dict(info,
                         backup_dir=os.path.join(app.backup_root, info["did"]))
        self.key = info["key"]
        self.cloud = app._make_cloud(info["did"])
        self.values = {}
        self.zones = []
        self.last_read = None
        self.last_error = None
        self.learning_until = None
        self.status = STATUS_STARTING
        self.status_detail = None
        self._snapshot()

    # ------------------------------------------------------------ commands
    # The entities and the card call these on the app's worker thread.

    def refresh(self, kwargs=None):
        """Read the settings, the zones and the zone types again."""
        self.read_all(tags=True)

    def set_sensitivity(self, label):
        self._write_setting(RID_SENSITIVITY, SENSITIVITY_LABELS, label)

    def set_approach_distance(self, label):
        self._write_setting(RID_APPROACH_DISTANCE, APPROACH_LABELS, label)

    def set_ai_detection(self, on):
        self._write_value(RID_AI_MODE, "1" if on else "0")

    def start_ai_learning(self):
        self._start_learning()

    def set_zone_sensitivity(self, range_id, label):
        self._zone_sensitivity(range_id, label)

    def zone_set(self, request):
        self._zone_set(request)

    def zone_delete(self, range_id):
        self._zone_delete(range_id)

    def restore(self, name):
        self._restore(name)

    def track(self, client, on):
        """A card's lease on the live positions. Each browser tab is its own
        client, so one tab that closes does not stop the dots in another tab
        watching the same FP2."""
        client = str(client or "")[:64]
        if not client or self.app.tracker is None:
            return
        if on:
            self.app.tracker.lease(self.info["did"], client)
        else:
            self.app.tracker.release(self.info["did"], client)

    def read_all(self, kwargs=None, tags=False):
        """Read the settings and the zones. The zone types are read once and
        kept by the app, since every FP2 on the account shares them; Refresh
        (`tags`) reads them again."""
        with self.app.lock:
            try:
                values = self.cloud.query_rids([RID_SENSITIVITY,
                                                RID_APPROACH_DISTANCE,
                                                RID_AI_MODE])
                zones = self.cloud.list_zones()
                if tags or not self.app.zone_tags:
                    self._read_tags()
            except AqaraError as exc:
                if exc.code in CODES_NOT_ON_ACCOUNT:
                    self.values = {}
                    self.app.log(f"{self.info['name']} is not on the "
                                 "signed-in account", level="WARNING")
                    self._set_status(STATUS_NOT_ON_ACCOUNT)
                    return False
                self._cloud_failed(exc)
                return False
        self.values = values
        self.zones = zones
        self.last_read = self.app._now()
        self._backup_if_changed(zones)
        if self.status == STATUS_CLOUD_ERROR:
            self.app.log(f"{self.info['name']}: the Aqara cloud answers again")
        self._set_status(STATUS_OK)
        return True

    def _read_tags(self):
        """Fill the app's zone type catalogue. A failure here leaves the old
        catalogue and fails nothing else: without it the card can still edit
        a zone, it only cannot change the zone's type."""
        try:
            tags = parse_tags(self.cloud.list_zone_tags(TAG_TYPE_ZONE))
        except AqaraError as exc:
            self.app.log(f"zone types not read: {exc}", level="WARNING")
            return
        if tags:
            self.app.zone_tags = tags

    def _backup_if_changed(self, zones):
        folder = self.info["backup_dir"]
        rows = list_backups(folder, limit=1)
        if rows:
            try:
                latest = read_backup(folder, rows[0]["name"])["zones"]
            except (OSError, ValueError):
                latest = None
            if latest is not None and not zones_differ(latest, zones):
                return
        name = write_backup(folder, zones, self.app._now(),
                            "changed" if rows else "first-read")
        prune_backups(folder, self.app.backups_keep)
        self.app.log(f"zones backed up to {name}")

    def _write_setting(self, rid, labels, label):
        value = next((v for v, text in labels.items() if text == label), None)
        if value is None:
            self._refuse(f"{label!r} is not an option of {rid}")
            return
        # With the AI mode on the device ignores a sensitivity write and
        # reports 3, so sending one would only end in write_not_applied.
        if rid == RID_SENSITIVITY and self.values.get(RID_AI_MODE) == "1":
            self._refuse("sensitivity is set by AI person detection while it "
                         "is on", REFUSED_AI_MODE_ON)
            return
        self._write_value(rid, value, label)

    def _write_value(self, rid, value, label=None):
        with self.app.lock:
            try:
                self.cloud.write_rid(rid, value)
            except AqaraRefused as exc:
                self._refuse(str(exc))
                return
            except AqaraError as exc:
                self._cloud_failed(exc)
                return
        self.app.log(f"{rid} -> {value}{f' ({label})' if label else ''} written; "
                 "reading it back")
        self.app.run_in(self._verify_write, WRITE_SETTLE_SECONDS,
                    rid=rid, value=value)

    def _verify_write(self, kwargs):
        if not self.read_all():
            return
        rid, value = kwargs["rid"], kwargs["value"]
        if self.values.get(rid) != value:
            self.app.log(f"{rid}: wrote {value}, the device still reports "
                     f"{self.values.get(rid)}", level="WARNING")
            self._set_status(STATUS_WRITE_NOT_APPLIED,
                             {"rid": rid, "wrote": value,
                              "reads": self.values.get(rid)})

    def _start_learning(self):
        """Send what the Aqara app sends to start AI learning. Nothing on
        the cloud reports progress or the end, so the app shows when the
        run should end and the card counts down to it."""
        with self.app.lock:
            try:
                self.cloud.write_rid(RID_AI_LEARNING, "1")
            except AqaraError as exc:
                self._cloud_failed(exc)
                return
        self.learning_until = self.app._now() + AI_LEARNING_SECONDS
        self.app.log("AI learning started")
        self._changed()
        # The end belongs to this run: a second press restarts the count, and
        # the first run's timer must not end the second one early.
        self.app.run_in(self._learning_done, AI_LEARNING_SECONDS,
                        until=self.learning_until)

    def _learning_done(self, kwargs=None):
        if (kwargs or {}).get("until") != self.learning_until:
            return
        self.learning_until = None
        self._changed()

    # ------------------------------------------------------------- zones
    #
    # Every zone write follows the same four steps. The zone list is read
    # fresh from the cloud rather than taken from the last read, because
    # the Aqara app may have changed it since. A backup of that list is
    # written before anything is sent. The write goes out. A readback a few
    # seconds later decides whether it landed, by rangeId.

    def _zone_sensitivity(self, range_id, label):
        value = next((int(v) for v, text in SENSITIVITY_LABELS.items()
                      if text == label), None)
        if value is None or not _is_range_id(range_id):
            self._refuse(f"zone sensitivity {label!r} for {range_id!r}")
            return
        self._zone_write(KIND_EDIT, {"rangeId": int(range_id),
                                     "sensitivity": value})

    def _zone_set(self, request):
        """A zone from the card: with a rangeId it is an edit, without one a
        create. Only name, blocks, sensitivity and the type's tagId are taken
        from the card; everything else, the type's name included, comes from
        the zone as the cloud holds it or from the tag catalogue."""
        if not isinstance(request, dict):
            self._refuse("zone_set needs a JSON object")
            return
        kind = KIND_CREATE if request.get("rangeId") is None else KIND_EDIT
        self._zone_write(kind, request)

    def _zone_write(self, kind, request):
        did = self.info["did"]
        with self.app.lock:
            try:
                before = self.cloud.list_zones()
                zone = self._build_zone(kind, request, before)
                if kind == KIND_EDIT and zone["regionType"] != next(
                        z.get("regionType") for z in before
                        if z.get("rangeId") == zone.get("rangeId")):
                    kind = KIND_ATTRIBUTE
                validate_zone(zone, did, new=kind == KIND_CREATE)
                self._backup_before(before, "before-write")
                self.cloud.set_zone(wire_zone(zone, new=kind == KIND_CREATE))
            except ZoneError as exc:
                self._refuse(str(exc), exc.code)
                return
            except AqaraRefused as exc:
                self._refuse(str(exc))
                return
            except AqaraError as exc:
                self._cloud_failed(exc)
                return
        self.app.log(f"zone {kind} sent for {zone.get('rangeId', 'a new zone')} "
                 f"({zone['name']}, {len(zone['blockList'])} blocks)")
        self.app.run_in(self._verify_zone, WRITE_SETTLE_SECONDS,
                    kind=kind, zone=zone, before=before)

    def _build_zone(self, kind, request, before):
        if kind == KIND_CREATE:
            # A second interference zone or edge was never captured from the
            # official app, so this app does not make one (owner, 2026-10-30).
            region = request.get("regionType", REGION_DETECTION)
            if region != REGION_DETECTION:
                raise ZoneError(LOCKED_REGIONS.get(region, ERR_BAD_REGION),
                                "only detection zones are created")
            zone = new_zone(self.info["did"], request.get("blockList") or [],
                            request.get("name"), request.get("sensitivity", 1))
            # "Other" on a new zone is sent as the official app sends it, an
            # empty tagId that the server turns into custom_Other.
            if request.get("tagId") not in (None, TAG_OTHER_ID):
                apply_tag(zone, request["tagId"], self.app.zone_tags)
            return zone
        current = next((z for z in before
                        if z.get("rangeId") == request.get("rangeId")), None)
        if current is None:
            raise ZoneError(ERR_RANGE_ID, f"no zone {request.get('rangeId')}")
        region = current.get("regionType")
        if request.get("regionType", region) != region:
            return self._with_attribute(current, request, before)
        zone = dict(current)
        if region in (REGION_EDGE, REGION_ENTRANCE):
            # The Aqara app redraws the edge and the entrance zone and changes
            # nothing else on them (captured 2026-10-31 and 2026-11-01), so a
            # request that changes anything else is refused whole.
            changed = [f for f in ("name", "sensitivity", "tagId")
                       if f in request and request[f] != current.get(f)]
            if changed:
                code = (ERR_EDGE_LOCKED if region == REGION_EDGE
                        else ERR_ENTRANCE_LOCKED)
                raise ZoneError(code, f"only the blocks of {current.get('name')} "
                                      f"change, not {', '.join(changed)}")
            if "blockList" in request:
                zone["blockList"] = request["blockList"]
            return self._entrance_blocks(zone, before)
        for field in ("name", "blockList", "sensitivity"):
            if field in request:
                zone[field] = request[field]
        if "tagId" in request:
            apply_tag(zone, request["tagId"], self.app.zone_tags)
        return zone

    def _with_attribute(self, current, request, before):
        """The zone `current` with the attribute the card asked for, shaped
        as the Aqara app sends that change (zone_model.with_attribute)."""
        region = request["regionType"]
        check_attribute_change(current, region, before)
        zone = with_attribute(current, region, request.get("name"),
                              request.get("sensitivity", 1))
        if "blockList" in request:
            zone["blockList"] = request["blockList"]
        if (region == REGION_DETECTION
                and request.get("tagId") not in (None, TAG_OTHER_ID)):
            apply_tag(zone, request["tagId"], self.app.zone_tags)
        return self._entrance_blocks(zone, before)

    @staticmethod
    def _entrance_blocks(zone, before):
        """An entrance zone without the interference zone's blocks, which the
        Aqara app leaves out before it sends one (captured 2026-11-01)."""
        if zone.get("regionType") != REGION_ENTRANCE:
            return zone
        zone["blockList"] = without_blocks_of(zone, REGION_INTERFERENCE, before)
        if not zone["blockList"]:
            raise ZoneError(ERR_NO_BLOCKS, "every block of the entrance zone "
                                           "is in the interference zone")
        return zone

    def _zone_delete(self, range_id):
        if not _is_range_id(range_id):
            self._refuse(f"zone_delete needs a rangeId, got {range_id!r}")
            return
        rid = int(range_id)
        with self.app.lock:
            try:
                before = self.cloud.list_zones()
                target = next((z for z in before if z.get("rangeId") == rid),
                              None)
                if target is None:
                    raise ZoneError(ERR_RANGE_ID, f"no zone {rid}")
                locked = LOCKED_REGIONS.get(target.get("regionType"))
                if locked:
                    raise ZoneError(locked, f"{target.get('name')} is not "
                                            "deleted")
                self._backup_before(before, "before-write")
                self.cloud.delete_zones([rid])
            except ZoneError as exc:
                self._refuse(str(exc), exc.code)
                return
            except AqaraError as exc:
                self._cloud_failed(exc)
                return
        self.app.log(f"zone {rid} ({target.get('name')}) delete sent")
        self.app.run_in(self._verify_zone, WRITE_SETTLE_SECONDS,
                    kind=KIND_DELETE, zone=target, before=before)

    def _restore(self, name):
        """Put the zones back as one backup holds them.

        Changed zones are rewritten in place, zones the backup lacks are
        deleted, and zones the device lacks are created again, which gives
        them new ids. An entrance zone or edge that appeared or went away
        since the backup is put back by changing the attribute of the zone
        that shares the most blocks with it (zone_model.plan_restore). A
        restore that would have to create or delete the interference zone,
        an entrance zone or the edge is refused whole, for the same reason a
        create of one is.
        """
        folder = self.info["backup_dir"]
        with self.app.lock:
            try:
                target = read_backup(folder, name)["zones"]
                before = self.cloud.list_zones()
                plan = plan_restore(before, target)
                now = {z.get("rangeId"): z for z in before}
                moved = ([z.get("regionType") for z in plan["create"]]
                         + [now[r].get("regionType") for r in plan["delete"]])
                locked = next((LOCKED_REGIONS[m] for m in moved
                               if m in LOCKED_REGIONS), None)
                if locked:
                    raise ZoneError(locked, "the restore would create or "
                                            "delete the interference zone "
                                            "or the edge")
                converts = plan["to_detection"] + plan["to_single"]
                if not (converts or plan["update"] or plan["create"]
                        or plan["delete"]):
                    self.app.log(f"restore {name}: the zones already match")
                    self._set_status(STATUS_OK)
                    return
                did = self.info["did"]
                for zone in [z for _r, z in converts] + plan["update"]:
                    validate_zone(zone, did)
                for zone in plan["create"]:
                    validate_zone(zone, did, new=True)
                self._backup_before(before, "before-restore")
                # An entrance zone or edge becomes detection first, so its
                # attribute is free before another zone takes it.
                for _rid, zone in converts:
                    self.cloud.set_zone(wire_zone(zone))
                for zone in plan["update"]:
                    self.cloud.set_zone(wire_zone(zone))
                if plan["delete"]:
                    self.cloud.delete_zones(plan["delete"])
                for zone in plan["create"]:
                    self.cloud.set_zone(wire_zone(zone, new=True))
            except ZoneError as exc:
                self._refuse(str(exc), exc.code)
                return
            except AqaraRefused as exc:
                # The client refused a request, possibly after part of the
                # plan was sent, so the device is read back as it now is.
                self._refuse(str(exc))
                self.app.run_in(self.read_all, WRITE_SETTLE_SECONDS)
                return
            except (OSError, ValueError) as exc:
                self._refuse(f"backup {name!r}: {exc}")
                return
            except AqaraError as exc:
                # Part of the plan may have been sent. Reading back shows the
                # device as it now is, which is what the owner needs to see.
                self._cloud_failed(exc)
                self.app.run_in(self.read_all, WRITE_SETTLE_SECONDS)
                return
        self.app.log(f"restore {name}: {len(converts)} changed attribute, "
                 f"{len(plan['update'])} rewritten, "
                 f"{len(plan['delete'])} deleted, {len(plan['create'])} "
                 "created")
        self.app.run_in(self._verify_zone, WRITE_SETTLE_SECONDS,
                    kind=KIND_RESTORE, zone={"backup": name, "zones": target},
                    before=before)

    def _verify_zone(self, kwargs):
        if not self.read_all():
            return
        kind, zone, before = kwargs["kind"], kwargs["zone"], kwargs["before"]
        if kind == KIND_EDIT:
            landed = zone_landed(zone, self.zones)
        elif kind == KIND_ATTRIBUTE:
            landed = attribute_landed(zone, self.zones) is not None
        elif kind == KIND_CREATE:
            landed = created_zone(zone, before, self.zones) is not None
        elif kind == KIND_DELETE:
            landed = all(z.get("rangeId") != zone["rangeId"]
                         for z in self.zones)
        else:
            landed = same_layout(zone["zones"], self.zones)
        if not landed:
            self.app.log(f"zone {kind} did not land", level="WARNING")
            self._set_status(STATUS_ZONE_WRITE_FAILED,
                             {"kind": kind, "rangeId": zone.get("rangeId"),
                              "backup": zone.get("backup")})

    def _backup_before(self, zones, reason):
        folder = self.info["backup_dir"]
        write_backup(folder, zones, self.app._now(), reason)
        prune_backups(folder, self.app.backups_keep)

    def _refuse(self, reason, code=None):
        self.app.log(f"refused: {reason}", level="WARNING")
        # The entities are written again with the status, so a select in
        # Home Assistant goes back to the value the device holds instead of
        # showing the one that was refused.
        self._set_status(STATUS_REFUSED, {"code": code, "reason": reason})

    def _cloud_failed(self, exc):
        self.last_error = {"endpoint": exc.endpoint, "code": exc.code,
                           "message": exc.message, "at": self.app._now()}
        # Once when the cloud is lost, not on every call while it stays
        # lost; the recovery is logged once by read_all.
        if self.status not in (STATUS_CLOUD_ERROR, STATUS_NO_SESSION):
            self.app.log(f"{self.info['name']}: cloud call failed: {exc}",
                         level="WARNING")
        if isinstance(exc, AqaraAuthError):
            self.app.session_lost()
        else:
            self._set_status(STATUS_CLOUD_ERROR)

    # ------------------------------------------------------------- state
    # What the entities show. They read it on Home Assistant's event loop
    # after `_changed`; every value here is replaced whole, never mutated in
    # place, so a read never sees half of an update. The attributes that need
    # the disk (the backups, the session) are built here on the worker, so
    # the event loop never reads a file.

    def _changed(self):
        self._snapshot()
        self.app.changed(self.key)

    def _snapshot(self):
        self.status_attrs = self.status_attributes()
        self.zones_attrs = self.zones_attributes()

    def _set_status(self, code, detail=None):
        self.status = code
        self.status_detail = detail
        self._changed()

    @property
    def reachable(self):
        """Whether the settings can be read and written now: a session, the
        device on the account, and a cloud that answered the last call."""
        return bool(self.values) and self.status not in (
            STATUS_NO_SESSION, STATUS_NOT_ON_ACCOUNT, STATUS_CLOUD_ERROR)

    @property
    def sensitivity(self):
        """The sensitivity as its label, or None before the first read."""
        return SENSITIVITY_LABELS.get(self.values.get(RID_SENSITIVITY))

    @property
    def approach_distance(self):
        return APPROACH_LABELS.get(self.values.get(RID_APPROACH_DISTANCE))

    @property
    def ai_detection(self):
        """True or False, or None before the first read."""
        ai = self.values.get(RID_AI_MODE)
        return {"1": True, "0": False}.get(ai)

    def zone_sensitivity(self, range_id):
        zone = next((z for z in self.zones
                     if z.get("rangeId") == range_id), None)
        if zone is None:
            return None
        return SENSITIVITY_LABELS.get(str(zone.get("sensitivity")))

    def detection_zones(self):
        return [z for z in self.zones
                if z.get("regionType") == REGION_DETECTION
                and z.get("rangeId") is not None]

    def status_attributes(self):
        return {
            "message": STATUS_TEXT[self.status],
            "detail": self.status_detail,
            "did": self.info["did"],
            # signed_in, account, country and token_expires belong to the one
            # session every device shares. The card decides on signed_in and
            # only shows the account, which a session made by tools/login.py
            # before 2026-10-30 lacks.
            **self.app.session_info(),
            "last_read": _iso(self.last_read),
            "last_error": self.last_error,
            "learning_until": _iso(self.learning_until),
        }

    def zones_attributes(self):
        return {
            "did": self.info["did"],
            "grid": {"columns": COLUMNS, "rows": ROWS},
            # Where the FP2 itself sits on the grid, the point x=0, y=0 of the
            # live positions, so the card draws it where the Aqara app does
            # and it moves with any future mounting transform.
            "sensor": dict(zip(("col", "row"), to_grid(0, 0))),
            "zones": self.zones,
            "tags": self.app.zone_tags,
            "backups": list_backups(self.info["backup_dir"], limit=BACKUPS_SHOWN),
            "last_read": _iso(self.last_read),
        }


def _is_range_id(value):
    """A zone's rangeId, as an int or as digits."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, str) and value.isdigit()


class Fp2Companion:
    """Every FP2 the integration serves, and what they share.

    Home Assistant's side comes from the runtime, a subclass that provides
    `args` (the options), `log(text, level)`, `run_in(callback, seconds,
    **kwargs)`, `run_daily(callback, "HH:MM:SS")`, `cancel_timer(handle,
    silent)`, `find_devices()`, `added(device)` when a device is first
    served, `changed(key)` after a device's state changed (None for the app's
    own state), `live(key, message)` for one live position message and
    `signed_out()` when the Aqara session is gone. A
    timer's callback receives its keyword arguments as one dict, as it did
    under AppDaemon, where this class began.
    """

    def initialize(self):
        a = self.args
        state_dir = a["state_dir"]
        self.session_file = os.path.join(state_dir, SESSION_NAME)
        self.backup_root = os.path.join(state_dir, BACKUP_FOLDER)
        self.backups_keep = int(a.get("backups_keep", 50))
        # Optional: every region has a built-in identity (regions.py).
        self._app_id = a.get("app_id")
        self._app_key = a.get("app_key")
        # Every app identity pair a sign-in may try, beyond the default one
        # (decision 19). From the options, never from the repo (decision 20).
        self._identities = [tuple(p) for p in a.get("app_identities") or []
                            if isinstance(p, (list, tuple)) and len(p) == 2]
        self._manual = a.get("devices") or []
        self._exclude = a.get("exclude") or []
        # A skipped model is logged once per run, not on every retry.
        self._warned_models = set()
        self._now = time.time

        # Every cloud call goes through this lock. The runtime already runs
        # the app's callbacks one at a time, but the live tracker calls the
        # cloud from its own thread, a refresh that interleaved with another
        # call would revoke the token that call carries, and every device
        # shares the one session.
        self.lock = threading.Lock()
        self.devices = {}
        # The zone types the Aqara app offers, shared by every FP2 on the
        # account (zone_model.parse_tags). Empty until the first read.
        self.zone_tags = []
        self._retry = None
        self.session = self._make_cloud("")
        self.tracker = self._make_tracker()
        self.hub_attrs = self.hub_attributes()

        self.run_in(self._check_old_card, 1)
        self.run_daily(self.token_check, a.get("token_check_time", "04:10:00"))
        self.run_in(self.start, 5)

    def _make_cloud(self, did):
        return AqaraCloud(SessionStore(self.session_file), self._app_id,
                          self._app_key, did, identities=self._identities)

    def _check_old_card(self, kwargs=None):
        """Up to version 1.3.1 the app copied the card into Home Assistant's www
        folder and added a dashboard resource for it (decision 22). The card
        is its own HACS repository since then (decision 25), so that copy no
        longer updates. The app says how to remove it and deletes nothing."""
        old = self.args.get("card_dir")
        if old and os.path.isdir(old):
            self.log(f"{old} holds the card an older version of this app "
                     f"installed, and it no longer updates. Install FP2 "
                     f"Companion Card from HACS, then delete the dashboard "
                     f"resource that starts with /local/fp2-companion/ and "
                     f"this folder.", level="WARNING")

    def _make_tracker(self):
        # A lambda, so the tests' replacement of _make_cloud applies.
        return LiveTracker(lambda did: self._make_cloud(did), self._live_out,
                           self.lock, log=self.log)

    def terminate(self):
        """The runtime calls this when the integration unloads or reloads.
        Without it the live socket's thread and loop would survive every
        reload, still subscribed."""
        tracker, self.tracker = getattr(self, "tracker", None), None
        if tracker is not None:
            tracker.shutdown()

    # ------------------------------------------------------------- startup
    def start(self, kwargs=None):
        """Find the devices and read them. Also what rescan runs, so it keeps
        at most one retry pending: a rescan pressed while nothing is found
        replaces the waiting retry instead of starting a second loop."""
        if self._retry is not None:
            # When the retry itself called this, its handle has already
            # fired, and a plain cancel_timer would log an invalid handle
            # (reported in PR #1).
            self.cancel_timer(self._retry, silent=True)
            self._retry = None
        known = set(self.devices)
        found = self.discover()
        self._hub_changed()
        if not found:
            self.log("no FP2 found in Home Assistant yet; trying again later",
                     level="WARNING")
            self._retry = self.run_in(self.start, DISCOVERY_RETRY_SECONDS)
            return
        if not known:
            self.token_check()
        # On a rescan only the devices that were not served yet are read.
        for key, device in self.devices.items():
            if key not in known:
                device.read_all()

    def discover(self):
        """Add every FP2 not served yet. A device that has gone from Home
        Assistant keeps its entities, because removing them would also remove
        the dashboard rows a person set up."""
        try:
            # Every Aqara PS device Home Assistant knows over HomeKit, with
            # its model; parse_discovery decides which of them are FP2s.
            found = self.find_devices()
        except Exception as exc:              # noqa: BLE001
            # Anything at all: at startup Home Assistant may still be coming
            # up, and an exception here would also skip the retry in start().
            self.log(f"FP2 discovery failed: {type(exc).__name__}: {exc}",
                     level="WARNING")
            found = []
        for model, name in unknown_fp2_models(found).items():
            if model not in self._warned_models:
                self._warned_models.add(model)
                self.log(f"skipped {name}: HomeKit model {model} is not a "
                         f"known FP2 model ({', '.join(FP2_MODELS)}). If it "
                         "is an FP2, please open an issue with the model.",
                         level="WARNING")
        for info in parse_discovery(found, self._manual, self._exclude):
            if info["key"] not in self.devices:
                device = Fp2Device(self, info)
                self.devices[info["key"]] = device
                self.added(device)
                self.log(f"serving {info['name']} ({info['did']})")
        return list(self.devices.values())

    # ------------------------------------------------------------- session
    def token_check(self, kwargs=None):
        """Refresh the token when it expires within three days.

        Runs daily and at startup. It costs a network call only when a refresh
        is due, so on most days it reads a file and nothing else.
        """
        with self.lock:
            try:
                if self.session.ensure_fresh():
                    self.log("cloud token refreshed")
            except AqaraError as exc:
                if isinstance(exc, AqaraAuthError):
                    self.session_lost()
                self.log(f"token check failed: {exc}", level="WARNING")
                return
        for device in self.devices.values():
            device._changed()
        # The hub carries the expiry too, and a refresh just moved it.
        self._hub_changed()

    def session_lost(self):
        for device in self.devices.values():
            device.values = {}
            device._set_status(STATUS_NO_SESSION)
        self._hub_changed()
        # Home Assistant asks for the password again (the reauth flow).
        self.signed_out()

    def session_info(self):
        """The shared session, from ONE read of the file: every device's
        status carries it, so N devices would otherwise read it 4N times."""
        try:
            session = self.session.store.load() or {}
        except (OSError, ValueError) as exc:
            self.log(f"session file not readable: {exc}", level="WARNING")
            session = {}
        token = session.get("token")
        return {"signed_in": bool(token),
                "account": mask_account(session.get("account")) if token else None,
                "country": session.get("country"),
                "token_expires": _iso(jwt_exp(token)) if token else None}

    def rescan(self):
        self.start()

    # ------------------------------------------------------------- hub
    def _hub_changed(self):
        self.hub_attrs = self.hub_attributes()
        self.changed(None)

    def hub_attributes(self):
        """What the app itself shows: the devices for the card's dropdown
        and the session."""
        devices = [{k: d.info[k] for k in ("key", "did", "name", "prefix",
                                            "area")}
                   for d in self.devices.values()]
        return {"devices": devices, **self.session_info()}

    def _live_out(self, did, message):
        """One live message, from the tracker's publish thread. It goes to
        the cards that watch this device and is never stored."""
        device = next((d for d in self.devices.values()
                       if d.info["did"] == did), None)
        if device is not None:
            self.live(device.key, message)


def mask_account(account):
    """Enough of an account to recognise it on the card, not to read it off a
    screenshot: "j.walker@example.com" becomes "j.w****@example.com"."""
    if not account:
        return None
    local, _, domain = str(account).partition("@")
    shown = local[:3] + "*" * max(1, len(local) - 3)
    return f"{shown}@{domain}" if domain else shown


def _iso(epoch):
    if epoch is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))
