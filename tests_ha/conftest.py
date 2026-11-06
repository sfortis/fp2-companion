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


@pytest.fixture(autouse=True)
def no_cloud(monkeypatch):
    """No test reaches Aqara: every request fails as an unreachable cloud."""
    from custom_components.fp2_companion import aqara_cloud

    def offline(self, method, endpoint, *args, **kwargs):
        raise aqara_cloud.AqaraError(endpoint, None, "offline in the tests")

    monkeypatch.setattr(aqara_cloud.AqaraCloud, "_send", offline)


def write_session(hass) -> None:
    """A session whose token is valid for years, so nothing refreshes it."""
    import base64
    import json
    import os

    claims = base64.urlsafe_b64encode(
        json.dumps({"exp": 4102444800}).encode()).decode().rstrip("=")
    folder = hass.config.path(".storage", "fp2_companion")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "session.json"), "w") as fh:
        json.dump({"token": f"e30.{claims}.sig", "user_id": "u",
                   "account": "j.walker@example.com",
                   "country": "GR", "phone_id": "P", "host": "example.invalid",
                   "appid": "id", "appkey": "key", "headers": {}}, fh)


@pytest.fixture
async def session(hass):
    """The Aqara session the integration needs to start."""
    await hass.async_add_executor_job(write_session, hass)
