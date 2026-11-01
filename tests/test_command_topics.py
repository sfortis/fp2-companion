"""The app makes sure the card's commands reach it, without doubling them."""
from conftest import World, _load

ct = _load("command_topics", "fp2_companion/command_topics.py")

CMD = "fp2companion/cmd/#"
DEV = "fp2companion/+/cmd/#"
PROBE_CMD = "fp2companion/cmd/probe"
PROBE_DEV = "fp2companion/probe/cmd/probe"


class App(World):
    def __init__(self):
        super().__init__()
        self.timers = []

    def run_in(self, callback, delay=None, **kwargs):
        self.timers.append(callback)


def probes_sent(app):
    return [d["topic"] for s, _e, d in app.calls
            if s == "mqtt/publish" and d.get("namespace") == "mqtt"]


def subscribed(app):
    return [d["topic"] for s, _e, d in app.calls if s == "mqtt/subscribe"]


def broker(app, topics, heard):
    """Delivers the probes just sent whose subscription is in `heard`."""
    commands = topics
    for s, _e, d in list(app.calls):
        if s == "mqtt/publish" and d.get("payload") == commands.nonce:
            sub = next(k for k, v in commands.probes.items() if v == d["topic"])
            if sub in heard:
                assert commands.consume(d["topic"], d["payload"])


def start(heard_before, heard_after=None):
    app = App()
    commands = ct.CommandTopics(app, "fp2companion")
    commands.start()
    broker(app, commands, heard_before)
    app.timers.pop()()
    if commands.stage == ct.STAGE_VERIFY:
        broker(app, commands, heard_after or set())
        app.timers.pop()()
    return app, commands


def test_with_the_default_hash_nothing_is_subscribed():
    """`#` already hears both: subscribing again would double every command."""
    app, commands = start({CMD, DEV})
    assert sorted(probes_sent(app)) == [PROBE_CMD, PROBE_DEV]
    assert subscribed(app) == [] and commands.stage == ct.STAGE_DONE


def test_a_list_without_the_topics_gets_them_subscribed():
    app, commands = start(set(), {CMD, DEV})
    assert subscribed(app) == [CMD, DEV]
    assert commands.missing == []
    assert any("subscribed to" in line for line in app.logs)


def test_only_the_missing_topic_is_subscribed():
    app, _c = start({CMD}, {DEV})
    assert subscribed(app) == [DEV]


def test_a_topic_still_silent_after_subscribing_is_a_warning():
    app, commands = start(set(), {CMD})
    assert commands.missing == [DEV]
    assert any("client_topics" in line and DEV in line for line in app.logs)


def test_a_probe_from_an_earlier_round_is_consumed_but_not_counted():
    app = App()
    commands = ct.CommandTopics(app, "fp2companion")
    commands.start()
    assert commands.consume(PROBE_CMD, "stale")
    assert commands.heard == set()
    assert not commands.consume("fp2companion/cmd/rescan", commands.nonce)
