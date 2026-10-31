"""Aqara cloud app API client for an FP2's settings and zones.

The FP2 reports presence locally over HomeKit but has no local configuration
channel, so its settings go through the private API the official Android app
uses. The protocol is described in docs/PROTOCOL.md. This module is
deliberately free of AppDaemon so every request shape can be tested without a
network.

Four rules are built in here, and each one cost a failed round when it was
broken:

1. A written value must be a JSON STRING. `{"14.1.85": 2}` is acknowledged
   with code 0 and never reaches the device; `{"14.1.85": "2"}` does.
2. A successful refresh revokes the old token at once. The new token is
   written to disk BEFORE anything else is sent, or the session is lost and
   only a manual login brings it back.
3. Code 108 means the token expired or was revoked. The answer is one refresh
   and one retry, then a hard failure, never a loop.
4. One stable PhoneId per session file. A login reuses the PhoneId already in
   the file, or the account collects parallel sessions.

The client works on whichever FP2 it is given.

Nothing in this module logs or formats a token, a user id or the app key. An
exception carries the endpoint, the code and the server's message only.
"""

import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from regions import identity_for, region_for

BASE_URL = "https://rpc-ger.aqara.com/app/v1.0/lumi/"
DEFAULT_TIMEOUT = 20

CODE_OK = 0
CODE_TOKEN_EXPIRED = 108
# The answer to a request signed with an app identity this region does not
# accept. A sign-in then tries the next configured pair.
CODE_INVALID_SIGN = 106

# Refresh this long before the token expires. A token lives about 30 days.
REFRESH_MARGIN_SECONDS = 3 * 86400

RID_SENSITIVITY = "14.1.85"           # 1 Low, 2 Medium, 3 High
RID_APPROACH_DISTANCE = "14.47.85"    # 0 Far, 1 Middle, 2 Close
# The Aqara app's "AI person detection" toggle, 1 on and 0 off. Found on
# 2026-10-30 by reading 260 rids with the toggle on, off and on again: this
# was the only one that followed it both ways. While it is 1 the device
# reports 14.1.85 as 3 and ignores writes to it; the real value returns when
# it goes back to 0. The toggle is not logged in the app's xlog at all.
RID_AI_MODE = "4.72.85"
# What the Aqara app sent when the owner started AI learning, captured from
# the xlog the same day. It reads "1" before, during and after a run, and no
# rid nor devex/radar/range/background/query changes while it runs, so the
# app's 60 second countdown is the phone's own and there is nothing to read.
RID_AI_LEARNING = "4.66.85"
# The live targets the Aqara app draws as dots on the map: 20 slots of
# {id, x, y, rangeId, targetType, state}. The cloud pushes it over the push
# socket about every 0.75 s, but only to a socket subscribed to the device's
# "control" view (docs/PROTOCOL.md).
RID_POSITIONS = "4.22.700"

# The push socket and the view it subscribes to. The view id and version are
# what the app's own cache holds for lumi.motion.agl001.
PUSH_SOCKET_PATH = "push/ws/aqarahome"
# The device page's heartbeat. The app's H5 page writes it once on opening
# and then every 60 s; without it the FP2 sends no positions to the cloud,
# subscription or not (found on 2026-10-31, when the dots stopped as soon as
# the emulator left the device page). It is written as the integer 1, the
# way the page writes it, and changes no setting.
RID_VIEW_HEARTBEAT = "4.22.85"
VIEW_HEARTBEAT_SECONDS = 60
VIEW_ID = "control"
VIEW_VERSION = "1.8"

# The only resource ids this client will write, with the values each accepts.
WRITABLE_RIDS = {
    RID_SENSITIVITY: ("1", "2", "3"),
    RID_APPROACH_DISTANCE: ("0", "1", "2"),
    RID_AI_MODE: ("0", "1"),
    RID_AI_LEARNING: ("1",),
}

# The static part of the Aqara Android app's header set. The app sets no
# User-Agent of its own, so the server sees the OkHttp default of the version
# the app ships. Phone-Model is a generic Android phone in the app's
# "<model>##Mobile" form. The country (Cuty) and the region (Area) are not
# here: a sign-in sets both from the country chosen, and the session keeps
# them.
STATIC_HEADERS = {
    "User-Agent": "okhttp/4.12.0",
    "Lang": "en",
    "App-Version": "6.4.3",
    "Phone-Model": "Pixel 8##Mobile",
    "Sys-Type": "1",
    "Sys-Version": "15",
    "ClientId": "",
}

