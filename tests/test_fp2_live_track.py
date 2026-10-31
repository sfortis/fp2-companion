"""The live position tracker: target parsing, leases and the socket loop.

The socket and the cloud are fakes and the loop runs in the test's own event
loop, so nothing here opens a connection or a thread.
"""
import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "apps" / "fp2_companion"))
import live_track  # noqa: E402
from aqara_cloud import AqaraAuthError, AqaraError  # noqa: E402
from live_track import (LIVE_NO_SESSION, LIVE_ON, LIVE_REFUSED,  # noqa: E402
                        LIVE_RETRYING, LIVE_STOPPED, LiveTracker, live_targets,
                        positions_from, to_grid)

DID = "lumi1.54ef44a1b2c3"
OTHER = "lumi1.54ef44d4e5f6"


def slot(i, x=0, y=0, state="0", zone="", kind=0):
    return {"rangeId": zone, "x": x, "y": y, "targetType": kind, "id": i,
            "state": state}


# The shape of the first push captured on 2026-10-30: one person in zone 2.
VALUE = json.dumps([slot(9, 40, 518, "1", 2)] + [slot(i) for i in range(19)])


def push(did, value=VALUE, stamp=1793382792080):
    return json.dumps([{"result": {"attr": "4.22.700", "subjectId": did,
                                   "timeStamp": stamp, "value": value,
                                   "model": "lumi.motion.agl001"}}])


# ----------------------------------------------------------------- parsing
def test_the_map_transform_is_the_apps():
    # col = 8 - x/50, row = y/50, from the H5 page's K4 for a wall mount.
    assert to_grid(0, 0) == (8, 0)
    assert to_grid(400, 1000) == (0, 20)
    assert to_grid(-400, 0) == (16, 0)


def test_only_live_slots_are_targets_and_they_land_in_their_zone():
    targets = live_targets(VALUE)
    assert targets == [{"id": 9, "type": 0, "x": 40.0, "y": 518.0, "zone": 2,
                        "col": 7.2, "row": 10.36}]
    # Zone 2 of the Living Room covered columns 7 to 9, rows 10 to 15.
    assert 7 <= targets[0]["col"] < 10 and 10 <= targets[0]["row"] < 16


def test_an_interference_source_keeps_its_type_and_has_no_zone():
    value = [slot(1, -100, 300, "1", "", 1)]
    assert live_targets(value)[0]["type"] == 1
    assert live_targets(value)[0]["zone"] is None


@pytest.mark.parametrize("value", [None, "", "not json", "{}", 7,
                                   [{"state": "1", "x": "?", "y": 1}]])
def test_a_value_that_is_not_a_slot_list_gives_no_targets(value):
    assert live_targets(value) == []


def test_a_message_of_another_rid_carries_no_positions():
    other = json.loads(push(DID))
    other[0]["result"]["attr"] = "3.51.85"
    assert list(positions_from(other)) == []
    assert list(positions_from(json.loads(push(DID))))[0][0] == DID


# ----------------------------------------------------------------- leases
class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def tracker(clock=None, cloud=None, sockets=()):
    published = []
    clock = clock or Clock()
    cloud = cloud or FakeCloud()
    t = LiveTracker(lambda did: cloud.bind(did), lambda did, m:
                    published.append((did, m)), threading.Lock(),
                    clock=clock, connect=FakeConnect(list(sockets), clock))
    t._ensure_running = lambda: None      # the tests drive run() themselves
    return t, published, cloud


def test_a_lease_runs_out_unless_renewed():
    clock = Clock()
    t, published, _ = tracker(clock)
    t.lease(DID, "tab-1", 75)
    assert t.watched() == {DID}
    assert published == [(DID, {"state": "starting"})]
    clock.now += 70
    t.lease(DID, "tab-1", 75)
    clock.now += 70
    assert t.watched() == {DID}
    clock.now += 10
    assert t.watched() == set()


