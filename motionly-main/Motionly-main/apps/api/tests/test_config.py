import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def ignore_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTINGS_ENV_FILE", "none")
    get_settings.cache_clear()


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


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WORKFLOW_DISPATCHER_BATCH_SIZE", "0"),
        ("WORKFLOW_DISPATCHER_BATCH_SIZE", "101"),
        ("WORKFLOW_DISPATCHER_LEASE_SECONDS", "4"),
        ("WORKFLOW_DISPATCHER_LEASE_SECONDS", "901"),
        ("WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS", "0"),
        ("WORKFLOW_DISPATCHER_BACKOFF_INITIAL_SECONDS", "0"),
        ("WORKFLOW_DISPATCHER_BACKOFF_MAX_SECONDS", "3601"),
        ("WORKFLOW_RECONCILER_INTERVAL_SECONDS", "4"),
        ("WORKFLOW_RECONCILER_STALENESS_SECONDS", "29"),
        ("WORKFLOW_RECONCILER_BATCH_SIZE", "101"),
        ("WORKFLOW_STEP_LEASE_SECONDS", "4"),
        ("WORKFLOW_STEP_LEASE_SECONDS", "3601"),
        ("WORKFLOW_STEP_RESULT_MAX_BYTES", "1023"),
        ("WORKFLOW_STEP_RESULT_MAX_BYTES", "1048577"),
    ],
)
def test_workflow_runtime_settings_reject_unsafe_bounds(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    with pytest.raises(ValidationError):
        get_settings()


def test_temporal_api_key_supports_file_secret_loading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    secret_path = tmp_path / "temporal-api-key"
    secret_path.write_text("temporal-secret-from-file\n", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("TEMPORAL_API_KEY", raising=False)
    monkeypatch.setenv("TEMPORAL_API_KEY_FILE", str(secret_path))
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.temporal_api_key == "temporal-secret-from-file"
    os.environ.pop("TEMPORAL_API_KEY", None)
