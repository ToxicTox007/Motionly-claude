import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.api.internal_jobs import require_worker_token
from app.api.publishing import _oauth_state_secret
from app.core.config import get_settings
from app.core.middleware import security_headers_middleware
from app.core.security import constant_time_token_matches
from app.main import _validate_production_settings

ROOT = Path(__file__).resolve().parents[3]


def _production_settings(*, process_role: str = "api", **updates):
    values = {
        "app_env": "production",
        "process_role": process_role,
        "api_public_base_url": "https://api.example.com",
        "api_cors_origins": "https://app.example.com",
        "supabase_url": "https://project.supabase.co",
        "supabase_jwt_secret": "jwt-secret",
        "supabase_service_role_key": "service-role",
        "redis_url": "redis://redis.example.com:6379",
        "worker_api_token": "worker-only-token",
        "admin_api_token": None,
        "publish_oauth_state_secret": "oauth-state-only-secret",
        "publish_token_encryption_key": "publish-token-encryption-key-with-32-bytes",
        "sentry_dsn": "https://public@example.ingest.sentry.io/1",
        "metrics_bearer_token": "metrics-token",
        "allow_fixture_providers": False,
        "stem_separation_provider": "demucs",
        "transcription_provider": "groq",
        "groq_api_key": "groq-key",
        "edit_planner_provider": "openrouter",
        "openrouter_api_key": "openrouter-key",
        "visual_intelligence_provider": "local_ml",
        "audio_ml_provider": "disabled",
        "music_provider": "licensed_catalog",
        "stock_video_provider": "pexels_pixabay",
        "pexels_api_key": "pexels-key",
        "pixabay_api_key": "pixabay-key",
        "billing_enabled": False,
        "publish_sync_completion": False,
        "force_local_media_storage": False,
    }
    values.update(updates)
    return get_settings().model_copy(update=values)


def test_constant_time_token_comparison_uses_compare_digest(monkeypatch) -> None:
    calls: list[tuple[bytes, bytes]] = []

    def spy(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr("app.core.security.compare_digest", spy)

    assert constant_time_token_matches("worker-token", "worker-token") is True
    assert calls == [(b"worker-token", b"worker-token")]


def test_internal_auth_accepts_worker_token_and_rejects_admin_token() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    worker_api_token="worker-only-token",
                    admin_api_token="admin-only-token",
                )
            )
        )
    )

    asyncio.run(require_worker_token(request, "worker-only-token"))
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(require_worker_token(request, "admin-only-token"))
    assert exc_info.value.status_code == 403


def test_publish_oauth_state_never_falls_back_to_admin_token() -> None:
    settings = SimpleNamespace(
        publish_oauth_state_secret=None,
        admin_api_token="admin-only-token",
    )

    with pytest.raises(ApiError) as exc_info:
        _oauth_state_secret(settings)
    assert exc_info.value.code == "publish_oauth_not_configured"


def test_production_api_requires_worker_and_oauth_secrets_but_not_admin_token() -> None:
    _validate_production_settings(_production_settings(admin_api_token=None))

    with pytest.raises(RuntimeError, match="WORKER_API_TOKEN"):
        _validate_production_settings(_production_settings(worker_api_token=None))
    with pytest.raises(RuntimeError, match="PUBLISH_OAUTH_STATE_SECRET"):
        _validate_production_settings(_production_settings(publish_oauth_state_secret=None))


def test_production_worker_requires_worker_token_without_admin_or_oauth_secret() -> None:
    settings = _production_settings(
        process_role="worker",
        worker_api_token="worker-only-token",
        admin_api_token=None,
        publish_oauth_state_secret=None,
    )

    _validate_production_settings(settings)
    with pytest.raises(RuntimeError, match="WORKER_API_TOKEN"):
        _validate_production_settings(settings.model_copy(update={"worker_api_token": None}))


