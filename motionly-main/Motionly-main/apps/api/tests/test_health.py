import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.routing import APIRoute

from app.api.dependencies import Actor, get_actor
from app.api.errors import ApiError
from app.api.health import (
    DependencyHealth,
    _probe_renderer,
    _probe_temporal,
    dependencies,
    live,
    ready,
    router,
)
from app.core.config import get_settings
from app.core.middleware import InMemoryRateLimiter


class EmptyQueueRegistry:
    def snapshots(self):
        return []


def make_request(request_id: str = "req_test", *, settings=None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings or get_settings(),
                queue_registry=EmptyQueueRegistry(),
                rate_limiter=InMemoryRateLimiter(),
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


def test_public_readiness_is_cheap_and_does_not_probe_dependencies(monkeypatch, tmp_path) -> None:
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

    async def unexpected_probe(*_args):
        raise AssertionError("public readiness must not probe dependencies")

    monkeypatch.setattr("app.api.health._probe_redis", unexpected_probe)
    monkeypatch.setattr("app.api.health._probe_providers", unexpected_probe)
    response = asyncio.run(ready(make_request("req_ready", settings=settings)))

    assert response.model_dump() == {
        "status": "ok",
        "service": "motionly-api",
        "environment": "test",
        "request_id": "req_ready",
    }


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


def test_deep_diagnostics_routes_require_workspace_authentication() -> None:
    protected_routes = [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path in {"/health/dependencies", "/health/deep"}
    ]

    assert len(protected_routes) == 2
    for route in protected_routes:
        assert get_actor in {dependency.call for dependency in route.dependant.dependencies}


def test_temporal_health_uses_secure_client_rpc_and_redacts_transport_errors(monkeypatch) -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "production",
            "temporal_deployment_mode": "self_hosted",
            "temporal_address": "temporal:7233",
            "temporal_namespace": "motionly",
            "temporal_task_queue": "motionly-orchestration",
            "temporal_tls_enabled": True,
            "temporal_server_root_ca_path": "/run/secrets/temporal_ca_cert",
            "temporal_client_cert_path": "/run/secrets/temporal_api_cert",
            "temporal_client_private_key_path": "/run/secrets/temporal_api_key",
            "temporal_server_name": "temporal",
            "temporal_client_role": "api",
            "temporal_identity": "motionly-api",
        }
    )
    calls = []

    class ServiceClient:
        async def check_health(self):
            calls.append("health")
            return True

    class Client:
        service_client = ServiceClient()

    async def connect(config):
        calls.append(config)
        return Client()

    monkeypatch.setattr("app.orchestration.client.connect_temporal_client", connect)
    result = asyncio.run(_probe_temporal(settings))
    assert result.status == "ok"
    assert result.message == "Temporal mTLS health RPC succeeded."
    assert calls[0].server_name == "temporal"
    assert calls[0].client_cert_path == "/run/secrets/temporal_api_cert"
    assert calls[1] == "health"

    async def rejected(_config):
        raise RuntimeError("private key /run/secrets/temporal_api_key was rejected")

    monkeypatch.setattr("app.orchestration.client.connect_temporal_client", rejected)
    rejected_result = asyncio.run(_probe_temporal(settings))
    assert rejected_result.status == "down"
    assert rejected_result.message == "Temporal secure health probe failed."
    assert "key" not in rejected_result.message.lower()


def test_authenticated_diagnostics_are_cached_and_independently_rate_limited(monkeypatch) -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "health_diagnostics_cache_ttl_seconds": 30,
            "health_diagnostics_rate_limit_per_minute": 2,
        }
    )
    request = make_request(settings=settings)
    actor = Actor(
        user_id=UUID("00000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("00000000-0000-4000-8000-000000000002"),
    )
    calls = 0

    async def collect(_request):
        nonlocal calls
        calls += 1
        return {"api": DependencyHealth(status="ok", message="Mocked API.")}

    monkeypatch.setattr("app.api.health._collect_dependency_checks", collect)

    async def exercise() -> None:
        first = await dependencies(request, actor)
        second = await dependencies(request, actor)
        assert first.status == "ok"
        assert second.status == "ok"
        with pytest.raises(ApiError) as exc_info:
            await dependencies(request, actor)
        assert exc_info.value.status_code == 429
        assert exc_info.value.code == "health_diagnostics_rate_limit_exceeded"

    asyncio.run(exercise())
    assert calls == 1
