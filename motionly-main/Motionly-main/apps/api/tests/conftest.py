import os

import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def default_local_app_env(monkeypatch: pytest.MonkeyPatch):
    if "APP_ENV" not in os.environ:
        monkeypatch.setenv("APP_ENV", "local")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
