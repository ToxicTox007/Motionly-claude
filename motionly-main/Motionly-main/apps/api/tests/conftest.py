import os

import pytest

# Test collection imports app.main before fixtures run. Override repository and
# inherited shell defaults so collection is hermetic and authentication fails
# closed. Individual tests can still opt in with monkeypatch after collection.
os.environ["APP_ENV"] = "local"
os.environ["SKIP_API_AUTH"] = "false"

from app.core.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def default_local_app_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "local")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