# The public 1024-bit key the app encrypts the password hash with at login.
LOGIN_PUBKEY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCG46slB57013JJs4Vvj5cVyMpR\n"
    "9b+B2F+YJU6qhBEYbiEmIdWpFPpOuBikDs2FcPS19MiWq1IrmxJtkICGurqImRUt\n"
    "4lP688IWlEmqHfSxSRf2+aH0cH8VWZ2OaZn5DWSIHIPBF2kxM71q8stmoYiV0oZs\n"
    "rZzBHsMuBwA4LQdxBwIDAQAB\n"
    "-----END PUBLIC KEY-----\n"
)


class AqaraError(Exception):
    """A call the server answered with a code other than 0, or not at all."""

    def __init__(self, endpoint, code, message):
        super().__init__(f"{endpoint}: code={code} {message}")
        self.endpoint = endpoint
        self.code = code
        self.message = message


class AqaraAuthError(AqaraError):
    """The session is gone: no session file, or a refresh did not save it.

    Recovery is a manual login with `tools/login.py`.
    """


class AqaraRefused(ValueError):
    """This client refused to send a request: a rid or value it does not
    write, or a zone that names a different device than this client's."""


# --------------------------------------------------------------- pure helpers
def did_from_serial(serial):
    """The cloud device id of an FP2, from the serial HomeKit reports.

    HomeKit gives an FP2's serial as `54EF44A1B2C3` and the cloud calls it
    `lumi1.54ef44a1b2c3`.
    """
    text = str(serial or "").strip().lower()
    if len(text) != 12 or any(c not in "0123456789abcdef" for c in text):
        raise ValueError(f"not an FP2 serial: {serial!r}")
    return f"lumi1.{text}"


def sign(appid, appkey, nonce, time_ms, token, payload):
    """MD5 over Appid, Nonce, Time, Token, the payload and the app key.

    `payload` is the JSON body of a POST and the query string of a GET. At
    login there is no token yet and the Token part is left out entirely.
    """
    parts = [f"Appid={appid}", f"Nonce={nonce}", f"Time={time_ms}"]
    if token is not None:
        parts.append(f"Token={token}")
    parts += [payload, appkey]
    return hashlib.md5("&".join(parts).encode()).hexdigest()


def jwt_exp(token):
    """The expiry of a JWT in epoch seconds, or None when it cannot be read."""
    try:
        claims = token.split(".")[1]
        claims += "=" * (-len(claims) % 4)
        exp = json.loads(base64.urlsafe_b64decode(claims)).get("exp")
    except (AttributeError, IndexError, ValueError):
        return None
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return None
    return exp / 1000 if exp > 1e12 else exp


