"""fp2_companion: every Aqara FP2's settings and zones in Home Assistant.

The FP2 reports presence locally over HomeKit and has no local configuration
channel, so its settings go through the Aqara cloud app API (`aqara_cloud.py`).
This app exposes them as MQTT discovery entities and feeds the zone card.
Plan and every owner decision: docs/DESIGN.md

One app serves every FP2 (decision 17). It finds them in Home Assistant as
HomeKit devices of model PS-S02D or PS-T02E, so nothing about a device is
written in the yaml: the cloud device id comes from the serial number, the
entity id prefix from the device's original name (which its HomeKit entity ids
were built from), and the area from the device. A yaml list of serials covers
an FP2 that is not paired over HomeKit. `Fp2Device` holds everything about one
FP2; `Fp2Companion` holds what they share: the discovery, the one cloud
session, the sign-in and the routing of commands.

The app never polls the cloud. It reads at startup, after every write and when
Refresh is pressed, which keeps the calls on the account to what a person
actually did (owner's decision, 2026-10-30). The one stream it does take is
the live target positions, and only while a card holds a lease on them
(`live_track.py`, owner's decision, 2026-10-31).
"""

import json
import os
import re
import threading
import time
import unicodedata

import hassapi as hass

from aqara_cloud import (RID_AI_LEARNING, RID_AI_MODE, RID_APPROACH_DISTANCE,
                         RID_SENSITIVITY, AqaraAuthError, AqaraCloud,
                         AqaraError, AqaraRefused, SessionStore,
                         did_from_serial, jwt_exp)
from live_track import LiveTracker, to_grid
from regions import COUNTRIES
from zone_model import (COLUMNS, ERR_BAD_REGION, ERR_EDGE_LOCKED,
                        ERR_ENTRANCE_LOCKED, ERR_RANGE_ID, LOCKED_REGIONS,
                        REGION_DETECTION, REGION_EDGE, REGION_ENTRANCE, ROWS,
                        TAG_OTHER_ID, TAG_TYPE_ZONE, ZoneError, apply_tag,
                        created_zone, list_backups, new_zone, parse_tags,
                        plan_restore,
                        prune_backups, read_backup, same_layout, validate_zone,
                        wire_zone, write_backup, zone_landed, zones_differ)

STATE_DIR = "/share/appdaemon/fp2_companion"
SESSION_FILE = f"{STATE_DIR}/session.json"
BACKUP_ROOT = f"{STATE_DIR}/backups"
RUNTIME_FILE = f"{STATE_DIR}/runtime.json"

# What a zone write did, carried to its readback. Zones are matched by
# rangeId and never by name (owner, 2026-10-30).
KIND_EDIT = "edit"
KIND_CREATE = "create"
KIND_DELETE = "delete"
KIND_RESTORE = "restore"

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
STATUS_LOGIN_FAILED = "login_failed"
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
    STATUS_NO_SESSION: "Not signed in to Aqara. Sign in on the card.",
    STATUS_LOGIN_FAILED: "Aqara refused the sign-in. The previous session, "
                         "if there was one, is unchanged.",
    STATUS_CLOUD_ERROR: "The last cloud call failed.",
    STATUS_WRITE_NOT_APPLIED: "The cloud accepted the write but the device "
                              "still reports the old value. The AI "
                              "high-precision mode in the Aqara app ignores "
                              "sensitivity writes; check that it is off.",
    STATUS_ZONE_WRITE_FAILED: "The cloud accepted the zone change but the "
                              "zones read back afterwards do not show it.",
    STATUS_REFUSED: "The command was refused.",
}

# Every FP2 Home Assistant knows over HomeKit. `original` is the name the
# integration gave the device, which its entity ids were built from; `name`
# is the one a person may have given it since. An FP2 reports its HomeKit
# model as PS-S02D or, on some units, PS-T02E.
DISCOVERY_TEMPLATE = """{%- set ns = namespace(out=[]) -%}
{%- for d in integration_entities('homekit_controller') | map('device_id')
      | reject('none') | unique -%}
  {%- if device_attr(d, 'model') in ['PS-S02D', 'PS-T02E'] -%}
    {%- set ns.out = ns.out + [{'serial': device_attr(d, 'serial_number'),
        'original': device_attr(d, 'name'),
        'name': device_attr(d, 'name_by_user') or device_attr(d, 'name'),
        'area': area_name(d)}] -%}
  {%- endif -%}
{%- endfor -%}
{{ ns.out | tojson }}"""


