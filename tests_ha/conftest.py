"""Home Assistant fixtures for the integration's own tests."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/fp2_companion from this repository."""
    yield


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """A config folder of the test's own: the integration writes its state
    and looks for the AppDaemon app in it."""
    return hass_tmp_config_dir
