"""ML_EXECUTION_MODE decides where GPU-dependent capabilities run.

``gpu`` (default): on the GPU workers of the GPU stage. ``test_fixture``: on CPU
workers with labelled deterministic fixtures, for the CPU stage only. Production
refuses ``test_fixture``, the visual and matte runners refuse to load real
models in it, and health and admin output say the GPU stage is not enabled.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import Actor, get_actor
from app.core.config import Settings, get_settings
from app.main import _validate_production_settings, create_app
from tests.test_service_hardening import _production_settings

ACTOR = Actor(
    user_id=UUID("11111111-1111-4111-8111-111111111111"),
    workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
)


def test_ml_execution_mode_defaults_to_gpu_and_rejects_unknown_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ML_EXECUTION_MODE", raising=False)
    assert Settings(_env_file=None).ml_execution_mode == "gpu"
    monkeypatch.setenv("ML_EXECUTION_MODE", "test_fixture")
    assert Settings(_env_file=None).ml_execution_mode == "test_fixture"
    monkeypatch.setenv("ML_EXECUTION_MODE", "cpu")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("process_role", ["api", "worker"])
def test_production_refuses_test_fixture_ml_execution(process_role: str) -> None:
    _validate_production_settings(_production_settings(process_role=process_role))

    with pytest.raises(RuntimeError, match="ML_EXECUTION_MODE=test_fixture is not allowed"):
        _validate_production_settings(
            _production_settings(process_role=process_role, ml_execution_mode="test_fixture")
        )


@pytest.mark.parametrize("module_name", ["app.workers.visual_job", "app.workers.matte_job"])
def test_ml_runners_refuse_real_models_in_test_fixture_mode(
    monkeypatch: pytest.MonkeyPatch, module_name: str
) -> None:
    import importlib

    module = importlib.import_module(module_name)
    settings = Settings(
        _env_file=None,
        APP_ENV="cpu-stage",
        ML_EXECUTION_MODE="test_fixture",
        VISUAL_PROVIDER="local_ml",
        SUPABASE_URL="http://supabase.invalid",
        SUPABASE_SERVICE_ROLE_KEY="service-role",
        SUPABASE_JWT_SECRET="jwt-secret",
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="requires VISUAL_PROVIDER=fixture"):
        module._runner()


@pytest.mark.parametrize(
    ("mode", "gpu_stage"), [("test_fixture", "not_enabled"), ("gpu", "enabled")]
)
def test_health_and_admin_output_report_whether_the_gpu_stage_is_enabled(
    monkeypatch: pytest.MonkeyPatch, mode: str, gpu_stage: str
) -> None:
    monkeypatch.setenv("ML_EXECUTION_MODE", mode)
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-token-for-test")
    get_settings.cache_clear()
    try:
        app = create_app()
        app.dependency_overrides[get_actor] = lambda: ACTOR
        with TestClient(app) as client:
            for path in ("/health/live", "/health/ready"):
                response = client.get(path)
                assert response.status_code == 200, response.text
                assert response.json()["gpu_stage"] == gpu_stage
            overview = client.get(
                "/admin/ops/overview",
                headers={"x-motionly-admin-token": "admin-token-for-test"},
            )
            assert overview.status_code == 200, overview.text
            assert overview.json()["gpu_stage"] == gpu_stage
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(("mode", "configured"), [("test_fixture", False), ("gpu", True)])
def test_gpu_autoscaler_runs_only_when_the_gpu_stage_is_enabled(
    monkeypatch: pytest.MonkeyPatch, mode: str, configured: bool
) -> None:
    monkeypatch.setenv("ML_EXECUTION_MODE", mode)
    monkeypatch.setenv("WORKER_API_TOKEN", "worker-token-for-test")
    get_settings.cache_clear()
    try:
        app = create_app()
        assert (app.state.gpu_scaling_service is not None) is configured
        with TestClient(app) as client:
            response = client.post(
                "/internal/operations/gpu-autoscaling/run",
                headers={"x-motionly-worker-token": "worker-token-for-test"},
            )
        assert response.status_code == 200, response.text
        if not configured:
            assert response.json() == {"status": "disabled", "reason": "gpu_stage_not_enabled"}
    finally:
        get_settings.cache_clear()
