"""Signing in to Aqara from the config flow (decision 28).

Up to version 1.4.x the card signed in and encrypted the password in the
browser. Home Assistant's config flow now asks for the account, the
password and the country, tests them with a real sign-in, and starts a
reauthentication when Aqara drops the session. The password is encrypted
here for the one request and is kept nowhere; only the session file holds
the token, as before.
"""

from __future__ import annotations

from enum import StrEnum
import os

from .aqara_cloud import (
    CODE_ACCOUNT_UNKNOWN,
    CODE_INVALID_SIGN,
    AqaraAuthError,
    AqaraCloud,
    AqaraError,
    AqaraRefused,
    SessionStore,
)
from .companion import SESSION_NAME


class SignInError(StrEnum):
    """Why a sign-in failed, as the config flow's error keys."""

    CANNOT_CONNECT = "cannot_connect"
    INVALID_AUTH = "invalid_auth"
    INVALID_IDENTITY = "invalid_identity_region"
    # Any other answer: the form shows Aqara's code and message.
    REFUSED = "refused"


class SignInFailed(Exception):
    """A sign-in that did not go through."""

    def __init__(
        self, reason: SignInError, code: int | None = None, message: str = ""
    ) -> None:
        """Keep the reason the flow shows, and Aqara's own code."""
        super().__init__(reason.value)
        self.reason = reason
        self.code = code
        self.message = message


def sign_in(
    state_dir: str,
    account: str,
    password: str,
    country: str,
    identities: list[list[str]],
) -> None:
    """Sign in and save the session, or raise SignInFailed. Blocks on the
    network, so it runs in an executor. A failed sign-in leaves the previous
    session file as it was."""
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    store = SessionStore(os.path.join(state_dir, SESSION_NAME))
    pairs = [tuple(p) for p in identities if len(p) == 2]
    cloud = AqaraCloud(store, None, None, "", identities=pairs)
    try:
        cloud.login(account, password=password, country=country)
    except AqaraRefused as exc:
        raise SignInFailed(SignInError.INVALID_AUTH) from exc
    except AqaraAuthError as exc:
        raise SignInFailed(SignInError.INVALID_AUTH) from exc
    except AqaraError as exc:
        if exc.code is None:
            raise SignInFailed(SignInError.CANNOT_CONNECT) from exc
        if exc.code == CODE_INVALID_SIGN:
            raise SignInFailed(SignInError.INVALID_IDENTITY) from exc
        if exc.code == CODE_ACCOUNT_UNKNOWN:
            raise SignInFailed(SignInError.INVALID_AUTH) from exc
        raise SignInFailed(SignInError.REFUSED, exc.code, exc.message) from exc


def has_session(state_dir: str) -> bool:
    """Whether the session file holds a session the client can use."""
    try:
        session = SessionStore(os.path.join(state_dir, SESSION_NAME)).load() or {}
    except (OSError, ValueError):
        return False
    return bool(session.get("token") and session.get("user_id"))


def sign_out(state_dir: str) -> None:
    """Forget the token when the integration is removed. The PhoneId stays,
    so adding it again is the same phone to Aqara and the account does not
    collect sessions."""
    path = os.path.join(state_dir, SESSION_NAME)
    if os.path.isfile(path):
        AqaraCloud(SessionStore(path), None, None, "").logout()


def parse_identities(values: list[str]) -> list[list[str]]:
    """`app_id:app_key` per entry, as [app_id, app_key] pairs."""
    pairs = []
    for value in values:
        app_id, sep, app_key = str(value).strip().partition(":")
        if sep and app_id.strip() and app_key.strip():
            pairs.append([app_id.strip(), app_key.strip()])
    return pairs
