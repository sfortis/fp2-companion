"""The Aqara cloud client: request shapes, signing, the session and refresh.

Driven through a fake transport, so nothing here touches the network. Each
rule tested below broke something while the protocol was worked out, and the
test names say which.
"""
import base64
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "apps" / "fp2_companion"))
import aqara_cloud  # noqa: E402
import regions  # noqa: E402
from aqara_cloud import (  # noqa: E402
    CODE_TOKEN_EXPIRED, REFRESH_MARGIN_SECONDS, AqaraAuthError,
    AqaraCloud, AqaraError, AqaraRefused, SessionStore, did_from_serial,
    jwt_exp)

DID = "lumi1.54ef44a1b2c3"
APPID = "test-appid"
APPKEY = "test-appkey-never-printed"
# The Office FP2, used here as "some other device".
OTHER_DID = "lumi1.54ef44d4e5f6"
NOW = 1793350000.0


def jwt(exp):
    def part(obj):
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()
        return raw.rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'exp': exp})}.signature"


TOKEN_1 = jwt(NOW + 20 * 86400)
TOKEN_2 = jwt(NOW + 30 * 86400)


class FakeTransport:
    """Answers each request with the next queued reply and records it."""

    def __init__(self, store=None):
        self.replies = []
        self.requests = []
        self.store = store
        self.token_on_disk = []

    def reply(self, code=0, result=None, message="ok"):
        self.replies.append({"code": code, "message": message,
                             "result": result})
        return self

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append({"method": method, "url": url,
                              "headers": dict(headers), "body": body})
        if self.store is not None:
            self.token_on_disk.append(self.store.load()["token"])
        return 200, json.dumps(self.replies.pop(0)).encode()

    def body(self, i=-1):
        return json.loads(self.requests[i]["body"])


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "session.json"))
    s.save({"user_id": "user-1", "token": TOKEN_1, "phone_id": "PHONE-1",
            "headers": {}})
    return s


@pytest.fixture(autouse=True)
def no_builtin_identity(monkeypatch):
    """Most tests sign with the configured test pair, so the built-in pairs
    are switched off here; the tests of the built-in pairs put fakes in. No
    test ever sees a real value."""
    monkeypatch.setattr(aqara_cloud, "identity_for", lambda area: None)


def builtin(monkeypatch):
    monkeypatch.setattr(aqara_cloud, "identity_for",
                        lambda area: (f"builtin-{area}", f"key-{area}"))


def client(store, transport):
    return AqaraCloud(store, APPID, APPKEY, DID, transport=transport,
                      clock=lambda: NOW)


# --------------------------------------------------------------- the device
def test_the_did_comes_from_the_homekit_serial():
    assert did_from_serial("54EF44A1B2C3") == DID


@pytest.mark.parametrize("bad", ["", None, "54EF44A1B2C", "54EF44A1B2C3X",
                                 "54EF44A1B2CG"])
def test_a_serial_that_is_not_an_fp2_is_refused(bad):
    with pytest.raises(ValueError):
        did_from_serial(bad)


def test_any_fp2_is_accepted(store):
    # The client works on whichever FP2 it is given.
    t = FakeTransport().reply()
    AqaraCloud(store, APPID, APPKEY, OTHER_DID, transport=t,
               clock=lambda: NOW).write_rid("14.1.85", "2")
    assert t.body()["subjectId"] == OTHER_DID


# ------------------------------------------------------------------ writes
def test_a_value_is_written_as_a_string(store):
    # An integer is acknowledged with code 0 and never reaches the device.
    t = FakeTransport().reply()
    client(store, t).write_rid("14.1.85", 2)
    assert t.body() == {"subjectId": DID, "data": {"14.1.85": "2"}}
    assert t.requests[0]["url"].endswith("/lumi/res/write")


def test_approach_distance_is_writable(store):
    t = FakeTransport().reply()
    client(store, t).write_rid("14.47.85", 0)
    assert t.body()["data"] == {"14.47.85": "0"}


@pytest.mark.parametrize("rid, value", [("14.49.85", "3"), ("14.1.85", 4),
                                        ("14.1.85", "0"), ("14.47.85", 3)])
def test_a_write_outside_the_whitelist_sends_nothing(store, rid, value):
    t = FakeTransport()
    with pytest.raises(AqaraRefused):
        client(store, t).write_rid(rid, value)
    assert t.requests == []


