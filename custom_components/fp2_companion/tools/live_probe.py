"""Probe of the FP2 live-position push, the way the Aqara app does it.

1. Open wss://<host>/app/v1.0/lumi/push/ws/aqarahome with signed headers.
2. Take `uniqueKey` from the first message.
3. POST app/view/subscribe {subjectId, viewId: control, version: 1.8} with
   header Clientid=<uniqueKey>.
4. Listen, then POST app/view/unsubscribe.

Writes nothing to the device. Prints no header, token or user id. It found
the protocol that `live_track.py` implements, and it checks that protocol
without the app or the card:

    docker exec $CONTAINER \
        python3 /config/apps/fp2_companion/tools/live_probe.py <did> [seconds]

`$CONTAINER` is the AppDaemon container. Note that without the device page
heartbeat (4.22.85) the FP2 sends nothing, so this probe sees pushes only
while the app, the card or the Aqara app is writing it.
"""
import asyncio
import json
import secrets
import sys
import time

sys.path.insert(0, "/config/apps/fp2_companion")
import aiohttp  # noqa: E402
import aqara_cloud  # noqa: E402
from aqara_cloud import AqaraCloud, STATIC_HEADERS, SessionStore, sign  # noqa: E402

SESSION_FILE = "/share/appdaemon/fp2_companion/session.json"
# Set by main(). AppDaemon imports every .py under apps/, tools included, so
# nothing may run at import time.
DID = None
LISTEN = 15
BODY = None


def identity(session):
    """The session's pair, else the built-in pair of its region."""
    if session.get("appid") and session.get("appkey"):
        return session["appid"], session["appkey"]
    from regions import identity_for
    return identity_for(session.get("area") or "EU")


def cloud_with_client_id(appid, appkey, client_id):
    def transport(method, url, headers, body, timeout):
        headers = dict(headers, Clientid=client_id)
        return aqara_cloud._urllib_transport(method, url, headers, body, timeout)
    return AqaraCloud(SessionStore(SESSION_FILE), appid, appkey, DID,
                      transport=transport)


def call(cloud, endpoint):
    try:
        res = cloud._call("POST", endpoint, json.dumps(BODY))
        return {"ok": True, "result_type": type(res).__name__,
                "result": res if not isinstance(res, (dict, list)) else
                (sorted(res) if isinstance(res, dict) else len(res))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "message": str(getattr(exc, "message", ""))[:120]}


async def main():
    session = SessionStore(SESSION_FILE).load()
    appid, appkey = identity(session)
    token = session["token"]
    nonce = secrets.token_hex(16)
    now_ms = str(int(time.time() * 1000))
    headers = dict(STATIC_HEADERS)
    headers.update(session.get("headers") or {})
    headers.update({
        "PhoneId": session.get("phone_id", ""),
        "Appid": appid, "Nonce": nonce, "Time": now_ms,
        "Sign": sign(appid, appkey, nonce, now_ms, token, ""),
        "Token": token,
    })
    host = session.get("host") or "https://rpc-ger.aqara.com"
    url = host.replace("https://", "wss://") + "/app/v1.0/lumi/push/ws/aqarahome"
    out = {"did": DID}
    pushes, samples, cloud = 0, [], None
    loop = asyncio.get_running_loop()
    first_at = None
    t0 = time.time()
    try:
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(url, headers=headers, heartbeat=30,
                                       timeout=10) as ws:
                end = time.time() + LISTEN
                while time.time() < end:
                    try:
                        msg = await ws.receive(timeout=end - time.time())
                    except asyncio.TimeoutError:
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        out.setdefault("other", []).append(str(msg.type))
                        if msg.type in (aiohttp.WSMsgType.CLOSED,
                                        aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    data = json.loads(msg.data)
                    if isinstance(data, dict) and "uniqueKey" in data:
                        cloud = cloud_with_client_id(appid, appkey,
                                                     data["uniqueKey"])
                        out["subscribe"] = await loop.run_in_executor(
                            None, call, cloud, "app/view/subscribe")
                        continue
                    for item in (data if isinstance(data, list) else [data]):
                        res = item.get("result") or {}
                        if res.get("attr") != "4.22.700":
                            out.setdefault("other_attrs", []).append(
                                res.get("attr"))
                            continue
                        pushes += 1
                        first_at = first_at or round(time.time() - t0, 2)
                        if len(samples) < 6:
                            targets = json.loads(res["value"])
                            samples.append({
                                "ts": res.get("timeStamp"),
                                "n": len(targets),
                                "live": [t for t in targets
                                         if str(t.get("state")) == "1"]})
                if cloud is not None:
                    out["unsubscribe"] = await loop.run_in_executor(
                        None, call, cloud, "app/view/unsubscribe")
    except aiohttp.WSServerHandshakeError as exc:
        out["handshake_error"] = {"status": exc.status}
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    out.update({"pushes": pushes, "first_push_s": first_at,
                "listen_s": LISTEN, "samples": samples})
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    DID = sys.argv[1]
    LISTEN = float(sys.argv[2]) if len(sys.argv) > 2 else 15
    BODY = {"subjectId": DID, "viewId": "control", "version": "1.8"}
    asyncio.run(main())
