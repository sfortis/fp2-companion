"""Home Assistant fixtures for the integration's own tests."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/fp2_companion from this repository."""
    yield