def test_api_security_headers_are_applied_at_runtime() -> None:
    app = FastAPI()
    app.middleware("http")(security_headers_middleware)

    @app.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/probe")

    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "camera=()" in response.headers["permissions-policy"]


def test_worker_credentials_are_structurally_separated_in_runtime_config() -> None:
    for relative in (
        "apps/api/app/api/internal_jobs.py",
        "apps/api/app/api/internal_renders.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "worker_api_token" in source
        assert "admin_api_token" not in source
        assert "constant_time_token_matches" in source

    for relative in (
        "apps/worker/src/queue-common.mjs",
        "apps/api/worker-bridge/queue-common.mjs",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert 'return env("WORKER_API_TOKEN");' in source
        assert "ADMIN_API_TOKEN" not in source

    railway = (ROOT / ".railway/railway.ts").read_text(encoding="utf-8")
    worker_env = railway.split("const workerEnv = {", 1)[1].split("};", 1)[0]
    assert "WORKER_API_TOKEN: api.env.WORKER_API_TOKEN" in worker_env
    assert "ADMIN_API_TOKEN" not in worker_env

    compose = (ROOT / "infra/docker-compose.gpu.yml").read_text(encoding="utf-8")
    shared_worker_env = compose.split("x-media-environment:", 1)[1].split(
        "x-media-volumes:", 1
    )[0]
    assert "WORKER_API_TOKEN_FILE: /run/secrets/worker_api_token" in shared_worker_env
    assert "ADMIN_API_TOKEN" not in shared_worker_env
    assert "publish_oauth_state_secret" in compose


def test_runtime_containers_drop_root_and_own_writable_paths() -> None:
    dockerfiles = (
        "apps/web/Dockerfile",
        "apps/worker/Dockerfile",
        "apps/worker/Dockerfile.python",
        "apps/worker/Dockerfile.python.demucs",
        "apps/worker/Dockerfile.gpu",
        "apps/api/Dockerfile.gpu",
    )
    for relative in dockerfiles:
        source = (ROOT / relative).read_text(encoding="utf-8")
        users = re.findall(r"^USER\s+(\S+)", source, flags=re.MULTILINE)
        assert users and users[-1].lower() not in {"0", "root"}, relative
        assert "chown" in source, relative

    for relative in dockerfiles[1:]:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "--uid 10001" in source, f"{relative} must use the shared runtime UID"
        for path in ("/var/cache/motionly", "/var/data/motionly", "/tmp/motionly"):
            assert path in source, f"{relative} does not provision {path}"


def test_environment_templates_contain_only_distinct_placeholders() -> None:
    root_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    api_example = (ROOT / "apps/api/.env.example").read_text(encoding="utf-8")
    worker_example = (ROOT / "apps/worker/.env.example").read_text(encoding="utf-8")
    production_template = (ROOT / "prod.env.example").read_text(encoding="utf-8")

    assert "ADMIN_API_TOKEN=replace-with-distinct-admin-token" in root_example
    assert "WORKER_API_TOKEN=replace-with-distinct-worker-token" in root_example
    assert "PUBLISH_OAUTH_STATE_SECRET=replace-with-distinct" in root_example
    assert "WORKER_API_TOKEN=replace-with-distinct-worker-token" in api_example
    assert "WORKER_API_TOKEN=replace-with-distinct-worker-token" in worker_example
    assert "ADMIN_API_TOKEN" not in production_template
    for line in production_template.splitlines():
        if line.startswith("export ") and "=\"" in line:
            value = line.split("=\"", 1)[1].removesuffix('"')
            allowed_literals = {
                "production",
                "worker",
                "true",
                "1",
                "visual_analysis",
                "exports",
                "raw-uploads",
                "groq",
                "whisper-large-v3-turbo",
                "900000",
                "120000",
                "30000",
            }
            assert (
                value in allowed_literals
                or "replace-with" in value
                or value.startswith("https://your-")
                or value.startswith("redis://default:replace-with")
            )
