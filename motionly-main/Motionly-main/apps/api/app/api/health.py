from pathlib import Path

import httpx
from fastapi import APIRouter, Request
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


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="motionly-api",
        environment=request.app.state.settings.app_env,
        request_id=request.state.request_id,
    )


@router.get("/dependencies", response_model=DependencyHealthResponse)
async def dependencies(request: Request) -> DependencyHealthResponse:
    settings = request.app.state.settings
    checks = {
        "api": DependencyHealth(status="ok", message="API process is responding."),
        "worker": _worker_health(settings),
        "renderer": _renderer_health(settings),
        "redis": _redis_health(request),
        "supabase": await _supabase_health(settings),
    }
    aggregate = "ok" if all(item.status == "ok" for item in checks.values()) else "degraded"
    return DependencyHealthResponse(
        status=aggregate,
        service="motionly-api",
        environment=settings.app_env,
        request_id=request.state.request_id,
        dependencies=checks,
    )


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


def _redis_health(request: Request) -> DependencyHealth:
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
    if response.status_code >= 500:
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
            status="ok" if settings.admin_api_token else "degraded",
            message=(
                "Worker token is configured."
                if settings.admin_api_token
                else "Worker token is not configured."
            ),
        ),
        "renderer": ComponentHealth(
            status="ok" if settings.render_execute_ffmpeg or settings.remotion_enabled else "degraded",
            message=(
                "Render execution is enabled."
                if settings.render_execute_ffmpeg or settings.remotion_enabled
                else "Render execution is simulated."
            ),
        ),
        "redis": _redis_health(request),
        "supabase": ComponentHealth(
            status="ok" if settings.supabase_url and settings.supabase_service_role_key else "degraded",
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


@router.get("/dependencies")
async def dependencies(request: Request) -> dict[str, object]:
    response = await deep(request)
    return {
        "status": response.status,
        "service": response.service,
        "environment": response.environment,
        "request_id": response.request_id,
        "dependencies": response.components,
    }


def _redis_health(request: Request) -> ComponentHealth:
    registry = request.app.state.queue_registry
    if isinstance(registry, BullMQQueueRegistry):
        try:
            registry.snapshots()
        except Exception as exc:
            return ComponentHealth(status="down", message=f"Redis queue bridge failed: {exc}")
        return ComponentHealth(status="ok", message="BullMQ queue registry is reachable.")
    return ComponentHealth(status="degraded", message="Using in-memory queue registry.")