def encrypt_password(password):
    """RSA-PKCS1v1.5 of the password's MD5 hex, base64, as the app sends it.

    `cryptography` is imported here and not at the top, so the AppDaemon app,
    which never logs in, does not depend on it.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_public_key(LOGIN_PUBKEY_PEM.encode())
    digest = hashlib.md5(password.encode()).hexdigest()
    return base64.b64encode(key.encrypt(digest.encode(),
                                        padding.PKCS1v15())).decode()


# --------------------------------------------------------------- the session
class SessionStore:
    """The session file: user id, token, PhoneId and the header values.

    It holds a live credential, so it is created with mode 0600 and replaced
    atomically: written to a temporary file in the same folder, flushed to
    disk, then renamed over the old one. A crash at any point leaves either the
    old session or the new one, never half of each.
    """

    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path) as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None

    def save(self, session):
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, exist_ok=True)
        tmp = f"{self.path}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(session, fh, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


# --------------------------------------------------------------- the client
class _NoRedirect(HTTPRedirectHandler):
    # The API never redirects. Following one would send the signed headers,
    # token included, to wherever the redirect pointed.
    def redirect_request(self, *args, **kwargs):
        return None


def _urllib_transport(method, url, headers, body, timeout):
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as resp:
            return resp.status, resp.read(4 * 1024 * 1024)
    except HTTPError as exc:
        return exc.code, exc.read(64 * 1024)


class AqaraCloud:
    """One FP2 on one session.

    `transport(method, url, headers, body, timeout)` returns `(status, bytes)`.
    The default uses urllib; the tests pass a fake.
    """

    def __init__(self, store, appid, appkey, did, base_url=BASE_URL,
                 transport=None, clock=time.time, timeout=DEFAULT_TIMEOUT,
                 identities=None):
        """`appid`/`appkey` are an optional configured pair and may be None;
        every region has a built-in pair (`regions.identity_for`). A session
        made by a sign-in carries its own host and pair. `identities` are
        further pairs a sign-in may try after the region's own."""
        self.store = store
        self.appid = appid
        self.appkey = appkey
        pairs = [(appid, appkey)] + list(identities or [])
        self.identities = list(dict.fromkeys(p for p in pairs if all(p)))
        self.did = did
        self.base_url = base_url
        self.timeout = timeout
        self._transport = transport or _urllib_transport
        self._clock = clock

    # ------------------------------------------------------------ session
    def _session(self):
        session = self.store.load()
        if not session or not session.get("token") or not session.get("user_id"):
            raise AqaraAuthError("session", None, "no session; run tools/login.py")
        return session

    def token_expires_at(self):
        """Epoch seconds, or None when there is no session or no readable exp."""
        session = self.store.load()
        return jwt_exp(session.get("token")) if session else None

    def needs_refresh(self, margin=REFRESH_MARGIN_SECONDS):
        exp = self.token_expires_at()
        return exp is None or exp - self._clock() < margin

    def ensure_fresh(self, margin=REFRESH_MARGIN_SECONDS):
        """Refresh when the token expires within `margin`. True if it did."""
        if not self.needs_refresh(margin):
            return False
        self.refresh_token()
        return True

    def refresh_token(self):
        """Exchange the current token for a new one and store it at once.

        The server revokes the old token the moment it answers, so the store
        happens before this returns and before any other request is sent.
        """
        session = self._session()
        try:
            result = self._send("POST", "user/refresh-token", "{}", session)
        except AqaraError as exc:
            if exc.code == CODE_TOKEN_EXPIRED:
                raise AqaraAuthError(exc.endpoint, exc.code,
                                     exc.message) from None
            raise
        token = result.get("token") if isinstance(result, dict) else None
        if not isinstance(token, str) or token.count(".") != 2:
            raise AqaraAuthError("user/refresh-token", CODE_OK,
                                 "the answer carried no token")
        session["token"] = token
        session["refreshed_at"] = self._clock()
        self.store.save(session)
        return jwt_exp(token)

    def login(self, account, password=None, encrypted_password=None,
              country=None):
        """Create the session, reusing the PhoneId of an existing file.

        `country` (ISO code) picks the region, its host and the Cuty and
        Area headers, as the Aqara app's sign-in does; without one the
        previous session's country is kept. The first app identity pair the
        region accepts is used and stored with the session.

        Give the password, or `encrypted_password`: the base64 RSA blob of
        its MD5 that the card computes in the browser, so that the clear
        password never leaves it (docs/DESIGN.md, decision 16).
        Either is used for this one request and is not kept anywhere. The
        session file is replaced only when the login succeeds, so a failed
        login leaves the previous session working.
        """
        if (password is None) == (encrypted_password is None):
            raise AqaraRefused("give the password or its encrypted form")
        if not isinstance(account, str) or not account.strip():
            raise AqaraRefused("no account")
        if encrypted_password is not None:
            try:
                blob = base64.b64decode(encrypted_password, validate=True)
            except ValueError:
                blob = b""
            if len(blob) != 128:
                raise AqaraRefused("the encrypted password is not a "
                                   "1024-bit RSA block")
        previous = self.store.load() or {}
        # No default country: it picks the servers, so a sign-in names one,
        # or keeps the previous session's.
        chosen = country or previous.get("country")
        if not chosen:
            raise AqaraRefused("choose the account's country")
        try:
            code, area, host = region_for(chosen)
        except ValueError as exc:
            raise AqaraRefused(str(exc)) from None
        phone_id = previous.get("phone_id") or str(uuid.uuid4()).upper()
        headers = dict(previous.get("headers") or STATIC_HEADERS)
        headers.update({"Cuty": code, "Area": area})
        body = json.dumps({"account": account.strip(), "encryptType": 2,
                           "password": encrypted_password
                           or encrypt_password(password)})
        result = None
        builtin = identity_for(area)
        candidates = list(dict.fromkeys(
            ([builtin] if builtin else []) + self.identities))
        for appid, appkey in candidates:
            session = {"phone_id": phone_id, "headers": headers,
                       "country": code, "area": area, "host": host,
                       "appid": appid, "appkey": appkey}
            try:
                result = self._send("POST", "user/login", body, session,
                                    token=None)
                break
            except AqaraError as exc:
                if exc.code != CODE_INVALID_SIGN:
                    raise
        else:
            raise AqaraError("user/login", CODE_INVALID_SIGN,
                             f"no configured app identity is accepted in "
                             f"region {area}")
        if not isinstance(result, dict) or not result.get("token") \
                or not result.get("userId"):
            raise AqaraAuthError("user/login", CODE_OK,
                                 "the answer carried no session")
        session.update({"user_id": result["userId"], "token": result["token"],
                        "account": account.strip(),
                        "created_at": self._clock(),
                        "refreshed_at": self._clock()})
        self.store.save(session)
        return {"phone_id_reused": bool(previous.get("phone_id")),
                "expires_at": jwt_exp(result["token"])}

    def logout(self):
        """Forget the session but keep the PhoneId, so the next login is the
        same phone to Aqara and the account does not collect sessions."""
        previous = self.store.load() or {}
        kept = {k: previous[k] for k in ("phone_id", "headers", "account",
                                         "country", "area", "host", "appid",
                                         "appkey")
                if k in previous}
        self.store.save(kept)

    def signed_in(self):
        """True while the session file holds a token."""
        session = self.store.load() or {}
        return bool(session.get("token"))

    def account(self):
        """The account of the session on disk, or None. A session made before
        the card could sign in has a token and no account."""
        session = self.store.load() or {}
        return session.get("account") if session.get("token") else None

    # ------------------------------------------------------------- calls
    def query_rids(self, rids):
        """{rid: value} for the requested resource ids. Values are strings."""
        body = json.dumps({"data": [{"options": list(rids),
                                     "subjectId": self.did}]})
        result = self._call("POST", "res/query/by/resourceId", body)
        values = {}
        for item in result or []:
            if isinstance(item, dict) and item.get("subjectId") == self.did:
                values[item.get("resourceId")] = item.get("value")
        return values

    def write_rid(self, rid, value):
        allowed = WRITABLE_RIDS.get(rid)
        if allowed is None:
            raise AqaraRefused(f"rid {rid} is not writable")
        text = str(value)
        if text not in allowed:
            raise AqaraRefused(f"{text!r} is not a value of {rid}")
        body = json.dumps({"subjectId": self.did, "data": {rid: text}})
        self._call("POST", "res/write", body)

    def list_zones(self):
        result = self._call("GET", "devex/radar/range/list",
                            urlencode({"deviceId": self.did}))
        if not isinstance(result, list):
            raise AqaraError("devex/radar/range/list", CODE_OK,
                             f"expected a list, got {type(result).__name__}")
        return result

    def set_zone(self, zone):
        """Send one zone item. The caller validates it with zone_model first.

        With a `rangeId` this rewrites that zone. Without one it creates a
        zone and the server assigns the id, which only a following
        `list_zones` reveals (captured from the official app, 2026-10-30).
        """
        if zone.get("deviceId") != self.did:
            raise AqaraRefused("the zone belongs to another device")
        self._call("POST", "devex/radar/range/set", json.dumps(zone))

    def delete_zones(self, range_ids):
        """Delete zones by rangeId. Deleting is its own endpoint and takes a
        list; a `range/set` with an empty blockList is NOT a delete."""
        ids = list(range_ids)
        if not ids or any(not isinstance(i, int) or isinstance(i, bool)
                          or i <= 0 for i in ids):
            raise AqaraRefused(f"not a list of zone ids: {ids!r}")
        self._call("POST", "devex/radar/range/del",
                   json.dumps({"deviceId": self.did, "rangeIdList": ids}))

    def push_socket(self):
        """The URL and the signed headers that open the push socket.

        The app signs the handshake like any call, over an empty payload, and
        sends no UserId. The first message on the socket carries the
        `uniqueKey` that `view_subscribe` needs."""
        session = self._session()
        headers = self._headers(session, session["token"], "")
        headers.pop("UserId", None)
        headers.pop("Content-Type", None)
        url = self._base(session).replace("https://", "wss://", 1)
        return url + PUSH_SOCKET_PATH, headers

    def view_subscribe(self, client_id):
        """Ask the cloud to push this device's live targets to the socket
        whose first message carried `client_id`."""
        self._view("app/view/subscribe", client_id)

    def view_unsubscribe(self, client_id):
        self._view("app/view/unsubscribe", client_id)

    def view_heartbeat(self):
        """Tell the FP2 that its map is being watched, as the open device
        page does every VIEW_HEARTBEAT_SECONDS."""
        self._call("POST", "res/write", json.dumps(
            {"subjectId": self.did, "data": {RID_VIEW_HEARTBEAT: 1}}))

    def _view(self, endpoint, client_id):
        if not client_id:
            raise AqaraRefused("no push socket client id")
        body = json.dumps({"subjectId": self.did, "viewId": VIEW_ID,
                           "version": VIEW_VERSION})
        self._call("POST", endpoint, body, {"Clientid": str(client_id)})

    def _call(self, method, endpoint, payload, extra_headers=None):
        session = self._session()
        try:
            return self._send(method, endpoint, payload, session,
                              extra_headers=extra_headers)
        except AqaraError as exc:
            if exc.code != CODE_TOKEN_EXPIRED:
                raise
        self.refresh_token()
        try:
            return self._send(method, endpoint, payload, self._session(),
                              extra_headers=extra_headers)
        except AqaraError as exc:
            if exc.code == CODE_TOKEN_EXPIRED:
                raise AqaraAuthError(endpoint, exc.code, exc.message) from None
            raise

    def _base(self, session):
        return (f"{session['host']}/app/v1.0/lumi/" if session.get("host")
                else self.base_url)

    def _pair(self, session):
        """The session's own pair, else the configured one, else the
        built-in pair of the session's region."""
        if session.get("appid") and session.get("appkey"):
            return session["appid"], session["appkey"]
        if self.appid and self.appkey:
            return self.appid, self.appkey
        builtin = identity_for(session.get("area") or "EU")
        if builtin is None:
            raise AqaraRefused("no app identity for this region")
        return builtin

    def _headers(self, session, token, payload):
        """The app's header set, signed over `payload`."""
        # Each nonce form is the one the research scripts proved for that
        # kind of call: upper-case uuid hex at login, lower-case hex after.
        nonce = (uuid.uuid4().hex.upper() if token is None
                 else secrets.token_hex(16))
        now_ms = str(int(self._clock() * 1000))
        appid, appkey = self._pair(session)
        headers = dict(STATIC_HEADERS)
        headers.update(session.get("headers") or {})
        headers.update({
            "PhoneId": session.get("phone_id", ""),
            "Appid": appid,
            "Nonce": nonce,
            "Time": now_ms,
            "Sign": sign(appid, appkey, nonce, now_ms, token, payload),
            "Content-Type": "application/json; charset=utf-8",
        })
        if token is not None:
            headers["Token"] = token
            headers["UserId"] = session["user_id"]
        return headers

    def _send(self, method, endpoint, payload, session, token=...,
              extra_headers=None):
        """One signed request. Returns `result` or raises AqaraError.

        For a GET, `payload` is the query string and is signed in place of a
        body. `token=None` leaves the token out of the headers and the
        signature, which is what a login needs. `extra_headers` are sent but
        not signed, as the app sends `Clientid`.
        """
        if token is ...:
            token = session["token"]
        headers = self._headers(session, token, payload)
        headers.update(extra_headers or {})
        url = self._base(session) + endpoint
        body = None
        if method == "GET":
            url = f"{url}?{payload}"
        else:
            body = payload.encode()
        try:
            status, raw = self._transport(method, url, headers, body,
                                          self.timeout)
        except OSError as exc:
            raise AqaraError(endpoint, None,
                             f"transport: {type(exc).__name__}") from None
        try:
            answer = json.loads(raw)
        except (TypeError, ValueError):
            raise AqaraError(endpoint, None,
                             f"http {status}, not JSON ({len(raw or b'')} bytes)"
                             ) from None
        if not isinstance(answer, dict):
            raise AqaraError(endpoint, None, f"http {status}, unexpected body")
        code = answer.get("code")
        if code != CODE_OK:
            raise AqaraError(endpoint, code, str(answer.get("message") or ""))
        return answer.get("result")
