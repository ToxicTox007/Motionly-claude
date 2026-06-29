import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.core.config import get_settings


@pytest.mark.parametrize("app_env", ["production", "staging", "preview", "test", "prodution", ""])
def test_skip_api_auth_fails_closed_outside_exact_local_envs(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    if app_env:
        monkeypatch.setenv("APP_ENV", app_env)
    else:
        monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("SKIP_API_AUTH", "true")
    if app_env not in {"", "production"}:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    get_settings.cache_clear()

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    assert "SKIP_API_AUTH can only be enabled" in str(exc_info.value)


@pytest.mark.parametrize("app_env", ["local", "development"])
def test_skip_api_auth_allowed_only_in_exact_local_envs(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("SKIP_API_AUTH", "true")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.app_env == app_env


@pytest.mark.parametrize("truthy_value", ["1", "yes", "on", "TRUE", "Yes"])
def test_skip_api_auth_truthy_values_fail_in_production(
    monkeypatch: pytest.MonkeyPatch,
    truthy_value: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SKIP_API_AUTH", truthy_value)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    get_settings.cache_clear()

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    assert "SKIP_API_AUTH can only be enabled" in str(exc_info.value)


def test_production_config_with_normal_auth_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SKIP_API_AUTH", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.example.com")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.app_env == "production"


@pytest.mark.parametrize("app_env", ["production", "staging", "test", "preview"])
def test_missing_jwt_secret_fails_closed_outside_local_envs(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.delenv("SKIP_API_AUTH", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    get_settings.cache_clear()

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    assert "SUPABASE_JWT_SECRET is required outside local development" in str(exc_info.value)


def test_membership_check_config_required_outside_local_envs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.delenv("SKIP_API_AUTH", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    get_settings.cache_clear()

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    assert "verify workspace membership" in str(exc_info.value)


def test_unsafe_skip_api_auth_config_exits_nonzero() -> None:
    env = {
        **os.environ,
        "APP_ENV": "production",
        "SKIP_API_AUTH": "true",
        "SUPABASE_JWT_SECRET": "test-secret",
    }

    result = subprocess.run(
        [sys.executable, "-c", "from app.core.config import get_settings; get_settings()"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "SKIP_API_AUTH can only be enabled" in (result.stderr + result.stdout)
