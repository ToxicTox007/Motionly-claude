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
from app.services.ai_features import ai_feature_report, unconfigured_features

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
    # Registry-driven runtime identities (plan Task 6), exposed additively so the
    # authenticated diagnostics report can show which inference catalog is live.
    registry_version: str | None = None
    catalog_fingerprint: str | None = None


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
    runtime = getattr(request.app.state, "inference_runtime", None)
    return DependencyHealthResponse(
        status=aggregate,
        service="motionly-api",
        environment=request.app.state.settings.app_env,
        gpu_stage=request.app.state.settings.gpu_stage,
        request_id=request.state.request_id,
        dependencies=checks,
        registry_version=getattr(runtime, "registry_version", None),
        catalog_fingerprint=getattr(runtime, "catalog_fingerprint", None),
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
    runtime = getattr(request.app.state, "inference_runtime", None)
    names = ("redis", "supabase", "storage", "renderer", "workers", "temporal", "providers")
    results = await asyncio.gather(
        _probe_redis(settings),
        _probe_supabase_ready(settings),
        _probe_storage(settings),
        _probe_renderer(settings),
        _probe_workers(request),
        _probe_temporal(settings),
        _provider_dependency_health(settings, runtime),
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
    checks["ai_features"] = _ai_feature_health(settings)
    checks["inference"] = _inference_health(request)
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


def _ai_feature_health(settings) -> DependencyHealth:
    """Provider, execution site and credential presence per AI feature (no values)."""
    report = ai_feature_report(settings)
    missing = unconfigured_features(report)
    metadata: dict[str, object] = {
        "gpu_workers": (
            "enabled" if getattr(settings, "gpu_workers_enabled", True) else "disabled"
        ),
        "features": report,
    }
    if missing:
        return DependencyHealth(
            status="degraded",
            message="Selected remote AI providers have no credentials: " + ", ".join(missing),
            metadata=metadata,
        )
    return DependencyHealth(
        status="ok", message="Every selected AI provider is configured.", metadata=metadata
    )


def _inference_health(request: Request) -> DependencyHealth:
    """The registry-driven inference diagnostics entry (plan Task 8, Section 2.6).

    Admin-only (served from the authenticated ``/health/dependencies`` report),
    it adds per-capability availability, validation warnings (route id + code),
    dropped routes, the read-only circuit state per route id, and the count of
    operations stuck in ``submit_unknown``. An open circuit never makes this
    entry (or the API) unhealthy; it surfaces as a degraded capability only.

    No base URL, model id, secret or provider label is returned.
    """
    runtime = getattr(request.app.state, "inference_runtime", None)
    if runtime is None:
        return DependencyHealth(
            status="ok", message="Inference runtime is not configured."
        )

    from app.services.inference_capability_report import build_capability_report

    report = build_capability_report(runtime)
    capability_states = {
        capability.capability: {
            "status": capability.status,
            "reason": capability.reason,
            "route_count": capability.route_count,
            "required": capability.required,
        }
        for capability in report.capabilities
    }

    startup = runtime.startup_validation
    warnings = (
        startup.warning_fields() if hasattr(startup, "warning_fields") else []
    )
    dropped = (
        startup.dropped_fields() if hasattr(startup, "dropped_fields") else []
    )

    # Read-only circuit state per active route id.
    circuit_states: dict[str, str] = {}
    for route_id in runtime.route_records:
        route = next(
            (r for r in runtime.gateway.all_routes if r.route_id == route_id), None
        )
        if route is not None:
            circuit_states[route_id] = runtime.gateway.circuit_state(route)

    submit_unknown = _submit_unknown_count(request, runtime)

    metadata: dict[str, object] = {
        "registry_version": report.registry_version,
        "capabilities": capability_states,
        "validation_warnings": warnings,
        "dropped_routes": dropped,
        "circuit_state": circuit_states,
        "submit_unknown": submit_unknown,
    }

    # A missing *required* capability is a genuine degradation; an open circuit
    # or an unavailable optional capability is not.
    required_unavailable = sorted(
        capability.capability
        for capability in report.capabilities
        if capability.required and capability.status == "unavailable"
    )
    if required_unavailable:
        return DependencyHealth(
            status="degraded",
            message="Required inference capabilities are unavailable: "
            + ", ".join(required_unavailable),
            metadata=metadata,
        )
    return DependencyHealth(
        status="ok",
        message="Inference registry capabilities reported.",
        metadata=metadata,
    )


def _submit_unknown_count(request: Request, runtime) -> int:
    """Count operations stuck in ``submit_unknown`` (plan Section 2.5/2.6).

    Uses the inference operations repository: the runtime's own repository, or
    the app-state operations repository, whichever exposes ``count_by_state``. A
    repository without that method, or any error, yields 0 so the probe never
    crashes the report.
    """
    from app.services.inference_operations import OperationState

    repository = getattr(runtime, "operations_repository", None)
    if repository is None or not hasattr(repository, "count_by_state"):
        return 0
    try:
        return int(repository.count_by_state(OperationState.SUBMIT_UNKNOWN))
    except Exception:
        return 0


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


async def _probe_providers(settings, runtime=None, *, transport=None) -> DependencyHealth:
    """Probe each configured adapter's declared health request (plan Task 8).

    Vendor URLs are gone. For every registry adapter with a ``health``
    ``{method, path}`` spec, probe ``base_url + path`` with the adapter's auth
    applier, a short timeout and no body. An adapter without a health spec
    reports ``not_probed``. Async submit endpoints are never called. Stock-media
    providers (not inference adapters) keep their lightweight probes.

    No base URL, model id, secret or provider label is returned; the metadata is
    keyed by the neutral adapter id and carries only the HTTP status or a neutral
    state.
    """
    adapter_probes = _adapter_health_probes(settings, runtime)
    media_probes = _stock_media_probes(settings)
    if not adapter_probes and not media_probes:
        return DependencyHealth(status="ok", message="No remote providers configured.")

    statuses: dict[str, object] = {}
    unhealthy = False
    async with httpx.AsyncClient(
        timeout=settings.readiness_timeout_seconds, transport=transport
    ) as client:
        for probe in adapter_probes:
            if probe.method is None:
                statuses[probe.name] = "not_probed"
                continue
            try:
                result = await client.request(
                    probe.method, probe.url, headers=probe.headers
                )
            except Exception:
                statuses[probe.name] = "unreachable"
                unhealthy = True
                continue
            statuses[probe.name] = result.status_code
            if result.status_code >= 500 or result.status_code in {401, 403}:
                unhealthy = True
        for name, url, headers in media_probes:
            try:
                result = await client.get(url, headers=headers)
            except Exception:
                statuses[name] = "unreachable"
                unhealthy = True
                continue
            statuses[name] = result.status_code
            if result.status_code >= 500 or result.status_code in {401, 403}:
                unhealthy = True
    if unhealthy:
        return DependencyHealth(
            status="down", message="A provider probe failed.", metadata=statuses
        )
    return DependencyHealth(status="ok", message="Provider probes responded.", metadata=statuses)


@dataclass
class _AdapterProbe:
    name: str
    method: str | None
    url: str
    headers: dict[str, str]


def _adapter_health_probes(settings, runtime) -> list[_AdapterProbe]:
    """Build an adapter-declared health probe per registry adapter.

    Reads the runtime's adapter records (neutral ``{method, path}`` health spec,
    base URL, network, auth). The auth applier is rebuilt through the same
    secret resolver the runtime uses; a resolution or auth-build failure makes
    the adapter ``not_probed`` rather than leaking a value. ``auth: none`` and
    adapters without a health spec are handled too.
    """
    if runtime is None:
        return []
    adapter_records = getattr(runtime, "adapter_records", None)
    if not adapter_records:
        return []
    from app.services.inference_auth import build_auth
    from app.services.inference_secrets import SecretResolver

    resolver = None
    probes: list[_AdapterProbe] = []
    for adapter_id, record in adapter_records.items():
        if record.type in {"local", "fake"} or not record.base_url:
            # Local/fake adapters make no outbound call; nothing to probe.
            continue
        if not record.health_method or not record.health_path:
            probes.append(
                _AdapterProbe(name=adapter_id, method=None, url="", headers={})
            )
            continue
        headers: dict[str, str] = {"accept": "application/json"}
        headers.update(record.extra_headers)
        try:
            if resolver is None:
                resolver = SecretResolver(settings=settings)
            applier = build_auth(record.auth, resolver, provider=adapter_id)
            applier.apply(headers)
        except Exception:
            # A missing secret or bad auth config: report not_probed, never the
            # value or the reason text.
            probes.append(
                _AdapterProbe(name=adapter_id, method=None, url="", headers={})
            )
            continue
        url = f"{str(record.base_url).rstrip('/')}/{str(record.health_path).lstrip('/')}"
        probes.append(
            _AdapterProbe(
                name=adapter_id,
                method=str(record.health_method).upper(),
                url=url,
                headers=headers,
            )
        )
    return probes


def _stock_media_probes(settings) -> list[tuple[str, str, dict[str, str]]]:
    """Lightweight probes for the stock-media providers (not inference adapters).

    These are stock search/footage providers, outside the inference registry.
    They keep their existing cheap probes so the ``providers`` dependency entry
    still reflects their health.
    """
    probes: list[tuple[str, str, dict[str, str]]] = []
    if getattr(settings, "pexels_api_key", None):
        probes.append(
            (
                "stock_curated",
                "https://api.pexels.com/v1/curated?per_page=1",
                {"authorization": settings.pexels_api_key},
            )
        )
    if getattr(settings, "pixabay_api_key", None):
        probes.append(
            (
                "stock_footage",
                f"https://pixabay.com/api/videos/?key={settings.pixabay_api_key}&per_page=3",
                {},
            )
        )
    return probes


async def _probe_workers(request: Request) -> DependencyHealth:
    settings = request.app.state.settings
    if settings.app_env != "production" and not settings.redis_url:
        return DependencyHealth(status="ok", message="Workers are optional in local mode.")
    heartbeats = await asyncio.to_thread(request.app.state.operations_repository.latest_heartbeats)
    cutoff = datetime.now(UTC).timestamp() - settings.worker_heartbeat_max_age_seconds
    live = [item for item in heartbeats if item.last_seen_at.timestamp() >= cutoff]
    if not live:
        return DependencyHealth(status="down", message="No current worker heartbeat.")
    covered = sorted({queue for item in live for queue in (item.queues or [])})
    required = [
        queue.strip()
        for queue in str(getattr(settings, "required_worker_queues", "") or "").split(",")
        if queue.strip()
    ]
    missing = [queue for queue in required if queue not in covered]
    metadata = {"workers": len(live), "queues": covered}
    if missing:
        # One live worker used to make every queue look served (audit WJ-20).
        return DependencyHealth(
            status="degraded",
            message="Required queues have no current worker heartbeat.",
            metadata={**metadata, "missing_queues": missing},
        )
    return DependencyHealth(
        status="ok", message="Current worker heartbeat found.", metadata=metadata
    )


async def _provider_dependency_health(settings, runtime=None) -> DependencyHealth:
    try:
        result = await _probe_providers(settings, runtime)
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
