import asyncio
from types import SimpleNamespace

from fastapi import Response

from app.api.health import DependencyHealth, _probe_renderer, dependencies, live, ready
from app.core.config import get_settings


class EmptyQueueRegistry:
    def snapshots(self):
        return []


def make_request(request_id: str = "req_test", *, settings=None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings or get_settings(),
                queue_registry=EmptyQueueRegistry(),
            )
        ),
        state=SimpleNamespace(request_id=request_id),
    )


def test_live_health_returns_request_id() -> None:
    response = asyncio.run(live(make_request()))

    assert response.model_dump() == {
        "status": "ok",
        "service": "motionly-api",
        "environment": get_settings().app_env,
        "request_id": "req_test",
    }


def test_ready_health_is_ok(monkeypatch, tmp_path) -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "redis_url": None,
            "supabase_url": None,
            "supabase_service_role_key": None,
            "media_work_dir": str(tmp_path),
            "openrouter_api_key": None,
            "groq_api_key": None,
            "pexels_api_key": None,
            "pixabay_api_key": None,
            "runway_api_key": None,
        }
    )
    async def fake_probe_renderer(_settings):
        return DependencyHealth(status="ok", message="Mocked renderer.")
    monkeypatch.setattr("app.api.health._probe_renderer", fake_probe_renderer)
    http_response = Response()
    response = asyncio.run(
        ready(make_request("req_ready", settings=settings), http_response)
    )

    assert response.status == "ok"
    assert response.service == "motionly-api"
    assert response.request_id == "req_ready"
    assert http_response.status_code == 200
    assert set(response.dependencies) == {
        "redis",
        "supabase",
        "storage",
        "renderer",
        "workers",
    }


def test_ready_health_returns_503_when_a_dependency_is_down(monkeypatch, tmp_path) -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "redis_url": None,
            "supabase_url": None,
            "supabase_service_role_key": None,
            "media_work_dir": str(tmp_path),
            "openrouter_api_key": None,
            "groq_api_key": None,
            "pexels_api_key": None,
            "pixabay_api_key": None,
            "runway_api_key": None,
        }
    )

    async def renderer_down(_settings):
        return DependencyHealth(status="down", message="Renderer unavailable.")

    monkeypatch.setattr("app.api.health._probe_renderer", renderer_down)
    http_response = Response()
    result = asyncio.run(ready(make_request(settings=settings), http_response))

    assert result.status == "not_ready"
    assert http_response.status_code == 503


def test_renderer_readiness_requires_worker_queue_in_production() -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "production",
            "redis_url": None,
            "render_execute_ffmpeg": True,
            "remotion_enabled": False,
        }
    )

    result = asyncio.run(_probe_renderer(settings))

    assert result.status == "down"
    assert "REDIS_URL" in result.message


def test_ready_health_does_not_probe_third_party_providers(monkeypatch, tmp_path) -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "redis_url": None,
            "supabase_url": None,
            "supabase_service_role_key": None,
            "media_work_dir": str(tmp_path),
            "openrouter_api_key": "configured",
        }
    )

    async def provider_probe_should_not_run(_settings):
        raise AssertionError("provider probes do not belong in readiness")

    monkeypatch.setattr("app.api.health._probe_providers", provider_probe_should_not_run)
    http_response = Response()
    result = asyncio.run(ready(make_request(settings=settings), http_response))

    assert result.status == "ok"
    assert "providers" not in result.dependencies


def test_dependencies_reports_provider_probe_failures_as_degraded(monkeypatch) -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "redis_url": None,
            "supabase_url": None,
            "supabase_service_role_key": None,
            "openrouter_api_key": "configured",
        }
    )

    async def provider_probe_down(_settings):
        return DependencyHealth(status="down", message="Provider auth failed.")

    monkeypatch.setattr("app.api.health._probe_providers", provider_probe_down)
    result = asyncio.run(dependencies(make_request(settings=settings)))

    assert result.status == "degraded"
    assert result.dependencies["providers"].status == "degraded"
