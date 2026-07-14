import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.services.queue import BullMQQueueRegistry

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
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


@router.get("/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="motionly-api",
        environment=request.app.state.settings.app_env,
        request_id=request.state.request_id,
    )


@router.get("/ready", response_model=DependencyHealthResponse)
async def ready(
    request: Request, response: Response = None  # type: ignore[assignment]
) -> DependencyHealthResponse:
    settings = request.app.state.settings
    names = ("redis", "supabase", "storage", "renderer", "workers")
    results = await asyncio.gather(
        _probe_redis(settings),
        _probe_supabase_ready(settings),
        _probe_storage(settings),
        _probe_renderer(settings),
        _probe_workers(request),
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
    ready_status = all(item.status == "ok" for item in checks.values())
    if not ready_status and response is not None:
        response.status_code = 503
    return DependencyHealthResponse(
        status="ok" if ready_status else "not_ready",
        service="motionly-api",
        environment=settings.app_env,
        request_id=request.state.request_id,
        dependencies=checks,
    )


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
    if (
        settings.app_env != "production"
        and (not settings.supabase_url or not settings.supabase_service_role_key)
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
    if not settings.render_execute_ffmpeg and not settings.remotion_enabled:
        return DependencyHealth(status="ok", message="Render execution is simulated.")
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
    heartbeats = await asyncio.to_thread(
        request.app.state.operations_repository.latest_heartbeats
    )
    cutoff = datetime.now(UTC).timestamp() - settings.worker_heartbeat_max_age_seconds
    live = [item for item in heartbeats if item.last_seen_at.timestamp() >= cutoff]
    if not live:
        return DependencyHealth(status="down", message="No current worker heartbeat.")
    return DependencyHealth(
        status="ok", message="Current worker heartbeat found.", metadata={"workers": len(live)}
    )


@router.get("/dependencies", response_model=DependencyHealthResponse)
async def dependencies(request: Request) -> DependencyHealthResponse:
    settings = request.app.state.settings
    checks = {
        "api": DependencyHealth(status="ok", message="API process is responding."),
        "worker": _worker_health(settings),
        "renderer": _renderer_health(settings),
        "redis": _redis_dependency_health(request),
        "supabase": await _supabase_health(settings),
        "providers": await _provider_dependency_health(settings),
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
        status_str = "ok" if all_ok else "degraded"
        checks["audio_ml"] = DependencyHealth(
            status=status_str,
            message="Audio ML dependencies check.",
            metadata=states,
        )
    aggregate = "ok" if all(item.status == "ok" for item in checks.values()) else "degraded"
    return DependencyHealthResponse(
        status=aggregate,
        service="motionly-api",
        environment=settings.app_env,
        request_id=request.state.request_id,
        dependencies=checks,
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


def _worker_health(settings) -> DependencyHealth:
    if not settings.redis_url:
        return DependencyHealth(
            status="degraded",
            message="REDIS_URL is not configured; background workers cannot consume BullMQ jobs.",
        )
    return DependencyHealth(status="ok", message="Worker queue configuration is present.")


def _renderer_health(settings) -> DependencyHealth:
    if not settings.remotion_enabled:
        return DependencyHealth(status="ok", message="Remotion renderer is disabled.")
    if settings.redis_url and settings.worker_api_token:
        return DependencyHealth(
            status="ok",
            message="Worker-backed Remotion renderer is enabled.",
        )
    renderer_root = Path(__file__).resolve().parents[3] / "renderer"
    missing = [
        str(path)
        for path in [
            renderer_root / "package.json",
            renderer_root / "src" / "render-cli.ts",
        ]
        if not path.exists()
    ]
    if missing:
        return DependencyHealth(
            status="degraded",
            message="Remotion renderer files are missing.",
            metadata={"missing": missing},
        )
    return DependencyHealth(status="ok", message="Remotion renderer files are present.")


def _redis_dependency_health(request: Request) -> DependencyHealth:
    registry = request.app.state.queue_registry
    try:
        snapshots = registry.snapshots()
    except Exception as exc:  # pragma: no cover - defensive ops surface
        return DependencyHealth(status="degraded", message=str(exc)[:300])
    return DependencyHealth(
        status="ok",
        message="Queue registry responded.",
        metadata={"queues": len(snapshots)},
    )


async def _supabase_health(settings) -> DependencyHealth:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return DependencyHealth(
            status="degraded",
            message="Supabase URL or service role key is not configured.",
        )
    try:
        async with httpx.AsyncClient(timeout=5) as client:
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


@router.get("/deep", response_model=DeepHealthResponse)
async def deep(request: Request) -> DeepHealthResponse:
    settings = request.app.state.settings
    components = {
        "api": ComponentHealth(status="ok", message="API process is accepting requests."),
        "worker": ComponentHealth(
            status="ok" if settings.worker_api_token else "degraded",
            message=(
                "Worker token is configured."
                if settings.worker_api_token
                else "Worker token is not configured."
            ),
        ),
        "renderer": ComponentHealth(
            status=(
                "ok" if settings.render_execute_ffmpeg or settings.remotion_enabled else "degraded"
            ),
            message=(
                "Render execution is enabled."
                if settings.render_execute_ffmpeg or settings.remotion_enabled
                else "Render execution is simulated."
            ),
        ),
        "redis": _redis_component_health(request),
        "supabase": ComponentHealth(
            status=(
                "ok" if settings.supabase_url and settings.supabase_service_role_key else "degraded"
            ),
            message=(
                "Supabase URL and service role are configured."
                if settings.supabase_url and settings.supabase_service_role_key
                else "Supabase is not configured; using in-memory repositories where allowed."
            ),
        ),
    }
    aggregate = "ok" if all(item.status == "ok" for item in components.values()) else "degraded"
    return DeepHealthResponse(
        status=aggregate,
        service="motionly-api",
        environment=settings.app_env,
        request_id=request.state.request_id,
        components=components,
    )


def _redis_component_health(request: Request) -> ComponentHealth:
    registry = request.app.state.queue_registry
    if isinstance(registry, BullMQQueueRegistry):
        try:
            registry.snapshots()
        except Exception as exc:
            return ComponentHealth(status="down", message=f"Redis queue bridge failed: {exc}")
        return ComponentHealth(status="ok", message="BullMQ queue registry is reachable.")
    return ComponentHealth(status="degraded", message="Using in-memory queue registry.")
