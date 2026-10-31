"""Live target positions of an FP2, while someone is looking at them.

The Aqara app draws a dot on the zone map for every target the FP2 tracks.
The cloud pushes those targets (rid 4.22.700) over a websocket, about one
message every 0.75 s, but only to a socket that has subscribed to the
device's view. This module holds that socket for the app and hands every
push to a `publish` callback, which the app puts on an MQTT topic for the
card. How the Aqara app does it is described in docs/PROTOCOL.md.

Two things are needed for a push, and the app's H5 page does both: the
subscription, and the device page's heartbeat (rid 4.22.85 written as 1
every 60 s), without which the FP2 sends nothing at all.

The socket is open only while a card holds a lease (owner's decision,
2026-10-31). A card renews its lease every 30 s; when the last lease of a
device runs out the device is unsubscribed, and when no device is left the
socket is closed. Nothing here polls.

aiohttp is asyncio and AppDaemon calls the app from worker threads, so the
tracker owns a private event loop on a daemon thread. The cloud calls are blocking and run on a separate worker thread under
the app's cloud lock, because a token refresh revokes the token any other
call is carrying. Publishing runs on one more single worker, which keeps the
order of the pushes and keeps AppDaemon's call out of the socket loop.
"""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from aqara_cloud import (RID_POSITIONS, VIEW_HEARTBEAT_SECONDS, AqaraAuthError,
                         AqaraError)

# A lease outlives one missed renewal from a card that renews every 30 s.
LEASE_SECONDS = 75
# The app re-subscribes every five minutes, so the cloud presumably ends a
# view subscription some time after that.
RESUBSCRIBE_SECONDS = 300
# How often the socket loop looks at the leases while no message arrives.
TICK_SECONDS = 1.0
CONNECT_TIMEOUT = 10
PING_SECONDS = 30
RETRY_MIN_SECONDS = 5
RETRY_MAX_SECONDS = 60
SHUTDOWN_TIMEOUT = 5

# The map transform of the app's H5 page (function K4, wall mount): the grid
# is 16 by 20 blocks of 50 units, and the point is turned by 180 degrees.
UNITS_PER_BLOCK = 50
GRID_HALF_WIDTH = 8

TARGET_PERSON = 0
TARGET_INTERFERENCE = 1

# What the `state` field of a published message says.
LIVE_STARTING = "starting"
# Subscribed and waiting for the FP2. It sends no position pushes while it
# tracks nobody, so this is also what an empty room looks like (2026-10-31).
LIVE_WATCHING = "watching"
LIVE_ON = "live"
LIVE_STOPPED = "stopped"
LIVE_RETRYING = "retrying"
LIVE_NO_SESSION = "no_session"
LIVE_REFUSED = "refused"


def to_grid(x, y):
    """A target's place on the card's grid, in blocks from the top left."""
    return GRID_HALF_WIDTH - x / UNITS_PER_BLOCK, y / UNITS_PER_BLOCK


def live_targets(value):
    """The targets a push or a query reports as present, from its value.

    The value is a JSON list of 20 slots, or the list itself. A slot is live
    when its `state` is "1"; the rest are zeros.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    out = []
    for slot in value:
        if not isinstance(slot, dict) or str(slot.get("state")) != "1":
            continue
        try:
            x, y = float(slot.get("x")), float(slot.get("y"))
        except (TypeError, ValueError):
            continue
        col, row = to_grid(x, y)
        zone = slot.get("rangeId")
        out.append({"id": slot.get("id"),
                    "type": slot.get("targetType", TARGET_PERSON),
                    "x": x, "y": y,
                    "zone": zone if isinstance(zone, int) else None,
                    "col": round(col, 2), "row": round(row, 2)})
    return out


def positions_from(message):
    """Every (did, timestamp, targets) a socket message carries."""
    items = message if isinstance(message, list) else [message]
    for item in items:
        result = item.get("result") if isinstance(item, dict) else None
        if not isinstance(result, dict) or result.get("attr") != RID_POSITIONS:
            continue
        yield (result.get("subjectId"), result.get("timeStamp"),
               live_targets(result.get("value")))


def _aiohttp_connect(url, headers):
    import aiohttp

    class _Socket:
        """aiohttp's session and socket as one async context manager."""

        async def __aenter__(self):
            self.http = aiohttp.ClientSession()
            try:
                self.ws = await self.http.ws_connect(
                    url, headers=headers, heartbeat=PING_SECONDS,
                    timeout=CONNECT_TIMEOUT)
            except BaseException:
                await self.http.close()
                raise
            return self

        async def __aexit__(self, *exc):
            try:
                await self.ws.close()
            finally:
                await self.http.close()

        async def receive_text(self, timeout):
            """The next text message, None on a quiet tick, and an error
            when the socket closed."""
            try:
                msg = await self.ws.receive(timeout=timeout)
            except asyncio.TimeoutError:
                return None
            if msg.type == aiohttp.WSMsgType.TEXT:
                return msg.data
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                raise ConnectionError(f"push socket {msg.type.name.lower()}")
            return None

    return _Socket()


