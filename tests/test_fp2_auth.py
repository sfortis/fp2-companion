"""Signing in from the config flow: what each failure becomes."""
import pytest

from conftest import _load

from custom_components.fp2_companion.aqara_cloud import (AqaraAuthError,
                                                         AqaraError,
                                                         AqaraRefused)


class FakeCloud:
    error = None
    calls = []

    def __init__(self, store, appid, appkey, did, identities=()):
        self.store = store
        self.identities = list(identities)

    def login(self, account, password=None, encrypted_password=None,
              country=None):
        FakeCloud.calls.append((account, password, country, self.identities))
        if FakeCloud.error:
            raise FakeCloud.error


@pytest.fixture
def auth(monkeypatch):
    mod = _load("auth")
    FakeCloud.error = None
    FakeCloud.calls = []
    monkeypatch.setattr(mod, "AqaraCloud", FakeCloud)
    return mod


def test_a_sign_in_passes_the_clear_password_once(auth, tmp_path):
    auth.sign_in(str(tmp_path), "a@example.com", "secret", "GR", [["id", "key"]])
    assert FakeCloud.calls == [("a@example.com", "secret", "GR", [("id", "key")])]


@pytest.mark.parametrize("error, reason", [
    (AqaraError("user/login", None, "timeout"), "cannot_connect"),
    (AqaraError("user/login", 106, "Invalid sign"), "invalid_identity_region"),
    (AqaraError("user/login", 810, "no such account"), "invalid_auth"),
    (AqaraError("user/login", 1001, "locked"), "refused"),
    (AqaraAuthError("user/login", 0, "no session"), "invalid_auth"),
    (AqaraRefused("no account"), "invalid_auth"),
])
def test_a_failed_sign_in_says_why(auth, tmp_path, error, reason):
    FakeCloud.error = error
    with pytest.raises(auth.SignInFailed) as exc:
        auth.sign_in(str(tmp_path), "a@example.com", "secret", "GR", [])
    assert exc.value.reason.value == reason


def test_identities_are_parsed_as_pairs(auth):
    assert auth.parse_identities(["id:key", " a : b ", "nokey", ":x"]) == [
        ["id", "key"], ["a", "b"]]


def test_an_unknown_refusal_carries_aqaras_code(auth, tmp_path):
    FakeCloud.error = AqaraError("user/login", 1001, "account locked")
    with pytest.raises(auth.SignInFailed) as exc:
        auth.sign_in(str(tmp_path), "a@example.com", "secret", "GR", [])
    assert (exc.value.code, exc.value.message) == (1001, "account locked")


def test_the_state_folder_is_private(auth, tmp_path):
    state = tmp_path / "state"
    auth.sign_in(str(state), "a@example.com", "secret", "GR", [])
    assert (state.stat().st_mode & 0o777) == 0o700


def test_sign_out_forgets_the_token_and_keeps_the_phone(tmp_path):
    import json

    mod = _load("auth")
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"token": "t", "phone_id": "P", "account": "a"}))
    mod.sign_out(str(tmp_path))
    kept = json.loads(path.read_text())
    assert "token" not in kept and kept["phone_id"] == "P"
    mod.sign_out(str(tmp_path / "missing"))   # nothing there: no error
