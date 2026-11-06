"""Test harness for the fp2_companion app.

Imports the package without Home Assistant, and provides `World`, a fake
runtime that records the devices the app announces, the changes it signals,
its live messages and its timers. The tests read the state itself off the
app's objects, as the entities do.
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
    """The runtime's side, in memory: recorded signals, logs and timers."""

    def __init__(self):
        self.added = []      # device keys, in the order they were announced
        self.changes = []    # device keys that changed; None for the app
        self.live = []       # (device key, message)
        self.logs = []

    def added_device(self, device):
        self.added.append(device.key)

    def changed(self, key):
        self.changes.append(key)

    def live_message(self, key, message):
        self.live.append((key, message))

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


def _bind(app, world):
    for name in ("log", "run_in", "run_daily", "cancel_timer", "changed"):
        setattr(app, name, getattr(world, name))
    app.added = world.added_device
    app.live = world.live_message