def test_a_zone_of_another_device_is_refused(store):
    t = FakeTransport()
    with pytest.raises(AqaraRefused):
        client(store, t).set_zone({"deviceId": OTHER_DID})
    assert t.requests == []


# ------------------------------------------------------------------ signing
def test_a_post_is_signed_over_its_body(store):
    t = FakeTransport().reply(result=[])
    client(store, t).query_rids(["14.1.85"])
    h = t.requests[0]["headers"]
    body = t.requests[0]["body"].decode()
    source = (f"Appid={APPID}&Nonce={h['Nonce']}&Time={h['Time']}"
              f"&Token={TOKEN_1}&{body}&{APPKEY}")
    assert h["Sign"] == hashlib.md5(source.encode()).hexdigest()
    assert h["Time"] == str(int(NOW * 1000))
    assert h["Token"] == TOKEN_1 and h["UserId"] == "user-1"
    assert h["PhoneId"] == "PHONE-1"
    assert h["User-Agent"] == "okhttp/4.12.0"


def test_the_zone_types_are_read_with_their_tag_type(store):
    t = FakeTransport().reply(result=[{"tagId": "1.2", "tagName": "Sofa Area"}])
    assert client(store, t).list_zone_tags(9) == [
        {"tagId": "1.2", "tagName": "Sofa Area"}]
    url = urlsplit(t.requests[0]["url"])
    assert t.requests[0]["method"] == "GET"
    assert url.path.endswith("/lumi/app/tag/bottom/list")
    assert parse_qs(url.query) == {"tagType": ["9"]}


def test_zone_types_that_are_not_a_list_are_an_error(store):
    t = FakeTransport().reply(result={"tagId": "1.2"})
    with pytest.raises(AqaraError):
        client(store, t).list_zone_tags(9)


def test_a_get_is_signed_over_its_query_string(store):
    t = FakeTransport().reply(result=[])
    client(store, t).list_zones()
    req = t.requests[0]
    assert req["method"] == "GET" and req["body"] is None
    url = urlsplit(req["url"])
    assert url.path.endswith("/lumi/devex/radar/range/list")
    assert parse_qs(url.query) == {"deviceId": [DID]}
    h = req["headers"]
    source = (f"Appid={APPID}&Nonce={h['Nonce']}&Time={h['Time']}"
              f"&Token={TOKEN_1}&deviceId={DID}&{APPKEY}")
    assert h["Sign"] == hashlib.md5(source.encode()).hexdigest()


def test_a_query_returns_values_by_rid(store):
    t = FakeTransport().reply(result=[
        {"resourceId": "14.1.85", "value": "1", "subjectId": DID},
        {"resourceId": "14.47.85", "value": "2", "subjectId": DID},
        {"resourceId": "14.1.85", "value": "3", "subjectId": OTHER_DID},
    ])
    values = client(store, t).query_rids(["14.1.85", "14.47.85"])
    assert values == {"14.1.85": "1", "14.47.85": "2"}
    assert t.body() == {"data": [{"options": ["14.1.85", "14.47.85"],
                                  "subjectId": DID}]}


# ------------------------------------------------------------------ refresh
def test_an_expired_token_is_refreshed_once_and_the_call_retried(store):
    t = FakeTransport(store)
    t.reply(code=CODE_TOKEN_EXPIRED, message="Token has expired")
    t.reply(result={"token": TOKEN_2, "expiresIn": 2592000})
    t.reply()
    client(store, t).write_rid("14.1.85", "2")
    assert [r["url"].rsplit("/lumi/", 1)[1] for r in t.requests] == [
        "res/write", "user/refresh-token", "res/write"]
    assert t.requests[1]["body"] == b"{}"
    assert t.requests[2]["headers"]["Token"] == TOKEN_2
    # The new token was on disk before the retry left, because the server
    # had already revoked the old one.
    assert t.token_on_disk[2] == TOKEN_2


def test_a_second_expiry_after_a_refresh_is_a_hard_failure(store):
    t = FakeTransport()
    t.reply(code=CODE_TOKEN_EXPIRED)
    t.reply(result={"token": TOKEN_2})
    t.reply(code=CODE_TOKEN_EXPIRED)
    with pytest.raises(AqaraAuthError):
        client(store, t).write_rid("14.1.85", "2")
    assert len(t.requests) == 3


