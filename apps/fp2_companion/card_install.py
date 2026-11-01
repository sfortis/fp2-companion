"""Installs the card that ships inside the app folder into Home Assistant.

HACS downloads an AppDaemon repository's app folder and nothing else, so the
card lives in `card/` beside this module. At startup the app copies it into
Home Assistant's `www` folder and points the card's Lovelace resource at the
copy, with the card's version and a digest of the file in `?v=`, so a browser
loads a new card after every update without anyone touching the resource.

The resource is changed over Home Assistant's websocket API, reached through
the Supervisor with the add-on's own `SUPERVISOR_TOKEN`. Without a Supervisor,
or with dashboards in YAML mode, the copy still happens and the log says
which resource to add by hand.

Planning is kept apart from doing, so the decisions are testable without a
Home Assistant: `plan_resource` decides, `install_card` carries it out with
whatever websocket client it is given.
"""
import asyncio
import hashlib
import json
import os
import re

CARD_NAME = "fp2-companion-card.js"
CARD_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "card",
                           CARD_NAME)
# Served by Home Assistant as /local/fp2-companion/.
DEFAULT_WWW_DIR = "/homeassistant/www/fp2-companion"
URL_DIR = "/local/fp2-companion"
SUPERVISOR_WS = "ws://supervisor/core/websocket"
WS_TIMEOUT = 15

# What install_card did with the resource.
RESOURCE_CREATED = "created"
RESOURCE_UPDATED = "updated"
RESOURCE_CURRENT = "current"

_VERSION = re.compile(r"const CARD_VERSION = '([^']+)'")


class CardInstallError(Exception):
    """A step that could not be done, with the manual step to replace it."""

    def __init__(self, message, manual=None):
        super().__init__(message)
        self.manual = manual


def card_version(text):
    match = _VERSION.search(text)
    return match.group(1) if match else None


def resource_url(data):
    """The resource URL for the card's bytes: its version, then a digest, so
    even a card changed without a version bump busts the browser's cache."""
    version = card_version(data.decode("utf-8", "replace")) or "0"
    digest = hashlib.sha256(data).hexdigest()[:8]
    return f"{URL_DIR}/{CARD_NAME}?v={version}-{digest}"


def is_card_resource(resource):
    """Any resource that loads this card, wherever it was copied by hand."""
    path = str(resource.get("url", "")).split("?", 1)[0]
    return path.rsplit("/", 1)[-1] == CARD_NAME


def plan_resource(resources, url):
    """What to do with the Lovelace resources so exactly one loads the card.

    Returns (action, resource_id, extra_ids): RESOURCE_CREATED with no id when
    none loads it, RESOURCE_UPDATED with the id of the first one that does
    when its URL or type differs, RESOURCE_CURRENT when it already matches.
    `extra_ids` are further resources that load the card too. They are
    reported, never deleted, because a person may have put them there.
    """
    mine = [r for r in resources if is_card_resource(r)]
    if not mine:
        return RESOURCE_CREATED, None, []
    first, extra = mine[0], [r.get("id") for r in mine[1:]]
    # The list names the type `type`; create and update take it as `res_type`.
    if first.get("url") == url and first.get("type") == "module":
        return RESOURCE_CURRENT, first.get("id"), extra
    return RESOURCE_UPDATED, first.get("id"), extra


def copy_card(data, www_dir):
    """Writes the card into `www_dir` unless the same bytes are there.

    Returns True when it wrote. The write goes to a temporary file first, so
    Home Assistant never serves half a card.
    """
    target = os.path.join(www_dir, CARD_NAME)
    try:
        with open(target, "rb") as handle:
            if handle.read() == data:
                return False
    except FileNotFoundError:
        pass
    os.makedirs(www_dir, exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, target)
    return True


def install_card(data, www_dir, ws_call):
    """Copies the card and makes its resource point at the copy.

    `ws_call(messages)` sends websocket commands in order and returns their
    results; it raises CardInstallError when it cannot. Returns
    (copied, action, url, extra_ids).
    """
    url = resource_url(data)
    copied = copy_card(data, www_dir)
    manual = f"add the resource {url} of type JavaScript module"
    try:
        resources = ws_call([{"type": "lovelace/resources"}])[0]
    except CardInstallError as exc:
        raise CardInstallError(str(exc), manual) from exc
    if not isinstance(resources, list):
        raise CardInstallError("the resource list is not a list", manual)
    action, rid, extra = plan_resource(resources, url)
    if action == RESOURCE_CREATED:
        message = {"type": "lovelace/resources/create",
                   "res_type": "module", "url": url}
    elif action == RESOURCE_UPDATED:
        message = {"type": "lovelace/resources/update", "resource_id": rid,
                   "res_type": "module", "url": url}
        manual = f"change the card's resource to {url}"
    else:
        return copied, action, url, extra
    try:
        ws_call([message])
    except CardInstallError as exc:
        raise CardInstallError(str(exc), manual) from exc
    return copied, action, url, extra


def supervisor_ws_call(token, url=SUPERVISOR_WS, timeout=WS_TIMEOUT):
    """A `ws_call` for install_card over Home Assistant's websocket API.

    Each call opens a connection, authenticates, sends the messages in order
    and closes. It runs its own event loop, so it must not be called from a
    thread that already runs one.
    """
    def call(messages):
        if not token:
            raise CardInstallError("no SUPERVISOR_TOKEN, so no Supervisor to ask")
        return asyncio.run(_exchange(url, token, messages, timeout))
    return call


async def _exchange(url, token, messages, timeout):
    import aiohttp  # AppDaemon ships it; imported here so tests need none.
    results = []
    try:
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(url, timeout=timeout) as ws:
                first = await ws.receive_json(timeout=timeout)
                if first.get("type") != "auth_required":
                    raise CardInstallError(f"unexpected greeting {first.get('type')!r}")
                await ws.send_json({"type": "auth", "access_token": token})
                auth = await ws.receive_json(timeout=timeout)
                if auth.get("type") != "auth_ok":
                    raise CardInstallError("Home Assistant refused the token")
                for number, message in enumerate(messages, start=1):
                    await ws.send_json(dict(message, id=number))
                    while True:
                        reply = await ws.receive_json(timeout=timeout)
                        if reply.get("id") == number:
                            break
                    if not reply.get("success"):
                        error = reply.get("error") or {}
                        raise CardInstallError(
                            f"{message['type']}: {error.get('code')} "
                            f"{error.get('message')}")
                    results.append(reply.get("result"))
    except CardInstallError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError,
            TypeError, OSError) as exc:
        raise CardInstallError(f"websocket: {type(exc).__name__}: {exc}") from exc
    return results
