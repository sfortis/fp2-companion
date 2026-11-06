"""Lets the tools import the integration's modules without Home Assistant.

The modules import each other relatively, as parts of the package
custom_components.fp2_companion, whose __init__.py needs Home Assistant. So
the package is registered by its folder only, as tests/conftest.py does.
Each tool imports this first; the tools folder is on sys.path as the folder
of the script being run.
"""
import pathlib
import sys
import types

PACKAGE = "custom_components.fp2_companion"
PACKAGE_DIR = pathlib.Path(__file__).resolve().parent.parent

for _name, _path in (("custom_components", PACKAGE_DIR.parent),
                     (PACKAGE, PACKAGE_DIR)):
    if _name not in sys.modules:
        _module = types.ModuleType(_name)
        _module.__path__ = [str(_path)]
        sys.modules[_name] = _module

# Where the integration keeps the session and the backups: .storage in Home
# Assistant's config folder, two levels above custom_components/fp2_companion.
STATE_DIR = str(PACKAGE_DIR.parent.parent / ".storage" / "fp2_companion")
