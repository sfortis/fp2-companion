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
    (AqaraError("user/login", 108, "wrong password"), "invalid_auth"),
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
