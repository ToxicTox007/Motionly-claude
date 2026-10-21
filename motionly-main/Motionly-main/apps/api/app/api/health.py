import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.dependencies import Actor, get_actor
from app.api.errors import ApiError

router = APIRouter(prefix="/health", tags=["health"])
ActorDep = Annotated[Actor, Depends(get_actor)]


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    # "not_enabled": GPU-dependent capabilities are labelled CPU-stage fixtures.
    gpu_stage: Literal["enabled", "not_enabled"]
    request_id: str


class DependencyHealth(BaseModel):
    status: str
    message: str
    metadata: dict[str, object] = Field(default_factory=dict)


class DependencyHealthResponse(HealthResponse):
    dependencies: dict[str, DependencyHealth]


class ComponentHealth(BaseModel):
    status: str
    message: str


class DeepHealthResponse(HealthResponse):
    components: dict[str, ComponentHealth]


@dataclass
class _DiagnosticsSnapshot:
    created_at: float
    checks: dict[str, DependencyHealth]


class HealthDiagnosticsCache:
    """Per-process cache that coalesces expensive diagnostics probes."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = max(0.0, ttl_seconds)
        self._snapshot: _DiagnosticsSnapshot | None = None
        self._lock = asyncio.Lock()

    async def get_or_refresh(
        self,
        refresh: Callable[[], Awaitable[dict[str, DependencyHealth]]],
    ) -> dict[str, DependencyHealth]:
        snapshot = self._fresh_snapshot()
        if snapshot is not None:
            return _copy_health_checks(snapshot.checks)
        async with self._lock:
            snapshot = self._fresh_snapshot()
            if snapshot is None:
                snapshot = _DiagnosticsSnapshot(created_at=monotonic(), checks=await refresh())
                self._snapshot = snapshot
            return _copy_health_checks(snapshot.checks)

    def _fresh_snapshot(self) -> _DiagnosticsSnapshot | None:
        snapshot = self._snapshot
        if (
            self.ttl_seconds <= 0
            or snapshot is None
            or monotonic() - snapshot.created_at >= self.ttl_seconds
        ):
            return None
        return snapshot


def _copy_health_checks(
    checks: dict[str, DependencyHealth],
) -> dict[str, DependencyHealth]:
    return {name: check.model_copy(deep=True) for name, check in checks.items()}


@router.get("/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    """Public liveness: no dependency, filesystem, or network probes."""
    return _public_health_response(request)


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    """Public readiness is intentionally process-local and inexpensive.

    Dependency checks belong to the authenticated diagnostics routes so a load
    balancer cannot amplify work against Redis, Supabase, storage, or providers.
    """
    return _public_health_response(request)


def _public_health_response(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        service="motionly-api",
        environment=settings.app_env,
        gpu_stage=settings.gpu_stage,
        request_id=request.state.request_id,
    )


@router.get("/dependencies", response_model=DependencyHealthResponse)
async def dependencies(request: Request, actor: ActorDep) -> DependencyHealthResponse:
    await _allow_diagnostics_request(request, actor)
    checks = await _cached_dependency_checks(request)
    aggregate = "ok" if all(item.status == "ok" for item in checks.values()) else "degraded"
    return DependencyHealthResponse(
        status=aggregate,
        service="motionly-api",
        environment=request.app.state.settings.app_env,
        gpu_stage=request.app.state.settings.gpu_stage,
        request_id=request.state.request_id,
        dependencies=checks,
    )


@router.get("/deep", response_model=DeepHealthResponse)
async def deep(request: Request, actor: ActorDep) -> DeepHealthResponse:
    await _allow_diagnostics_request(request, actor)
    checks = await _cached_dependency_checks(request)
    components = {
        name: ComponentHealth(status=check.status, message=check.message)
        for name, check in checks.items()
    }
    aggregate = "ok" if all(item.status == "ok" for item in components.values()) else "degraded"
    return DeepHealthResponse(
        status=aggregate,
        service="motionly-api",
        environment=request.app.state.settings.app_env,
        gpu_stage=request.app.state.settings.gpu_stage,
        request_id=request.state.request_id,
        components=components,
    )


async def _allow_diagnostics_request(request: Request, actor: Actor) -> None:
    """Throttle diagnostics independently from ordinary API requests."""
    settings = request.app.state.settings
    limiter = request.app.state.rate_limiter
    limit = settings.health_diagnostics_rate_limit_per_minute
    allowed, _remaining = await limiter.allow(
        key=f"health-diagnostics:user:{actor.user_id}",
        limit=limit,
    )
    if not allowed:
        raise ApiError(
            429,
            "health_diagnostics_rate_limit_exceeded",
            "Too many diagnostics requests. Try again shortly.",
        )


def _diagnostics_cache(request: Request) -> HealthDiagnosticsCache:
    cache = getattr(request.app.state, "health_diagnostics_cache", None)
    if cache is None:
        cache = HealthDiagnosticsCache(
            request.app.state.settings.health_diagnostics_cache_ttl_seconds
        )
        request.app.state.health_diagnostics_cache = cache
    return cache


async def _cached_dependency_checks(request: Request) -> dict[str, DependencyHealth]:
    return await _diagnostics_cache(request).get_or_refresh(
        lambda: _collect_dependency_checks(request)
    )


async def _collect_dependency_checks(request: Request) -> dict[str, DependencyHealth]:
    settings = request.app.state.settings
    names = ("redis", "supabase", "storage", "renderer", "workers", "temporal", "providers")
    results = await asyncio.gather(
        _probe_redis(settings),
        _probe_supabase_ready(settings),
        _probe_storage(settings),
        _probe_renderer(settings),
        _probe_workers(request),
        _probe_temporal(settings),
        _provider_dependency_health(settings),
        return_exceptions=True,
    )
    checks = {
        name: (
            result
            if isinstance(result, DependencyHealth)
            else DependencyHealth(status="down", message=str(result)[:300])
        )
        for name, result in zip(names, results, strict=True)
    }
    checks = {
        "api": DependencyHealth(status="ok", message="API process is responding."),
        **checks,
    }
    audio_ml_providers = getattr(request.app.state, "audio_ml_providers", None)
    if not audio_ml_providers:
        checks["audio_ml"] = DependencyHealth(
            status="degraded",
            message="Audio ML providers are disabled.",
        )
    else:
        states = {}
        all_ok = True
        for name, provider in audio_ml_providers.items():
            if hasattr(provider, "check_availability"):
                ok, reason = provider.check_availability()
                states[name] = {"available": ok, "status": reason}
                if not ok:
                    all_ok = False
            else:
                states[name] = {"available": True, "status": "unknown"}
        checks["audio_ml"] = DependencyHealth(
            status="ok" if all_ok else "degraded",
            message="Audio ML dependencies check.",
            metadata=states,
        )
    return checks


async def _probe_temporal(settings) -> DependencyHealth:
    from app.orchestration.client import TemporalConnectionConfig, connect_temporal_client

    target = str(getattr(settings, "temporal_address", "") or "").strip()
    if not target:
        if settings.app_env != "production":
            return DependencyHealth(status="ok", message="Temporal is optional outside production.")
        return DependencyHealth(status="down", message="Temporal is not configured.")
    try:
        config = TemporalConnectionConfig.from_settings(settings)
        client = await asyncio.wait_for(
            connect_temporal_client(config), timeout=settings.readiness_timeout_seconds
        )
        healthy = await asyncio.wait_for(
            client.service_client.check_health(), timeout=settings.readiness_timeout_seconds
        )
        if not healthy:
            return DependencyHealth(
                status="down", message="Temporal secure health RPC was unavailable."
            )
    except Exception:
        # TLS, mTLS, authorization, and transport errors can include a target,
        # certificate path, or peer detail. Keep diagnostics safe for operators.
        return DependencyHealth(status="down", message="Temporal secure health probe failed.")
    return DependencyHealth(status="ok", message="Temporal mTLS health RPC succeeded.")


async def _probe_redis(settings) -> DependencyHealth:
    if not settings.redis_url:
        if settings.app_env != "production":
            return DependencyHealth(status="ok", message="Local in-memory queue mode.")
        return DependencyHealth(status="down", message="Redis is not configured.")
    from redis.asyncio import Redis

    client = Redis.from_url(settings.redis_url)
    try:
        await asyncio.wait_for(client.ping(), timeout=settings.readiness_timeout_seconds)
    finally:
        await client.aclose()
    return DependencyHealth(status="ok", message="Redis PING succeeded.")


async def _probe_supabase_ready(settings) -> DependencyHealth:
    if settings.app_env != "production" and (
        not settings.supabase_url or not settings.supabase_service_role_key
    ):
        return DependencyHealth(status="ok", message="Local in-memory repository mode.")
    result = await _supabase_health(settings)
    if result.status != "ok":
        result.status = "down"
    return result


async def _probe_storage(settings) -> DependencyHealth:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        if settings.app_env != "production":
            root = Path(settings.media_work_dir)
            root.mkdir(parents=True, exist_ok=True)
            probe = root / f".ready-{uuid4().hex}"
            probe.write_bytes(b"ready")
            probe.unlink()
            return DependencyHealth(status="ok", message="Local storage write succeeded.")
        return DependencyHealth(status="down", message="Storage is not configured.")
    async with httpx.AsyncClient(timeout=settings.readiness_timeout_seconds) as client:
        result = await client.get(
            f"{str(settings.supabase_url).rstrip('/')}/storage/v1/bucket",
            headers={
                "apikey": settings.supabase_service_role_key,
                "authorization": f"Bearer {settings.supabase_service_role_key}",
            },
        )
    if result.status_code >= 400:
        return DependencyHealth(
            status="down", message=f"Storage responded with HTTP {result.status_code}."
        )
    return DependencyHealth(status="ok", message="Storage API responded.")


async def _probe_renderer(settings) -> DependencyHealth:
    if settings.redis_url or settings.app_env != "production":
        return DependencyHealth(
            status="ok",
            message="Media rendering is delegated to worker runtimes.",
        )
    return DependencyHealth(
        status="down",
        message="Renderer workers require REDIS_URL in production.",
    )


async def _probe_providers(settings) -> DependencyHealth:
    probes: list[tuple[str, str, dict[str, str]]] = []
    if settings.openrouter_api_key:
        probes.append(
            (
                "openrouter",
                "https://openrouter.ai/api/v1/models",
                {"authorization": f"Bearer {settings.openrouter_api_key}"},
            )
        )
    if settings.groq_api_key:
        probes.append(
            (
                "groq",
                "https://api.groq.com/openai/v1/models",
                {"authorization": f"Bearer {settings.groq_api_key}"},
            )
        )
    if settings.pexels_api_key:
        probes.append(
            (
                "pexels",
                "https://api.pexels.com/v1/curated?per_page=1",
                {"authorization": settings.pexels_api_key},
            )
        )
    if settings.pixabay_api_key:
        probes.append(
            (
                "pixabay",
                f"https://pixabay.com/api/videos/?key={settings.pixabay_api_key}&per_page=3",
                {},
            )
        )
    if settings.runway_api_key:
        probes.append(
            (
                "runway",
                "https://api.dev.runwayml.com/v1/tasks",
                {
                    "authorization": f"Bearer {settings.runway_api_key}",
                    "x-runway-version": "2024-11-06",
                },
            )
        )
    if not probes:
        return DependencyHealth(status="ok", message="No remote providers configured.")
    statuses: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=settings.readiness_timeout_seconds) as client:
        for name, url, headers in probes:
            result = await client.get(url, headers=headers)
            statuses[name] = result.status_code
    if any(status >= 500 or status in {401, 403} for status in statuses.values()):
        return DependencyHealth(
            status="down", message="A provider probe failed.", metadata=statuses
        )
    return DependencyHealth(status="ok", message="Provider probes responded.", metadata=statuses)


async def _probe_workers(request: Request) -> DependencyHealth:
    settings = request.app.state.settings
    if settings.app_env != "production" and not settings.redis_url:
        return DependencyHealth(status="ok", message="Workers are optional in local mode.")
    heartbeats = await asyncio.to_thread(request.app.state.operations_repository.latest_heartbeats)
    cutoff = datetime.now(UTC).timestamp() - settings.worker_heartbeat_max_age_seconds
    live = [item for item in heartbeats if item.last_seen_at.timestamp() >= cutoff]
    if not live:
        return DependencyHealth(status="down", message="No current worker heartbeat.")
    return DependencyHealth(
        status="ok", message="Current worker heartbeat found.", metadata={"workers": len(live)}
    )


async def _provider_dependency_health(settings) -> DependencyHealth:
    try:
        result = await _probe_providers(settings)
    except Exception as exc:
        return DependencyHealth(
            status="degraded",
            message=f"Provider probe failed: {str(exc)[:240]}",
        )
    if result.status == "down":
        result.status = "degraded"
    return result


async def _supabase_health(settings) -> DependencyHealth:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return DependencyHealth(
            status="degraded",
            message="Supabase URL or service role key is not configured.",
        )
    try:
        async with httpx.AsyncClient(timeout=settings.readiness_timeout_seconds) as client:
            response = await client.get(
                f"{str(settings.supabase_url).rstrip('/')}/rest/v1/",
                headers={
                    "apikey": settings.supabase_service_role_key,
                    "authorization": f"Bearer {settings.supabase_service_role_key}",
                },
            )
    except httpx.HTTPError as exc:
        return DependencyHealth(status="degraded", message=str(exc)[:300])
    if response.status_code >= 400:
        return DependencyHealth(
            status="degraded",
            message=f"Supabase responded with HTTP {response.status_code}.",
        )
    return DependencyHealth(
        status="ok",
        message="Supabase REST endpoint responded.",
        metadata={"status_code": response.status_code},
    )
