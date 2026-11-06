"""Test harness for the fp2_companion app.

Imports the package without Home Assistant, and provides `World`, a fake
runtime that records what the app publishes, its timers and its
subscriptions. MQTT is the app's only output, so the tests read what it
published back off the recorded calls.
"""
import importlib.util
import sys
import types
from pathlib import Path

PACKAGE = "custom_components.fp2_companion"
PACKAGE_DIR = (Path(__file__).resolve().parent.parent
               / "custom_components" / "fp2_companion")

# The package's __init__.py needs Home Assistant, which these tests do without.
# The modules import each other relatively, so two bare packages that only
# carry the folder in __path__ are enough to import them.
for _name, _path in (("custom_components", PACKAGE_DIR.parent),
                     (PACKAGE, PACKAGE_DIR)):
    if _name not in sys.modules:
        _pkg = types.ModuleType(_name)
        _pkg.__path__ = [str(_path)]
        sys.modules[_name] = _pkg


def _load(module_name):
    """A fresh copy of one module of the package, so that a test may replace
    its constants without reaching the next test."""
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{module_name}", PACKAGE_DIR / f"{module_name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class World:
    """The runtime's side, in memory: recorded publishes, timers and
    subscriptions."""

    def __init__(self):
        self.calls = []      # ("mqtt/publish", None, data)
        self.logs = []

    def publish(self, topic, payload, retain=False, qos=0):
        self.calls.append(("mqtt/publish", None,
                           {"topic": topic, "payload": payload,
                            "retain": retain, "qos": qos}))

    def log(self, *a, **k):
        self.logs.append(" ".join(str(x) for x in a))

    def run_daily(self, callback, start, *a, **k):
        self.daily = getattr(self, "daily", [])
        handle = "daily-%d" % len(self.daily)
        self.daily.append({"handle": handle, "start": start,
                           "callback": callback})
        return handle

    def cancel_timer(self, handle, silent=False):
        self.cancelled = getattr(self, "cancelled", [])
        self.cancelled.append(handle)
        self.silent_cancels = getattr(self, "silent_cancels", [])
        self.silent_cancels.append(silent)

    def run_in(self, callback=None, delay=None, *a, **k):
        """Timers are never fired; a test calls the callback itself."""
        self.timers = getattr(self, "timers", [])
        handle = "timer-%d" % len(self.timers)
        self.timers.append({"handle": handle, "callback": callback,
                            "delay": delay})
        return handle

    def subscribe(self, topics, callback):
        self.subscriptions = getattr(self, "subscriptions", [])
        self.subscriptions.append({"topics": list(topics),
                                   "callback": callback})


def _bind(app, world):
    for name in ("publish", "log", "run_in", "run_daily", "cancel_timer",
                 "subscribe"):
        setattr(app, name, getattr(world, name))


def mqtt_payload(world, topic):
    """The last payload published to `topic`, or None."""
    for service, _entity, data in reversed(world.calls):
        if service == "mqtt/publish" and data.get("topic") == topic:
            return data.get("payload")
    return None