class LiveTracker:
    """One push socket for every FP2 somebody is watching.

    `cloud_for(did)` gives an AqaraCloud for a device, `publish(did, message)`
    sends a message to that device's topic, and `lock` is the app's cloud lock.
    `connect(url, headers)` and `clock` are for the tests.
    """

    def __init__(self, cloud_for, publish, lock, log=None, clock=time.time,
                 connect=_aiohttp_connect):
        self._cloud_for = cloud_for
        self._publish = publish
        self._cloud_lock = lock
        self._log = log or (lambda *a, **k: None)
        self._clock = clock
        self._connect = connect
        self._guard = threading.Lock()
        self._leases = {}          # did -> {client: until}
        self._loop = None
        self._thread = None
        self._runner = None
        self._http = None
        self._out = None
        self._beats = {}
        self._last_state = {}      # did -> the last message that was not a push

    # ---------- what the app calls, from any thread ----------
    def lease(self, did, client, seconds=LEASE_SECONDS):
        with self._guard:
            fresh = not self._live_clients(did)
            clients = self._leases.setdefault(did, {})
            joined = str(client) not in clients
            clients[str(client)] = self._clock() + seconds
        self._ensure_running()
        if fresh:
            self._emit(did, {"state": LIVE_STARTING})
        elif joined and did in self._last_state:
            # A card that opens on a device already being watched gets the
            # state again, or it waits for a push that an empty room never
            # sends.
            self._emit(did, self._last_state[did])

    def release(self, did, client):
        """A card that closed. The device stops at the next tick when that
        was its last lease."""
        with self._guard:
            self._leases.get(did, {}).pop(str(client), None)

    def watched(self):
        """The devices with at least one live lease. Expired ones go."""
        with self._guard:
            return {did for did in list(self._leases)
                    if self._live_clients(did)}

    def shutdown(self):
        """Called from the app's terminate(). Closing the socket ends its
        subscriptions on the cloud side as well."""
        with self._guard:
            self._leases.clear()
        loop, thread = self._loop, self._thread
        self._loop = self._thread = None
        if loop is not None:
            # The runner's own cleanup unsubscribes and closes the socket, so
            # it is cancelled and awaited before the loop stops.
            try:
                asyncio.run_coroutine_threadsafe(self._stop_runner(), loop
                                                 ).result(SHUTDOWN_TIMEOUT + 1)
            except Exception as exc:      # noqa: BLE001
                self._log(f"live shutdown: {type(exc).__name__}",
                          level="DEBUG")
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=SHUTDOWN_TIMEOUT)
            try:
                loop.close()
            except RuntimeError:
                pass
        for pool in (self._http, self._out):
            if pool is not None:
                pool.shutdown(wait=False)
        self._http = self._out = None

    # ---------- internals ----------
    def _live_clients(self, did):
        """Called with the guard held."""
        now = self._clock()
        clients = self._leases.get(did, {})
        for client in [c for c, until in clients.items() if until <= now]:
            del clients[client]
        if not clients:
            self._leases.pop(did, None)
        return bool(clients)

    def _ensure_running(self):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._http = ThreadPoolExecutor(1, "fp2_live_cloud")
            self._out = ThreadPoolExecutor(1, "fp2_live_publish")
            self._thread = threading.Thread(target=self._loop.run_forever,
                                            name="fp2_live", daemon=True)
            self._thread.start()
        asyncio.run_coroutine_threadsafe(self._start_runner(), self._loop)

    async def _stop_runner(self):
        runner = self._runner
        if runner is not None and not runner.done():
            runner.cancel()
            await asyncio.wait([runner], timeout=SHUTDOWN_TIMEOUT)

    async def _start_runner(self):
        if self._runner is None or self._runner.done():
            self._runner = asyncio.ensure_future(self.run())

    def _emit(self, did, message):
        if message.get("state") != LIVE_ON:
            self._last_state[did] = message
        if self._out is not None:
            self._out.submit(self._safe_publish, did, message)
        else:
            self._safe_publish(did, message)

    def _safe_publish(self, did, message):
        try:
            self._publish(did, message)
        except Exception as exc:          # noqa: BLE001
            self._log(f"live publish failed: {type(exc).__name__}: {exc}",
                      level="WARNING")

    async def _cloud(self, did, method, *args):
        def call():
            with self._cloud_lock:
                return getattr(self._cloud_for(did), method)(*args)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._http, call)

    async def run(self):
        """Hold the socket while anyone watches; reconnect with a backoff
        when it fails. Ends when the last lease has run out."""
        delay = RETRY_MIN_SECONDS
        while self.watched():
            try:
                await self._session()
                delay = RETRY_MIN_SECONDS
            except asyncio.CancelledError:
                raise
            except AqaraAuthError:
                self._log("live positions stopped: no session",
                          level="WARNING")
                for did in self.watched():
                    self._emit(did, {"state": LIVE_NO_SESSION})
                with self._guard:
                    self._leases.clear()
                return
            except Exception as exc:          # noqa: BLE001
                reason = (f"code {exc.code}" if isinstance(exc, AqaraError)
                          else type(exc).__name__)
                self._log(f"live positions: {reason}; retrying in {delay} s",
                          level="WARNING")
                for did in self.watched():
                    self._emit(did, {"state": LIVE_RETRYING, "reason": reason,
                                     "code": getattr(exc, "code", None)})
                waited = 0.0
                while waited < delay and self.watched():
                    await asyncio.sleep(TICK_SECONDS)
                    waited += TICK_SECONDS
                delay = min(delay * 2, RETRY_MAX_SECONDS)

    async def _session(self):
        """One socket from open to close."""
        anyone = next(iter(self.watched()), None)
        if anyone is None:
            return
        url, headers = await self._cloud(anyone, "push_socket")
        subscribed = {}            # did -> when it was (re)subscribed
        self._beats = {}           # did -> when its heartbeat was written
        client_id = None
        opened = self._clock()
        async with self._connect(url, headers) as ws:
            try:
                while True:
                    wanted = self.watched()
                    if client_id is not None:
                        await self._sync(client_id, subscribed, wanted)
                    elif self._clock() - opened > CONNECT_TIMEOUT:
                        raise ConnectionError("push socket sent no client id")
                    if not wanted:
                        return
                    text = await ws.receive_text(TICK_SECONDS)
                    if text is None:
                        continue
                    try:
                        message = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(message, dict) and "uniqueKey" in message:
                        client_id = message.get("uniqueKey")
                        continue
                    for did, stamp, targets in positions_from(message):
                        if did in subscribed:
                            self._emit(did, {"state": LIVE_ON, "ts": stamp,
                                             "targets": targets})
            finally:
                if client_id is not None:
                    for did in list(subscribed):
                        await self._quietly(did, "view_unsubscribe",
                                            client_id)
                for did in subscribed:
                    if did not in self.watched():
                        self._emit(did, {"state": LIVE_STOPPED})

    async def _sync(self, client_id, subscribed, wanted):
        """Subscribe new devices, drop ones nobody watches, and renew every
        RESUBSCRIBE_SECONDS the way the app does (unsubscribe, subscribe)."""
        now = self._clock()
        for did in [d for d in subscribed if d not in wanted]:
            del subscribed[did]
            await self._quietly(did, "view_unsubscribe", client_id)
            self._emit(did, {"state": LIVE_STOPPED})
        for did in wanted:
            since = subscribed.get(did)
            if since is not None and now - since < RESUBSCRIBE_SECONDS:
                continue
            if since is not None:
                await self._quietly(did, "view_unsubscribe", client_id)
            try:
                await self._cloud(did, "view_subscribe", client_id)
            except AqaraAuthError:
                raise
            except AqaraError as exc:
                # A device this account cannot see (706, 755) is refused on
                # its own; the socket keeps serving the others.
                self._log(f"live subscribe for {did} refused: code "
                          f"{exc.code}", level="WARNING")
                self._emit(did, {"state": LIVE_REFUSED, "code": exc.code})
                with self._guard:
                    self._leases.pop(did, None)
                continue
            if since is None:
                self._emit(did, {"state": LIVE_WATCHING})
            subscribed[did] = now
        for did in subscribed:
            if now - self._beats.get(did, -VIEW_HEARTBEAT_SECONDS) \
                    >= VIEW_HEARTBEAT_SECONDS:
                self._beats[did] = now
                await self._quietly(did, "view_heartbeat")

    async def _quietly(self, did, method, *args):
        try:
            await self._cloud(did, method, *args)
        except Exception as exc:          # noqa: BLE001
            self._log(f"live {method} for {did}: {type(exc).__name__}",
                      level="DEBUG")