def test_one_tab_closing_does_not_stop_another():
    t, _, _ = tracker()
    t.lease(DID, "tab-1")
    t.lease(DID, "tab-2")
    t.release(DID, "tab-1")
    assert t.watched() == {DID}
    t.release(DID, "tab-2")
    assert t.watched() == set()


# ----------------------------------------------------------------- the loop
class FakeCloud:
    def __init__(self):
        self.calls = []
        self.refuse = {}           # did -> exception for view_subscribe
        self.socket_error = None
        self.did = None

    def bind(self, did):
        self.did = did
        return self

    def push_socket(self):
        self.calls.append(("push_socket", self.did))
        if self.socket_error:
            raise self.socket_error
        return "wss://x/push", {"Token": "t"}

    def view_subscribe(self, client):
        self.calls.append(("subscribe", self.did, client))
        if self.did in self.refuse:
            raise self.refuse[self.did]

    def view_unsubscribe(self, client):
        self.calls.append(("unsubscribe", self.did, client))

    def view_heartbeat(self):
        self.calls.append(("heartbeat", self.did))


def kinds(cloud, drop=("heartbeat",)):
    return [c[0] for c in cloud.calls if c[0] not in drop]


class FakeSocket:
    """Plays a script: a text, None for a quiet tick, a number to move the
    clock on, or an exception to raise. After the script it moves the clock
    past every lease, which ends the loop."""

    def __init__(self, script, clock):
        self.script = list(script)
        self.clock = clock
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def receive_text(self, timeout):
        await asyncio.sleep(0)
        if not self.script:
            self.clock.now += 10_000
            return None
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, (int, float)):
            self.clock.now += step
            return None
        return step


class FakeConnect:
    def __init__(self, scripts, clock):
        self.scripts = scripts
        self.clock = clock
        self.opened = []

    def __call__(self, url, headers):
        sock = FakeSocket(self.scripts.pop(0), self.clock)
        self.opened.append(sock)
        return sock


HELLO = json.dumps({"success": True, "uniqueKey": "socket-1"})


def run(t):
    asyncio.run(t.run())


def test_a_watched_device_is_subscribed_with_the_socket_key_and_published():
    clock = Clock()
    t, published, cloud = tracker(clock, sockets=[[HELLO, push(DID),
                                                   push(OTHER)]])
    t.lease(DID, "tab-1")
    run(t)
    assert cloud.calls == [("push_socket", DID),
                           ("subscribe", DID, "socket-1"),
                           ("heartbeat", DID),
                           ("unsubscribe", DID, "socket-1")]
    live = [m for d, m in published if m["state"] == LIVE_ON]
    # The other FP2's push is not this socket's business.
    assert len(live) == 1 and published[1][0] == DID
    assert live[0]["ts"] == 1793382792080
    assert live[0]["targets"][0]["zone"] == 2
    assert published[-1] == (DID, {"state": LIVE_STOPPED})
    assert t._connect.opened[0].closed


def test_nothing_is_subscribed_before_the_socket_names_itself():
    clock = Clock()
    t, published, cloud = tracker(clock, sockets=[[None, push(DID), HELLO]])
    t.lease(DID, "tab-1")
    run(t)
    assert kinds(cloud) == ["push_socket", "subscribe", "unsubscribe"]
    assert not [m for d, m in published if m["state"] == LIVE_ON]


def test_the_subscription_is_renewed_every_five_minutes():
    clock = Clock()
    t, _, cloud = tracker(clock, sockets=[[HELLO, None, 299, None, 2, None]])
    t.lease(DID, "tab-1", 10_000)
    run(t)
    assert kinds(cloud)[:4] == ["push_socket", "subscribe", "unsubscribe",
                                "subscribe"]


