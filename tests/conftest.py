"""Test harness for the fp2_companion app.

Stubs `hassapi` so the app imports without AppDaemon, and provides `World`,
an in-memory Home Assistant that records service calls, timers and
listeners. MQTT is the app's only output, so the tests read what it
published back off the recorded service calls.
"""
import importlib.util
import sys
import types
from pathlib import Path

_hass_mod = types.ModuleType("hassapi")


class _Hass:
    """A minimal base; the World binds the real methods per instance."""

    def __init__(self, *a, **k):
        pass


_hass_mod.Hass = _Hass
sys.modules["hassapi"] = _hass_mod

APPS = Path(__file__).resolve().parent.parent / "apps"
APP_DIR = APPS / "fp2_companion"
# AppDaemon puts the app folder on sys.path, so the app imports its sibling
# modules by name. The same is done here.
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _load(module_name, rel_path):
    path = APPS / rel_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class World:
    """In-memory Home Assistant: states, attributes and recorded calls."""

    def __init__(self):
        self.states = {}
        self.attrs = {}
        self.calls = []      # (service, entity, data)
        self.logs = []

    def get_state(self, entity, attribute=None):
        if attribute is None:
            return self.states.get(entity)
        if attribute == "all":
            return {"state": self.states.get(entity),
                    "attributes": self.attrs.get(entity, {})}
        return self.attrs.get(entity, {}).get(attribute)

    def call_service(self, service, **data):
        self.calls.append((service, data.get("entity_id"), data))

    def log(self, *a, **k):
        self.logs.append(" ".join(str(x) for x in a))

    def run_every(self, *a, **k):
        pass

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

    def listen_event(self, callback=None, event=None, **k):
        """Recorded with its keywords: the MQTT plugin publishes its events
        in its own namespace, so a listener without namespace="mqtt" never
        fires, and only the registration shows it."""
        self.listeners = getattr(self, "listeners", [])
        self.listeners.append({"callback": callback, "event": event, **k})

    def listen_state(self, *a, **k):
        pass


def _bind(app, world):
    for name in ("get_state", "call_service", "log", "run_every", "run_in",
                 "run_daily", "cancel_timer", "listen_event", "listen_state"):
        setattr(app, name, getattr(world, name))


def mqtt_payload(world, topic):
    """The last payload published to `topic`, or None."""
    for service, _entity, data in reversed(world.calls):
        if service == "mqtt/publish" and data.get("topic") == topic:
            return data.get("payload")
    return None