def test_a_refused_refresh_is_a_hard_failure(store):
    t = FakeTransport()
    t.reply(code=CODE_TOKEN_EXPIRED)
    t.reply(code=CODE_TOKEN_EXPIRED)
    with pytest.raises(AqaraAuthError):
        client(store, t).list_zones()
    assert len(t.requests) == 2
    assert store.load()["token"] == TOKEN_1


def test_a_refresh_with_no_token_in_the_answer_keeps_the_old_one(store):
    t = FakeTransport().reply(result={"expiresIn": 1})
    with pytest.raises(AqaraAuthError):
        client(store, t).refresh_token()
    assert store.load()["token"] == TOKEN_1


def test_the_session_file_is_private_and_replaced_whole(store, tmp_path):
    t = FakeTransport().reply(result={"token": TOKEN_2})
    client(store, t).refresh_token()
    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    assert mode == 0o600
    assert os.listdir(tmp_path) == ["session.json"]
    saved = store.load()
    assert saved["token"] == TOKEN_2
    assert saved["phone_id"] == "PHONE-1"
    assert saved["refreshed_at"] == NOW


def test_a_token_far_from_expiry_is_left_alone(store):
    t = FakeTransport()
    assert client(store, t).ensure_fresh() is False
    assert t.requests == []


def test_a_token_inside_the_margin_is_refreshed(store):
    s = store.load()
    s["token"] = jwt(NOW + REFRESH_MARGIN_SECONDS - 3600)
    store.save(s)
    t = FakeTransport().reply(result={"token": TOKEN_2})
    assert client(store, t).ensure_fresh() is True
    assert store.load()["token"] == TOKEN_2


def test_an_unreadable_expiry_counts_as_due(store):
    s = store.load()
    s["token"] = "not-a-jwt"
    store.save(s)
    assert client(store, FakeTransport()).needs_refresh()


def test_jwt_expiry_in_milliseconds_is_read_as_seconds():
    assert jwt_exp(jwt((NOW + 60) * 1000)) == pytest.approx(NOW + 60)


def test_no_session_sends_nothing(tmp_path):
    t = FakeTransport()
    c = AqaraCloud(SessionStore(str(tmp_path / "none.json")), APPID, APPKEY,
                   DID, transport=t)
    with pytest.raises(AqaraAuthError):
        c.list_zones()
    assert t.requests == []


# ------------------------------------------------------------------- errors
def test_an_error_carries_no_credential(store):
    t = FakeTransport().reply(code=302, message="Params error")
    with pytest.raises(AqaraError) as exc:
        client(store, t).write_rid("14.1.85", "1")
    text = str(exc.value)
    assert exc.value.code == 302
    for secret in (TOKEN_1, APPKEY, "user-1", "PHONE-1"):
        assert secret not in text


def test_a_transport_failure_is_an_aqara_error(store):
    def broken(*_a):
        raise TimeoutError("timed out")
    with pytest.raises(AqaraError) as exc:
        client(store, broken).list_zones()
    assert exc.value.code is None


def test_a_body_that_is_not_json_is_an_aqara_error(store):
    with pytest.raises(AqaraError):
        client(store, lambda *_a: (502, b"<html>bad gateway</html>")).list_zones()


# -------------------------------------------------------------------- login
def test_a_login_reuses_the_phone_id_and_sends_no_token(tmp_path):
    s = SessionStore(str(tmp_path / "session.json"))
    s.save({"phone_id": "PHONE-KEEP"})
    t = FakeTransport().reply(result={"token": TOKEN_1, "userId": "user-9"})
    out = client(s, t).login("owner@example.com", "pw-never-sent-plain", country="GR")
    h = t.requests[0]["headers"]
    assert "Token" not in h and "UserId" not in h
    assert h["PhoneId"] == "PHONE-KEEP"
    body = t.requests[0]["body"].decode()
    source = (f"Appid={APPID}&Nonce={h['Nonce']}&Time={h['Time']}"
              f"&{body}&{APPKEY}")
    assert h["Sign"] == hashlib.md5(source.encode()).hexdigest()
    assert "pw-never-sent-plain" not in body
    assert json.loads(body)["encryptType"] == 2
    assert out["phone_id_reused"] is True
    saved = s.load()
    assert saved["phone_id"] == "PHONE-KEEP"
    assert saved["token"] == TOKEN_1 and saved["user_id"] == "user-9"
    assert "pw-never-sent-plain" not in json.dumps(saved)


