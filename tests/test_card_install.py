"""The card installs itself: copied into www, one resource pointing at it."""
import hashlib

import pytest

from conftest import _load

ci = _load("card_install", "fp2_companion/card_install.py")

CARD = b"/* card */\nconst CARD_VERSION = '1.16.0';\n"
DIGEST = hashlib.sha256(CARD).hexdigest()[:8]
URL = f"/local/fp2-companion/fp2-companion-card.js?v=1.16.0-{DIGEST}"


class FakeWs:
    """Home Assistant's resource collection over the websocket."""

    def __init__(self, resources=(), fail_on=None):
        self.resources = [dict(r) for r in resources]
        self.sent = []
        self.fail_on = fail_on

    def __call__(self, messages):
        out = []
        for m in messages:
            self.sent.append(m)
            if m["type"] == self.fail_on:
                raise ci.CardInstallError(f"{m['type']}: unknown_command")
            if m["type"] == "lovelace/resources":
                out.append([dict(r) for r in self.resources])
            elif m["type"] == "lovelace/resources/create":
                self.resources.append({"id": "new", "url": m["url"],
                                       "type": m["res_type"]})
                out.append(self.resources[-1])
            elif m["type"] == "lovelace/resources/update":
                r = next(r for r in self.resources if r["id"] == m["resource_id"])
                r.update(url=m["url"], type=m["res_type"])
                out.append(r)
        return out


def test_the_url_carries_the_version_and_a_digest_of_the_file():
    assert ci.resource_url(CARD) == URL
    assert ci.resource_url(CARD + b" ") != URL


def test_the_shipped_card_has_a_version():
    with open(ci.CARD_SOURCE, encoding="utf-8") as handle:
        assert ci.card_version(handle.read())


def test_a_fresh_home_gets_the_card_and_one_resource(tmp_path):
    ws = FakeWs([{"id": "a", "url": "/hacsfiles/other-card.js", "type": "module"}])
    copied, action, url, extra = ci.install_card(CARD, str(tmp_path / "fp2"), ws)
    assert copied and action == ci.RESOURCE_CREATED and url == URL and extra == []
    assert (tmp_path / "fp2" / "fp2-companion-card.js").read_bytes() == CARD
    assert [r["url"] for r in ws.resources] == ["/hacsfiles/other-card.js", URL]


def test_a_card_copied_by_hand_has_its_resource_moved_not_doubled(tmp_path):
    ws = FakeWs([{"id": "old", "url": "/local/hvac/fp2-companion-card.js?v=1.15.0",
                  "type": "module"}])
    _c, action, _u, _e = ci.install_card(CARD, str(tmp_path), ws)
    assert action == ci.RESOURCE_UPDATED
    assert ws.resources == [{"id": "old", "url": URL, "type": "module"}]


def test_a_resource_of_the_wrong_type_is_corrected(tmp_path):
    ws = FakeWs([{"id": "old", "url": URL, "type": "js"}])
    _c, action, _u, _e = ci.install_card(CARD, str(tmp_path), ws)
    assert action == ci.RESOURCE_UPDATED and ws.resources[0]["type"] == "module"


def test_nothing_is_written_when_all_is_current(tmp_path):
    ci.copy_card(CARD, str(tmp_path))
    ws = FakeWs([{"id": "r", "url": URL, "type": "module"}])
    copied, action, _u, _e = ci.install_card(CARD, str(tmp_path), ws)
    assert not copied and action == ci.RESOURCE_CURRENT
    assert [m["type"] for m in ws.sent] == ["lovelace/resources"]


def test_extra_resources_for_the_card_are_reported_never_removed(tmp_path):
    ws = FakeWs([{"id": "1", "url": URL, "type": "module"},
                 {"id": "2", "url": "/local/fp2-companion-card.js", "type": "module"}])
    _c, action, _u, extra = ci.install_card(CARD, str(tmp_path), ws)
    assert action == ci.RESOURCE_CURRENT and extra == ["2"]
    assert len(ws.resources) == 2


def test_a_similar_name_is_not_the_card():
    assert not ci.is_card_resource({"url": "/local/my-fp2-companion-card.js.bak"})
    assert not ci.is_card_resource({"url": "/local/x/fp2-companion-card.json"})
    assert ci.is_card_resource({"url": "/local/x/fp2-companion-card.js?v=3"})


@pytest.mark.parametrize("fail_on, manual", [
    ("lovelace/resources", "add the resource"),
    ("lovelace/resources/create", "add the resource"),
])
def test_a_refused_resource_still_copies_and_names_the_manual_step(tmp_path, fail_on, manual):
    with pytest.raises(ci.CardInstallError) as err:
        ci.install_card(CARD, str(tmp_path), FakeWs(fail_on=fail_on))
    assert err.value.manual.startswith(manual) and URL in err.value.manual
    assert (tmp_path / "fp2-companion-card.js").read_bytes() == CARD


def test_a_refused_update_names_the_resource_to_change(tmp_path):
    ws = FakeWs([{"id": "old", "url": "/local/fp2-companion-card.js", "type": "module"}],
                fail_on="lovelace/resources/update")
    with pytest.raises(ci.CardInstallError) as err:
        ci.install_card(CARD, str(tmp_path), ws)
    assert err.value.manual == f"change the card's resource to {URL}"


def test_no_token_is_an_error_only_when_the_websocket_is_used():
    call = ci.supervisor_ws_call(None)
    with pytest.raises(ci.CardInstallError):
        call([{"type": "lovelace/resources"}])


def test_the_copy_replaces_a_changed_file_whole(tmp_path):
    (tmp_path / "fp2-companion-card.js").write_bytes(b"old")
    assert ci.copy_card(CARD, str(tmp_path))
    assert (tmp_path / "fp2-companion-card.js").read_bytes() == CARD
    assert not (tmp_path / "fp2-companion-card.js.tmp").exists()