def slugify(text):
    """Close to Home Assistant's slugify for the names this app meets."""
    folded = unicodedata.normalize("NFKD", str(text or ""))
    folded = folded.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", folded).strip("_")


def parse_discovery(found, manual=(), exclude=()):
    """The devices to serve, from the discovery rows and the yaml.

    Each is {key, did, name, prefix, area}. The key is the serial in lower
    case and names the device in MQTT topics. A row whose serial is not an
    FP2 serial is skipped, an excluded serial is dropped, and a manual serial
    already found is not added twice.
    """
    skip = {str(s).strip().lower() for s in exclude}
    out = {}
    for row in found or []:
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


class Fp2Device:
    """One FP2: its values, zones, backups, entities, topics and status.

    Shared services (logging, timers, MQTT, the cloud lock, the clock) come
    from the app, so a device never talks to AppDaemon by itself.
    """

    def __init__(self, app, info):
        self.app = app
        self.info = dict(info, backup_dir=os.path.join(BACKUP_ROOT, info["did"]))
        self.base = f"{app.base}/{info['key']}"
        self.cloud = app._make_cloud(info["did"])
        self.values = {}
        self.zones = []
        self.last_read = None
        self.last_error = None
        self.learning_until = None
        self.status = STATUS_STARTING
        self.status_detail = None

    def handle(self, key, payload):
        """One command from this device's topic."""
        if key == "refresh":
            self.read_all(tags=True)
        elif key == "sensitivity":
            self._write_setting(RID_SENSITIVITY, SENSITIVITY_LABELS, payload)
        elif key == "ai_detection":
            self._write_value(RID_AI_MODE,
                              "1" if payload.lower() in ("on", "1") else "0")
        elif key == "ai_learning":
            self._start_learning()
        elif key == "approach_distance":
            self._write_setting(RID_APPROACH_DISTANCE, APPROACH_LABELS, payload)
        elif key.startswith("zone/") and key.endswith("/sensitivity"):
            self._zone_sensitivity(key[len("zone/"):-len("/sensitivity")],
                                   payload)
        elif key == "zone_set":
            self._zone_set(payload)
        elif key == "zone_delete":
            self._zone_delete(payload)
        elif key == "restore":
            self._restore(payload)
        elif key == "preview":
            self._preview(payload)
        elif key == "track":
            self._track(payload)
        else:
            self.app.log(f"unknown command {key!r}", level="WARNING")

    def _track(self, payload):
        """A card's lease on the live positions: `{"client": id, "on": bool}`.
        Each browser tab is its own client, so one tab that closes does not
        stop the dots in another tab watching the same FP2."""
        try:
            request = json.loads(payload)
        except ValueError:
            request = None
        if not isinstance(request, dict) or not request.get("client"):
            self.app.log("track needs {client, on}", level="WARNING")
            return
        client = str(request["client"])[:64]
        if request.get("on", True):
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
        self._publish_zone_selects()
        self._publish_values()
        self._publish_zones()
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
        rows = list_backups(folder)
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
        the cloud reports progress or the end, so the app publishes when the
        run should end and the card counts down to it."""
        with self.app.lock:
            try:
                self.cloud.write_rid(RID_AI_LEARNING, "1")
            except AqaraError as exc:
                self._cloud_failed(exc)
                return
        self.learning_until = self.app._now() + AI_LEARNING_SECONDS
        self.app.log("AI learning started")
        self._publish_status()
        # The end belongs to this run: a second press restarts the count, and
        # the first run's timer must not end the second one early.
        self.app.run_in(self._learning_done, AI_LEARNING_SECONDS,
                        until=self.learning_until)

    def _learning_done(self, kwargs=None):
        if (kwargs or {}).get("until") != self.learning_until:
            return
        self.learning_until = None
        self._publish_status()

    # ------------------------------------------------------------- zones
    #
    # Every zone write follows the same four steps. The zone list is read
    # fresh from the cloud rather than taken from the last publish, because
    # the Aqara app may have changed it since. A backup of that list is
    # written before anything is sent. The write goes out. A readback a few
    # seconds later decides whether it landed, by rangeId.

    def _zone_sensitivity(self, rid_text, label):
        value = next((int(v) for v, text in SENSITIVITY_LABELS.items()
                      if text == label), None)
        if value is None or not rid_text.isdigit():
            self._refuse(f"zone sensitivity {label!r} for {rid_text!r}")
            return
        self._zone_write(KIND_EDIT, {"rangeId": int(rid_text),
                                     "sensitivity": value})

    def _zone_set(self, payload):
        """A zone from the card: with a rangeId it is an edit, without one a
        create. Only name, blocks, sensitivity and the type's tagId are taken
        from the card; everything else, the type's name included, comes from
        the zone as the cloud holds it or from the tag catalogue."""
        try:
            request = json.loads(payload)
        except ValueError:
            request = None
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
        if current.get("regionType") == REGION_ENTRANCE:
            # No write of an entrance zone was captured (PR #1, 2026-10-31).
            raise ZoneError(ERR_ENTRANCE_LOCKED,
                            f"{current.get('name')} is an entrance zone, "
                            "which is not written")
        zone = dict(current)
        if current.get("regionType") == REGION_EDGE:
            # The Aqara app redraws the edge and changes nothing else on it
            # (captured 2026-10-31), so a request that changes anything else
            # is refused whole.
            changed = [f for f in ("name", "sensitivity", "tagId")
                       if f in request and request[f] != current.get(f)]
            if changed:
                raise ZoneError(ERR_EDGE_LOCKED,
                                f"only the edge's blocks change, not "
                                f"{', '.join(changed)}")
            if "blockList" in request:
                zone["blockList"] = request["blockList"]
            return zone
        for field in ("name", "blockList", "sensitivity"):
            if field in request:
                zone[field] = request[field]
        if "tagId" in request:
            apply_tag(zone, request["tagId"], self.app.zone_tags)
        return zone

    def _zone_delete(self, payload):
        if not payload.isdigit():
            self._refuse(f"zone_delete needs a rangeId, got {payload!r}")
            return
        rid = int(payload)
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
        them new ids. A restore that would have to create or delete the
        interference zone or the edge is refused whole, for the same reason
        a create of one is. Their blocks are restored like any other zone's.
        An entrance zone is left as the device holds it.
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
                if not (plan["update"] or plan["create"] or plan["delete"]):
                    self.app.log(f"restore {name}: the zones already match")
                    self._set_status(STATUS_OK)
                    return
                did = self.info["did"]
                for zone in plan["update"]:
                    validate_zone(zone, did)
                for zone in plan["create"]:
                    validate_zone(zone, did, new=True)
                self._backup_before(before, "before-restore")
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
        self.app.log(f"restore {name}: {len(plan['update'])} rewritten, "
                 f"{len(plan['delete'])} deleted, {len(plan['create'])} "
                 "created")
        self.app.run_in(self._verify_zone, WRITE_SETTLE_SECONDS,
                    kind=KIND_RESTORE, zone={"backup": name, "zones": target},
                    before=before)

    def _preview(self, name):
        """Publish one backup's zones so the card can draw them before a
        restore. A sensor of its own, because the zones of every backup
        together would pass the 16 KB Home Assistant keeps per entity."""
        try:
            body = read_backup(self.info["backup_dir"], name)
        except (OSError, ValueError) as exc:
            self._refuse(f"backup {name!r}: {exc}")
            return
        self._pub(f"{self.base}/preview/state", name)
        self._pub(f"{self.base}/preview/attrs", {
            "name": name, "saved_at": body.get("saved_at"),
            "reason": body.get("reason"), "zones": body["zones"]})

    def _verify_zone(self, kwargs):
        if not self.read_all():
            return
        kind, zone, before = kwargs["kind"], kwargs["zone"], kwargs["before"]
        if kind == KIND_EDIT:
            landed = zone_landed(zone, self.zones)
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
        # Republished so a select in Home Assistant goes back to the value the
        # device holds instead of showing the one that was refused.
        self._publish_values()
        self._set_status(STATUS_REFUSED, {"code": code, "reason": reason})

    def _cloud_failed(self, exc):
        self.last_error = {"endpoint": exc.endpoint, "code": exc.code,
                           "message": exc.message, "at": self.app._now()}
        self.app.log(f"{self.info['name']}: cloud call failed: {exc}",
                     level="WARNING")
        if isinstance(exc, AqaraAuthError):
            self.app.session_lost()
        else:
            self._set_status(STATUS_CLOUD_ERROR)

    # ------------------------------------------------------------- publishing
    def _pub(self, topic, payload):
        self.app._pub(topic, payload)

    def _set_status(self, code, detail=None):
        self.status = code
        self.status_detail = detail
        self._publish_status()

    def _publish_status(self):
        self._pub(f"{self.base}/status/state", self.status)
        self._pub(f"{self.base}/status/attrs", {
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
        })

    def _publish_values(self):
        b = self.base
        sens = SENSITIVITY_LABELS.get(self.values.get(RID_SENSITIVITY))
        dist = APPROACH_LABELS.get(self.values.get(RID_APPROACH_DISTANCE))
        if sens:
            self._pub(f"{b}/state/sensitivity", sens)
        if dist:
            self._pub(f"{b}/state/approach_distance", dist)
        ai = self.values.get(RID_AI_MODE)
        if ai in ("0", "1"):
            self._pub(f"{b}/state/ai_detection", "on" if ai == "1" else "off")
        for zone in self._detection_zones():
            label = SENSITIVITY_LABELS.get(str(zone.get("sensitivity")))
            if label:
                self._pub(f"{b}/state/zone/{zone['rangeId']}/sensitivity",
                          label)

    def _publish_zones(self):
        self._pub(f"{self.base}/zones/state", str(len(self.zones)))
        self._pub(f"{self.base}/zones/attrs", {
            "did": self.info["did"],
            "grid": {"columns": COLUMNS, "rows": ROWS},
            # Where the FP2 itself sits on the grid, the point x=0, y=0 of the
            # live positions, so the card draws it where the Aqara app does
            # and it moves with any future mounting transform.
            "sensor": dict(zip(("col", "row"), to_grid(0, 0))),
            "zones": self.zones,
            "tags": self.app.zone_tags,
            "backups": list_backups(self.info["backup_dir"])[:BACKUPS_SHOWN],
            "last_read": _iso(self.last_read),
        })

    def _detection_zones(self):
        return [z for z in self.zones
                if z.get("regionType") == REGION_DETECTION
                and z.get("rangeId") is not None]

    # ------------------------------------------------------------- discovery
    def _device_block(self):
        d = self.info
        block = {"identifiers": [f"fp2_companion_{d['did']}"],
                 "name": f"{d['name']} Settings",
                 "manufacturer": "AppDaemon", "model": "fp2_companion"}
        if d.get("area"):
            block["suggested_area"] = d["area"]
        return block

    def _disc(self, component, key, cfg, entity_key=None):
        """`default_entity_id` pins the id to the HomeKit device's prefix, so
        the entities read as belonging to that FP2 without a registry rename.
        It only acts when an entity is created. `entity_key` replaces `key` in
        the entity id only; the unique id always uses `key`."""
        d = self.info
        object_id = f"fp2_companion_{d['did'].replace('.', '_')}_{key}"
        cfg["unique_id"] = object_id
        cfg["object_id"] = object_id
        cfg["default_entity_id"] = \
            f"{component}.{d['prefix']}_{entity_key or key}"
        cfg["device"] = self._device_block()
        self._pub(f"{self.app.disc}/{component}/{object_id}/config", cfg)
        return object_id

    def _publish_discovery(self):
        b = self.base
        self._disc("select", "sensitivity", {
            "name": "Sensitivity",
            "command_topic": f"{b}/cmd/sensitivity",
            "state_topic": f"{b}/state/sensitivity",
            "options": list(SENSITIVITY_LABELS.values()),
            "icon": "mdi:motion-sensor", "entity_category": "config"})
        self._disc("select", "approach_distance", {
            "name": "Approach Distance",
            "command_topic": f"{b}/cmd/approach_distance",
            "state_topic": f"{b}/state/approach_distance",
            "options": list(APPROACH_LABELS.values()),
            "icon": "mdi:arrow-collapse-horizontal",
            "entity_category": "config"})
        self._disc("switch", "ai_detection", {
            "name": "AI Person Detection",
            "command_topic": f"{b}/cmd/ai_detection",
            "state_topic": f"{b}/state/ai_detection",
            "payload_on": "on", "payload_off": "off",
            "icon": "mdi:account-search", "entity_category": "config"})
        self._disc("button", "ai_learning", {
            "name": "AI Learning", "command_topic": f"{b}/cmd/ai_learning",
            "payload_press": "PRESS", "icon": "mdi:school"})
        self._disc("button", "refresh", {
            "name": "Refresh", "command_topic": f"{b}/cmd/refresh",
            "payload_press": "PRESS", "icon": "mdi:cloud-refresh"})
        self._disc("sensor", "zones", {
            "name": "Zones", "state_topic": f"{b}/zones/state",
            "json_attributes_topic": f"{b}/zones/attrs",
            "icon": "mdi:floor-plan"})
        self._disc("sensor", "backup_preview", {
            "name": "Backup Preview", "state_topic": f"{b}/preview/state",
            "json_attributes_topic": f"{b}/preview/attrs",
            "icon": "mdi:file-restore", "entity_category": "diagnostic"})
        self._disc("sensor", "cloud_status", {
            "name": "Cloud Status", "state_topic": f"{b}/status/state",
            "json_attributes_topic": f"{b}/status/attrs",
            "icon": "mdi:cloud-check", "entity_category": "diagnostic"})

    def _publish_zone_selects(self):
        """One select per detection zone, keyed by its rangeId.

        The key is the rangeId and not the name, so renaming a zone in the
        Aqara app keeps the same entity. A zone that has gone is removed with
        an empty retained config, and the set of published zones is kept on
        disk so that a zone deleted while the app was down is removed too.
        """
        b = self.base
        published = set()
        for zone in self._detection_zones():
            rid = zone["rangeId"]
            slug = slugify(zone.get("name")) or f"zone_{rid}"
            cfg = {"name": f"{zone.get('name')} Sensitivity",
                   "command_topic": f"{b}/cmd/zone/{rid}/sensitivity",
                   "state_topic": f"{b}/state/zone/{rid}/sensitivity",
                   "options": list(SENSITIVITY_LABELS.values()),
                   "icon": "mdi:vector-square", "entity_category": "config"}
            # The entity id carries the zone NAME, which reads better than the
            # rangeId; the unique id keeps the rangeId.
            published.add(self._disc("select", f"zone_{rid}_sensitivity", cfg,
                                     entity_key=f"{slug}_sensitivity"))
        # One list for every device; a device retires only its own selects,
        # which are the object ids that carry its device id.
        mine = f"fp2_companion_{self.info['did'].replace('.', '_')}_"
        runtime = _load_json(RUNTIME_FILE)
        known = set(runtime.get("zone_selects", []))
        for object_id in {o for o in known if o.startswith(mine)} - published:
            self._pub(f"{self.app.disc}/select/{object_id}/config", "")
            self.app.log(f"zone select {object_id} retired")
        runtime["zone_selects"] = sorted(
            {o for o in known if not o.startswith(mine)} | published)
        _save_json(RUNTIME_FILE, runtime)


class Fp2Companion(hass.Hass):

    def initialize(self):
        a = self.args
        self.base = a.get("base_topic", "fp2companion")
        self.disc = a.get("discovery_prefix", "homeassistant")
        self.backups_keep = int(a.get("backups_keep", 50))
        # Optional: every region has a built-in identity (regions.py).
        self._app_id = a.get("app_id")
        self._app_key = a.get("app_key")
        # Every app identity pair a sign-in may try, beyond the default one
        # (decision 19). From secrets.yaml, never from the repo (decision 20).
        self._identities = [tuple(p) for p in a.get("app_identities") or []
                            if isinstance(p, (list, tuple)) and len(p) == 2]
        self._manual = a.get("devices") or []
        self._exclude = a.get("exclude") or []
        self._now = time.time

        # Every cloud call goes through this lock. AppDaemon already runs one
        # app's callbacks on one thread, but a refresh that interleaved with
        # another call would revoke the token that call carries, and every
        # device shares the one session.
        self.lock = threading.Lock()
        self.devices = {}
        # The zone types the Aqara app offers, shared by every FP2 on the
        # account (zone_model.parse_tags). Empty until the first read.
        self.zone_tags = []
        self._retry = None
        self.session = self._make_cloud("")
        self.tracker = self._make_tracker()

        # namespace="mqtt" is REQUIRED: the MQTT plugin publishes its events in
        # its own namespace, and without it the app ignores every command.
        self.listen_event(self._on_cmd, "MQTT_MESSAGE", namespace="mqtt")
        self.run_daily(self.token_check, a.get("token_check_time", "04:10:00"))
        self.run_in(self.start, 5)

    def _make_cloud(self, did):
        return AqaraCloud(SessionStore(SESSION_FILE), self._app_id,
                          self._app_key, did, identities=self._identities)

    def _make_tracker(self):
        # A lambda, so the tests' replacement of _make_cloud applies.
        return LiveTracker(lambda did: self._make_cloud(did), self._live_pub,
                           self.lock, log=self.log)

    def terminate(self):
        """AppDaemon calls this before a reload. Without it the live socket's
        thread and loop would survive every deploy, still subscribed."""
        tracker, self.tracker = self.tracker, None
        if tracker is not None:
            tracker.shutdown()

    # ------------------------------------------------------------- startup
    def start(self, kwargs=None):
        """Find the devices and read them. Also what rescan runs, so it keeps
        at most one retry pending: a rescan pressed while nothing is found
        replaces the waiting retry instead of starting a second loop."""
        if self._retry is not None:
            # When the retry itself called this, AppDaemon has already taken
            # its handle off the schedule, and a plain cancel_timer logs
            # "Invalid callback handle" (reported in PR #1).
            self.cancel_timer(self._retry, silent=True)
            self._retry = None
        known = set(self.devices)
        found = self.discover()
        self._publish_hub()
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
            raw = self.render_template(DISCOVERY_TEMPLATE)
            # AppDaemon hands back a native value when the text parses as a
            # Python literal, and the raw text when it does not (a JSON null).
            found = raw if isinstance(raw, list) else json.loads(raw)
        except Exception as exc:              # noqa: BLE001
            # Anything at all: at startup Home Assistant may still be coming
            # up, and an exception here would also skip the retry in start().
            self.log(f"FP2 discovery failed: {type(exc).__name__}: {exc}",
                     level="WARNING")
            found = []
        for info in parse_discovery(found, self._manual, self._exclude):
            if info["key"] not in self.devices:
                device = Fp2Device(self, info)
                self.devices[info["key"]] = device
                device._publish_discovery()
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
            device._publish_status()
        # The hub carries the expiry too, and a refresh just moved it.
        self._publish_hub()

    def session_lost(self):
        for device in self.devices.values():
            device.values = {}
            device._set_status(STATUS_NO_SESSION)
        self._publish_hub()

    def session_info(self):
        """The shared session, from ONE read of the file: every device's
        status carries it, so N devices would otherwise read it 4N times."""
        session = self.session.store.load() or {}
        token = session.get("token")
        return {"signed_in": bool(token),
                "account": mask_account(session.get("account")) if token else None,
                "country": session.get("country"),
                "token_expires": _iso(jwt_exp(token)) if token else None}

    def _login(self, payload):
        """Sign in from the card (decisions 15, 16 and 19). The payload carries
        the account, the country and the password already encrypted in the
        browser; neither the account nor the block is logged, and only the
        token is kept."""
        try:
            request = json.loads(payload)
        except ValueError:
            request = None
        if not isinstance(request, dict):
            self._refuse_all("login needs a JSON object")
            return
        account = str(request.get("account") or "")
        with self.lock:
            try:
                self.session.login(account,
                                   encrypted_password=request.get("password_rsa"),
                                   country=request.get("country"))
            except AqaraRefused as exc:
                self._refuse_all(str(exc))
                return
            except AqaraError as exc:
                self.log(f"sign-in for {mask_account(account)} refused: "
                         f"code {exc.code}", level="WARNING")
                for device in self.devices.values():
                    device.last_error = {"endpoint": exc.endpoint,
                                         "code": exc.code,
                                         "message": exc.message,
                                         "at": self._now()}
                    device._set_status(STATUS_LOGIN_FAILED,
                                       {"code": exc.code,
                                        "reason": exc.message})
                return
        self.log(f"signed in as {mask_account(account)}")
        self._publish_hub()
        for device in self.devices.values():
            device.last_error = None
            device.read_all()

    def _logout(self):
        with self.lock:
            self.session.logout()
        self.log("signed out; the PhoneId is kept for the next sign-in")
        self.session_lost()

    def _refuse_all(self, reason):
        for device in self.devices.values():
            device._refuse(reason)

    # ------------------------------------------------------------- commands
    def _on_cmd(self, event_name, data, kwargs):
        """`<base>/cmd/<command>` is for the app, `<base>/<key>/cmd/<command>`
        for one device. Every app in this namespace receives every MQTT
        message, so the prefix test keeps another app's commands out."""
        topic = (data or {}).get("topic", "")
        if not topic.startswith(f"{self.base}/"):
            return
        rest = topic[len(self.base) + 1:]
        payload = str((data or {}).get("payload") or "").strip()
        if rest.startswith("cmd/"):
            key = rest[len("cmd/"):]
            if key == "login":
                self._login(payload)
            elif key == "logout":
                self._logout()
            elif key == "rescan":
                self.start()
            else:
                self.log(f"unknown command {key!r}", level="WARNING")
            return
        dkey, _, sub = rest.partition("/")
        device = self.devices.get(dkey)
        if device is None or not sub.startswith("cmd/"):
            return
        device.handle(sub[len("cmd/"):], payload)

    # ------------------------------------------------------------- hub
    def _publish_hub(self):
        """One sensor for the app itself: the devices for the card's
        dropdown, the countries for its sign-in form, and the session."""
        object_id = "fp2_companion_devices"
        self._pub(f"{self.disc}/sensor/{object_id}/config", {
            "name": "Devices", "unique_id": object_id, "object_id": object_id,
            "default_entity_id": "sensor.fp2_companion_devices",
            "state_topic": f"{self.base}/hub/state",
            "json_attributes_topic": f"{self.base}/hub/attrs",
            "icon": "mdi:radar",
            "device": {"identifiers": ["fp2_companion"], "name": "FP2 Companion",
                       "manufacturer": "AppDaemon", "model": "fp2_companion"}})
        devices = [{k: d.info[k] for k in ("key", "did", "name", "prefix",
                                            "area")}
                   for d in self.devices.values()]
        self._pub(f"{self.base}/hub/state", str(len(devices)))
        self._pub(f"{self.base}/hub/attrs", {
            "devices": devices,
            "countries": [[code, name] for code, name, _a in COUNTRIES],
            **self.session_info()})

    def _pub(self, topic, payload):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.call_service("mqtt/publish", topic=topic, payload=text,
                          retain=True)

    def _live_pub(self, did, message):
        """One live message, from the tracker's publish thread. It goes
        straight to the broker through the MQTT plugin and is not retained:
        at more than one a second, a call through Home Assistant would be an
        event row each, and a retained position would be stale on arrival."""
        device = next((d for d in self.devices.values()
                       if d.info["did"] == did), None)
        if device is None:
            return
        self.call_service("mqtt/publish", namespace="mqtt",
                          topic=f"{device.base}/live",
                          payload=json.dumps(message), qos=0, retain=False)


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


def _load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def _save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)