def test_a_first_login_creates_a_phone_id_that_the_next_one_keeps(tmp_path):
    s = SessionStore(str(tmp_path / "session.json"))
    t = FakeTransport()
    t.reply(result={"token": TOKEN_1, "userId": "user-9"})
    t.reply(result={"token": TOKEN_2, "userId": "user-9"})
    c = client(s, t)
    assert c.login("a", "b", country="GR")["phone_id_reused"] is False
    first = s.load()["phone_id"]
    assert c.login("a", "b", country="GR")["phone_id_reused"] is True
    assert s.load()["phone_id"] == first
    assert t.requests[1]["headers"]["PhoneId"] == first


# -------------------------------------------------------------------- zones
def test_a_zone_without_an_id_is_sent_as_a_create(store):
    t = FakeTransport().reply()
    z = {"deviceId": DID, "blockList": [15, 16, 31, 32], "colorId": 0,
         "name": "TestZone", "regionType": 0, "sensitivity": 1,
         "tagId": "", "tagName": "Other"}
    client(store, t).set_zone(z)
    assert t.requests[0]["url"].endswith("/lumi/devex/radar/range/set")
    assert "rangeId" not in t.body()


def test_a_delete_uses_its_own_endpoint_and_a_list(store):
    # Captured 2026-10-30: the official app deletes with range/del.
    t = FakeTransport().reply()
    client(store, t).delete_zones([4])
    assert t.requests[0]["url"].endswith("/lumi/devex/radar/range/del")
    assert t.body() == {"deviceId": DID, "rangeIdList": [4]}


@pytest.mark.parametrize("ids", [[], [0], ["4"], [True], [-1]])
def test_a_delete_of_no_valid_id_sends_nothing(store, ids):
    t = FakeTransport()
    with pytest.raises(AqaraRefused):
        client(store, t).delete_zones(ids)
    assert t.requests == []


# ------------------------------------------------------- login from the card
BLOB = base64.b64encode(bytes(range(128))).decode()


def test_a_card_login_sends_the_browser_blob_verbatim(store):
    t = FakeTransport().reply(result={"token": TOKEN_2, "userId": "user-2"})
    c = client(store, t)
    c.login("owner@example.com", encrypted_password=BLOB, country="GR")
    assert json.loads(t.requests[0]["body"])["password"] == BLOB
    assert c.account() == "owner@example.com"
    assert store.load()["phone_id"] == "PHONE-1"


@pytest.mark.parametrize("blob", ["", "not base64!", base64.b64encode(b"x" * 64).decode()])
def test_a_blob_that_is_not_an_rsa_block_sends_nothing(store, blob):
    t = FakeTransport()
    with pytest.raises(AqaraRefused):
        client(store, t).login("owner@example.com", encrypted_password=blob, country="GR")
    assert t.requests == []


def test_a_password_and_a_blob_together_are_refused(store):
    with pytest.raises(AqaraRefused):
        client(store, FakeTransport()).login("a", "pw", encrypted_password=BLOB, country="GR")


def test_a_failed_login_keeps_the_session_that_was_working(store):
    t = FakeTransport().reply(code=10012, message="password error")
    with pytest.raises(AqaraError):
        client(store, t).login("other@example.com", encrypted_password=BLOB, country="GR")
    assert store.load()["token"] == TOKEN_1


def test_a_logout_forgets_the_token_and_keeps_the_phone(store):
    c = client(store, FakeTransport())
    c.logout()
    saved = store.load()
    assert "token" not in saved and "user_id" not in saved
    assert saved["phone_id"] == "PHONE-1"
    assert c.account() is None
    with pytest.raises(AqaraAuthError):
        c.list_zones()


# ---------------------------------------------------------------------- AI
def test_the_ai_mode_and_learning_rids_are_writable_as_strings(store):
    t = FakeTransport().reply().reply()
    c = client(store, t)
    c.write_rid("4.72.85", 0)
    c.write_rid("4.66.85", "1")
    assert t.body(0)["data"] == {"4.72.85": "0"}
    assert t.body(1)["data"] == {"4.66.85": "1"}


