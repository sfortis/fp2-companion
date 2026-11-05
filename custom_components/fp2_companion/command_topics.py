"""Makes sure the card's commands reach the app, without a manual step.

AppDaemon's MQTT plugin hears only the topics in its `client_topics` list,
which defaults to `#`. A person who has written their own list without this
app's topics used to get a card whose every button did nothing, with no
error anywhere.

The app cannot read that list, and subscribing regardless is wrong: on
Mosquitto 2.1.2 a client subscribed to both `#` and `fp2companion/cmd/#` gets
every command twice (measured on 2026-11-01), which would send every zone
write twice. So the app asks the broker instead. It publishes a probe on each
command topic and waits. A probe that comes back means the topic is already
heard. For one that does not, it subscribes through the plugin's
`mqtt/subscribe` service, which also keeps the topic across reconnects, and
probes again. A topic still silent after that is logged as a warning with
the manual step.
"""
import secrets

PROBE_WAIT = 5
PROBE = "probe"
# The probe stands in for a device key, which is always a hex serial.
PROBE_KEY = "probe"

STAGE_PROBE = "probe"
STAGE_VERIFY = "verify"
STAGE_DONE = "done"


class CommandTopics:

    def __init__(self, app, base):
        self.app = app
        self.base = base
        # The topic each subscription covers, and the probe that tests it.
        self.probes = {
            f"{base}/cmd/#": f"{base}/cmd/{PROBE}",
            f"{base}/+/cmd/#": f"{base}/{PROBE_KEY}/cmd/{PROBE}",
        }
        self.stage = None
        self.nonce = None
        self.heard = set()
        self.subscribed = []
        self.missing = []

    def start(self):
        self._probe(list(self.probes), STAGE_PROBE)

    def _probe(self, topics, stage):
        self.stage = stage
        self.nonce = secrets.token_hex(8)
        self.heard = set()
        self.waiting = topics
        for topic in topics:
            self.app.call_service("mqtt/publish", namespace="mqtt",
                                  topic=self.probes[topic],
                                  payload=self.nonce, qos=0, retain=False)
        self.app.run_in(self._check, PROBE_WAIT)

    def consume(self, topic, payload):
        """True when `topic` is a probe, so the caller ignores it. A probe
        from an earlier round, or another instance, is consumed too."""
        for subscription, probe in self.probes.items():
            if topic == probe:
                if payload == self.nonce:
                    self.heard.add(subscription)
                return True
        return False

    def _check(self, kwargs=None):
        missing = [t for t in self.waiting if t not in self.heard]
        if self.stage == STAGE_PROBE:
            if not missing:
                self.stage = STAGE_DONE
                self.app.log("command topics already heard by the MQTT plugin")
                return
            for topic in missing:
                self.app.call_service("mqtt/subscribe", namespace="mqtt",
                                      topic=topic)
            self.subscribed = missing
            self._probe(missing, STAGE_VERIFY)
            return
        if self.stage != STAGE_VERIFY:
            return
        self.stage = STAGE_DONE
        self.missing = missing
        if missing:
            self.app.log(
                f"the card's commands on {', '.join(missing)} do not reach "
                f"this app even after subscribing; add them to client_topics "
                f"of the MQTT plugin in appdaemon.yaml and restart AppDaemon",
                level="WARNING")
        else:
            self.app.log(f"subscribed to {', '.join(self.subscribed)}")