def test_a_device_the_account_cannot_see_is_refused_alone():
    clock = Clock()
    cloud = FakeCloud()
    cloud.refuse[OTHER] = AqaraError("app/view/subscribe", 755, "no access")
    t, published, _ = tracker(clock, cloud=cloud,
                              sockets=[[HELLO, push(DID), push(OTHER)]])
    t.lease(DID, "tab-1")
    t.lease(OTHER, "tab-1")
    run(t)
    assert (OTHER, {"state": LIVE_REFUSED, "code": 755}) in published
    assert [m for d, m in published if d == DID and m["state"] == LIVE_ON]


def test_no_session_stops_every_device_and_drops_the_leases():
    clock = Clock()
    cloud = FakeCloud()
    cloud.socket_error = AqaraAuthError("session", None, "no session")
    t, published, _ = tracker(clock, cloud=cloud, sockets=[])
    t.lease(DID, "tab-1")
    run(t)
    assert published[-1] == (DID, {"state": LIVE_NO_SESSION})
    assert t.watched() == set()


def test_a_dropped_socket_is_reopened_after_a_pause(monkeypatch):
    monkeypatch.setattr(live_track, "TICK_SECONDS", 0)
    monkeypatch.setattr(live_track, "RETRY_MIN_SECONDS", 0)
    clock = Clock()
    t, published, cloud = tracker(clock, sockets=[
        [HELLO, ConnectionError("push socket closed")],
        [HELLO, push(DID)]])
    t.lease(DID, "tab-1")
    run(t)
    assert len(t._connect.opened) == 2
    states = [m["state"] for d, m in published]
    assert LIVE_RETRYING in states
    assert states.index(LIVE_RETRYING) < states.index(LIVE_ON)
    # The first socket's subscription is dropped before the second opens.
    assert kinds(cloud)[:4] == ["push_socket", "subscribe", "unsubscribe",
                                "push_socket"]


def test_a_socket_that_never_names_itself_is_given_up(monkeypatch):
    monkeypatch.setattr(live_track, "RETRY_MIN_SECONDS", 0)
    monkeypatch.setattr(live_track, "TICK_SECONDS", 0)
    clock = Clock()
    t, published, cloud = tracker(clock, sockets=[[None, 11], [HELLO]])
    t.lease(DID, "tab-1", 100)
    run(t)
    assert len(t._connect.opened) == 2
    assert any(m["state"] == LIVE_RETRYING for d, m in published)


def test_shutdown_stops_a_running_tracker_and_its_thread():
    """The real thread and loop, with a socket that waits for ever."""
    class Hang(FakeSocket):
        async def receive_text(self, timeout):
            await asyncio.sleep(0.01)

    clock = Clock()
    cloud = FakeCloud()
    t = LiveTracker(lambda did: cloud.bind(did), lambda d, m: None,
                    threading.Lock(), clock=clock,
                    connect=lambda url, headers: Hang([], clock))
    t.lease(DID, "tab-1")
    thread = t._thread
    t.shutdown()
    assert not thread.is_alive()
    assert t.watched() == set()


def test_the_device_page_heartbeat_goes_out_on_subscribe_and_every_minute():
    """Without 4.22.85 the FP2 sends no positions at all (2026-10-31)."""
    clock = Clock()
    t, _, cloud = tracker(clock, sockets=[[HELLO, None, 30, None, 31, None,
                                           None]])
    t.lease(DID, "tab-1", 1_000)
    run(t)
    beats = [c for c in cloud.calls if c[0] == "heartbeat"]
    assert beats == [("heartbeat", DID), ("heartbeat", DID)]
    order = kinds(cloud, drop=())
    assert order.index("subscribe") < order.index("heartbeat")


def test_no_heartbeat_goes_to_a_refused_device():
    clock = Clock()
    cloud = FakeCloud()
    cloud.refuse[DID] = AqaraError("app/view/subscribe", 755, "no access")
    t, _, _ = tracker(clock, cloud=cloud, sockets=[[HELLO]])
    t.lease(DID, "tab-1")
    run(t)
    assert not [c for c in cloud.calls if c[0] == "heartbeat"]