def test_learning_has_no_stop_value(store):
    t = FakeTransport()
    with pytest.raises(AqaraRefused):
        client(store, t).write_rid("4.66.85", "0")
    assert t.requests == []


# ------------------------------------------------------------------ regions
def test_a_sign_in_in_the_us_goes_to_the_us_host_with_its_headers(store):
    t = FakeTransport().reply(result={"token": TOKEN_2, "userId": "user-2"})
    t.reply(result=[])
    c = client(store, t)
    c.login("a@example.com", encrypted_password=BLOB, country="us")
    req = t.requests[0]
    assert req["url"] == "https://aiot-rpc-usa.aqara.com/app/v1.0/lumi/user/login"
    assert req["headers"]["Cuty"] == "US" and req["headers"]["Area"] == "US"
    saved = store.load()
    assert saved["country"] == "US" and saved["host"].endswith("aqara.com")
    c.list_zones()
    assert t.requests[1]["url"].startswith(
        "https://aiot-rpc-usa.aqara.com/app/v1.0/lumi/devex/")


def test_a_rejected_signature_tries_the_next_app_identity(store):
    # With the built-in pairs switched off: the configured pairs in order.
    t = FakeTransport()
    t.reply(code=106, message="invalid sign")
    t.reply(result={"token": TOKEN_2, "userId": "user-2"})
    c = AqaraCloud(store, APPID, APPKEY, DID, transport=t, clock=lambda: NOW,
                   identities=[("other-id", "other-key")])
    c.login("a@example.com", encrypted_password=BLOB, country="JP")
    assert [r["headers"]["Appid"] for r in t.requests] == [APPID, "other-id"]
    assert store.load()["appid"] == "other-id"
    assert t.requests[1]["url"].startswith("https://rpc-kr.aqara.com/")


def test_no_accepted_identity_is_a_clear_failure(store):
    t = FakeTransport().reply(code=106, message="invalid sign")
    with pytest.raises(AqaraError) as exc:
        client(store, t).login("a@example.com", encrypted_password=BLOB,
                               country="GR")
    assert exc.value.code == 106
    assert store.load()["token"] == TOKEN_1


def test_an_unknown_country_sends_nothing(store):
    t = FakeTransport()
    with pytest.raises(AqaraRefused):
        client(store, t).login("a@example.com", encrypted_password=BLOB,
                               country="XX")
    assert t.requests == []


def test_a_session_from_before_regions_still_uses_the_eu_host(store):
    t = FakeTransport().reply(result=[])
    client(store, t).list_zones()
    assert t.requests[0]["url"].startswith("https://rpc-ger.aqara.com/")


# ----------------------------------------------------------------- live positions
def test_the_push_socket_is_signed_over_nothing_and_sends_no_user_id(store):
    url, headers = client(store, FakeTransport()).push_socket()
    assert url == "wss://rpc-ger.aqara.com/app/v1.0/lumi/push/ws/aqarahome"
    want = hashlib.md5("&".join([
        f"Appid={APPID}", f"Nonce={headers['Nonce']}",
        f"Time={headers['Time']}", f"Token={TOKEN_1}", "", APPKEY,
    ]).encode()).hexdigest()
    assert headers["Sign"] == want
    assert headers["Token"] == TOKEN_1
    assert "UserId" not in headers and "Content-Type" not in headers


def test_the_push_socket_follows_the_session_host(store):
    session = store.load()
    session["host"] = "https://aiot-rpc-usa.aqara.com"
    store.save(session)
    url, _ = client(store, FakeTransport()).push_socket()
    assert url == "wss://aiot-rpc-usa.aqara.com/app/v1.0/lumi/push/ws/aqarahome"


def test_a_view_subscribe_names_the_socket_in_an_unsigned_header(store):
    t = FakeTransport().reply(result="").reply(result="")
    c = client(store, t)
    c.view_subscribe("socket-1")
    c.view_unsubscribe("socket-1")
    assert [urlsplit(r["url"]).path.rsplit("/", 2)[-2:] for r in t.requests] \
        == [["view", "subscribe"], ["view", "unsubscribe"]]
    assert t.body(0) == {"subjectId": DID, "viewId": "control",
                         "version": "1.8"}
    assert t.requests[0]["headers"]["Clientid"] == "socket-1"
    # The signature covers the body only, as for any other POST.
    h = t.requests[0]["headers"]
    assert h["Sign"] == hashlib.md5("&".join([
        f"Appid={APPID}", f"Nonce={h['Nonce']}", f"Time={h['Time']}",
        f"Token={TOKEN_1}", t.requests[0]["body"].decode(), APPKEY,
    ]).encode()).hexdigest()


def test_a_view_subscribe_without_a_socket_sends_nothing(store):
    t = FakeTransport()
    with pytest.raises(AqaraRefused):
        client(store, t).view_subscribe("")
    assert t.requests == []


def test_no_session_opens_no_push_socket(tmp_path):
    c = AqaraCloud(SessionStore(str(tmp_path / "none.json")), APPID, APPKEY,
                   DID, transport=FakeTransport())
    with pytest.raises(AqaraAuthError):
        c.push_socket()


def test_the_view_heartbeat_is_the_device_pages_own_write(store):
    t = FakeTransport().reply(result="")
    client(store, t).view_heartbeat()
    assert urlsplit(t.requests[0]["url"]).path.endswith("/res/write")
    # The integer 1, exactly as the H5 page sends it.
    assert t.body() == {"subjectId": DID, "data": {"4.22.85": 1}}


# ----------------------------------------------------------------- app identity
def test_the_regions_own_pair_is_tried_before_a_configured_one(store,
                                                                monkeypatch):
    builtin(monkeypatch)
    t = FakeTransport()
    t.reply(code=106, message="invalid sign")
    t.reply(result={"token": TOKEN_2, "userId": "user-2"})
    client(store, t).login("a@example.com", encrypted_password=BLOB,
                           country="US")
    assert [r["headers"]["Appid"] for r in t.requests] == ["builtin-US",
                                                          APPID]
    assert store.load()["appid"] == APPID


def test_no_configured_pair_signs_in_with_the_built_in_one(store, monkeypatch):
    builtin(monkeypatch)
    t = FakeTransport().reply(result={"token": TOKEN_2, "userId": "user-2"})
    AqaraCloud(store, None, None, DID, transport=t, clock=lambda: NOW).login(
        "a@example.com", encrypted_password=BLOB, country="GR")
    h = t.requests[0]["headers"]
    assert h["Appid"] == "builtin-EU"
    source = (f"Appid=builtin-EU&Nonce={h['Nonce']}&Time={h['Time']}"
              f"&{t.requests[0]['body'].decode()}&key-EU")
    assert h["Sign"] == hashlib.md5(source.encode()).hexdigest()
    assert store.load()["appkey"] == "key-EU"


def test_a_session_without_a_pair_signs_with_its_regions(store, monkeypatch):
    builtin(monkeypatch)
    session = store.load()
    session["area"] = "KR"
    store.save(session)
    t = FakeTransport().reply(result=[])
    AqaraCloud(store, None, None, DID, transport=t,
               clock=lambda: NOW).list_zones()
    assert t.requests[0]["headers"]["Appid"] == "builtin-KR"


def test_every_region_has_a_built_in_pair_stored_obfuscated():
    source = open(regions.__file__).read()
    for area in regions.AREA_HOSTS:
        pair = regions.identity_for(area)
        # Booleans only, so a failure never prints a value.
        present = pair is not None and all(pair)
        assert present, area
        hidden = pair[0] not in source and pair[1] not in source
        assert hidden, area


def test_a_sign_in_without_a_country_sends_nothing(tmp_path):
    t = FakeTransport()
    c = AqaraCloud(SessionStore(str(tmp_path / "s.json")), APPID, APPKEY, DID,
                   transport=t, clock=lambda: NOW)
    with pytest.raises(AqaraRefused):
        c.login("a@example.com", encrypted_password=BLOB)
    assert t.requests == []


def test_a_sign_in_again_keeps_the_previous_country(store):
    session = store.load()
    session["country"] = "US"
    store.save(session)
    t = FakeTransport().reply(result={"token": TOKEN_2, "userId": "user-2"})
    client(store, t).login("a@example.com", encrypted_password=BLOB)
    assert t.requests[0]["url"].startswith("https://aiot-rpc-usa.aqara.com/")
