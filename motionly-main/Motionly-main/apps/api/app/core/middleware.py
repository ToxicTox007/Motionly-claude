import asyncio
import hashlib
import os
import re
from collections.abc import Awaitable, Callable
from time import monotonic, perf_counter, time
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.api.errors import ApiError
from app.core.config import LOCAL_AUTH_BYPASS_ENVS, env_flag_enabled
from app.core.logging import get_logger
from app.repositories.api_keys import KEY_SCHEME_PREFIX as API_KEY_SCHEME_PREFIX
from app.repositories.api_keys import key_fingerprint as _api_key_fingerprint
from app.services.auth import verify_supabase_jwt, verify_supabase_token_with_auth_api
from app.services.offload import run_blocking
from app.services.request_counters import RequestCounters, current_request_counters
from app.services.tracing import current_trace_id

logger = get_logger(__name__)


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TRACEPARENT_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def _safe_request_id(value: str | None) -> str:
    return value if value and _REQUEST_ID_RE.fullmatch(value) else f"req_{uuid4().hex}"


def _safe_trace_id(value: str | None) -> str:
    return value[3:35] if value and _TRACEPARENT_RE.fullmatch(value) else uuid4().hex


async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = _safe_request_id(request.headers.get("x-request-id"))
    incoming_trace = request.headers.get("traceparent")
    trace_id = _safe_trace_id(incoming_trace)
    span_id = uuid4().hex[:16]
    request.state.request_id = request_id
    request.state.trace_id = trace_id
    trace_token = current_trace_id.set(trace_id)
    counters = RequestCounters()
    counters_token = current_request_counters.set(counters)
    lag_started_at = perf_counter()
    await asyncio.sleep(0)
    event_loop_lag_ms = max(0.0, (perf_counter() - lag_started_at) * 1000)
    started_at = perf_counter()
    try:
        response = await call_next(request)
    finally:
        current_trace_id.reset(trace_token)
        current_request_counters.reset(counters_token)
    response.headers["x-request-id"] = request_id
    response.headers["traceparent"] = f"00-{trace_id}-{span_id}-01"
    latency_ms = round((perf_counter() - started_at) * 1000)
    metrics = getattr(request.app.state, "metrics_registry", None)
    if metrics:
        metrics.observe(
            "event_loop_lag_ms",
            event_loop_lag_ms,
            route=_route_label(request),
        )
        metrics.observe(
            "http_request_duration_ms",
            latency_ms,
            method=request.method,
            route=_route_label(request),
            status=str(response.status_code),
        )
    response_size = response.headers.get("content-length", "unknown")
    workspace_hash = _workspace_hash(request)
    if latency_ms >= 1000:
        logger.warning(
            "api_slow_request",
            extra={
                "request_id": request_id,
                "trace_id": trace_id,
                "route": _route_label(request),
                "method": request.method,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "workspace_hash": workspace_hash,
                "supabase_call_count": counters.supabase_calls,
                "queue_call_count": counters.queue_calls,
                "response_size": response_size,
            },
        )
    logger.info(
        "api_request_completed",
        extra={
            "request_id": request_id,
            "trace_id": trace_id,
            "span_id": span_id,
            "method": request.method,
            "path": request.url.path,
            "route": _route_label(request),
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "workspace_hash": workspace_hash,
            "supabase_call_count": counters.supabase_calls,
            "queue_call_count": counters.queue_calls,
            "response_size": response_size,
            "user_id": _verified_user_id(request),
            "workspace_id": request.headers.get("x-motionly-workspace-id"),
            "service": "motionly-api",
            "environment": request.app.state.settings.app_env,
        },
    )
    return response


def _verified_user_id(request: Request) -> str | None:
    """The user named by the request's verified token (rate_limit_middleware
    verifies it first); never a value the caller supplies."""
    verified = getattr(getattr(request, "state", None), "verified_supabase_token", None)
    return str(verified.user_id) if verified is not None else None


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return request.url.path


def _workspace_hash(request: Request) -> str | None:
    workspace_id = (
        getattr(request.state, "workspace_id", None)
        or request.headers.get("x-motionly-workspace-id")
    )
    if not workspace_id:
        return None
    return hashlib.sha256(str(workspace_id).encode()).hexdigest()[:12]


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    async def allow(self, *, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        if limit <= 0:
            return True, limit
        now = monotonic()
        window_start = now - window_seconds
        hits = [hit for hit in self._hits.get(key, []) if hit >= window_start]
        if len(hits) >= limit:
            self._hits[key] = hits
            return False, 0
        hits.append(now)
        self._hits[key] = hits
        return True, max(0, limit - len(hits))


class RedisRateLimiter:
    def __init__(self, redis_url: str) -> None:
        from redis.asyncio import Redis

        self.redis = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
            health_check_interval=30,
            retry_on_timeout=False,
        )
        self._fallback = InMemoryRateLimiter()

    async def allow(
        self, *, key: str, limit: int, window_seconds: int = 60
    ) -> tuple[bool, int]:
        if limit <= 0:
            return True, limit
        window = int(time()) // window_seconds
        redis_key = f"motionly:rate-limit:{window}:{key}"
        try:
            async with self.redis.pipeline(transaction=True) as pipeline:
                pipeline.incr(redis_key)
                pipeline.expire(redis_key, window_seconds + 2)
                count, _ = await pipeline.execute()
        except Exception as exc:  # noqa: BLE001 - availability fallback
            logger.error(
                "redis_rate_limiter_unavailable",
                extra={"error": type(exc).__name__},
            )
            return await self._fallback.allow(
                key=key,
                limit=limit,
                window_seconds=window_seconds,
            )
        count = int(count)
        return count <= limit, max(0, limit - count)


async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if request.url.path.startswith("/health"):
        return await call_next(request)

    settings = request.app.state.settings
    limiter: InMemoryRateLimiter | RedisRateLimiter = request.app.state.rate_limiter
    client_host = request.client.host if request.client else "anonymous"
    actor_key = await _resolve_rate_limit_identity(request) or f"ip:{client_host}"
    key = f"{actor_key}:{request.url.path}"
    allowed, remaining = await limiter.allow(key=key, limit=settings.rate_limit_per_minute)
    if not allowed:
        request_id = getattr(request.state, "request_id", f"req_{uuid4().hex}")
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "Too many requests. Try again shortly.",
                    "request_id": request_id,
                }
            },
            headers={"x-ratelimit-remaining": "0", "x-request-id": request_id},
        )

    response = await call_next(request)
    response.headers["x-ratelimit-remaining"] = str(remaining)
    return response


def _verified_identity(request: Request) -> str | None:
    """Rate-limit identity from the request's credentials, verified locally.

    Never touches the network; tokens that need the Supabase Auth API (an
    asymmetric signing algorithm) yield ``None`` here and are resolved by
    :func:`_resolve_rate_limit_identity`.
    """
    identity, _ = _local_identity(request)
    return identity


def _local_identity(request: Request) -> tuple[str | None, str | None]:
    """``(identity, None)``, or ``(None, token)`` when only Auth can verify it."""
    settings = request.app.state.settings
    authorization = request.headers.get("authorization")
    if (
        settings.supabase_jwt_secret
        and authorization
        and authorization.lower().startswith("bearer ")
    ):
        token = authorization.split(" ", 1)[1].strip()
        # Agency API keys (``mtly_...``) are rate-limited per key: the SHA-256
        # fingerprint is a stable, 1:1 stand-in for the key id, so the limiter
        # keys on it without a database lookup in the hot path (Task 24).
        if token.startswith(API_KEY_SCHEME_PREFIX):
            return f"apikey:{_api_key_fingerprint(token)}", None
        try:
            verified = verify_supabase_jwt(token=token, jwt_secret=settings.supabase_jwt_secret)
        except ApiError as exc:
            # Only a token signed with an algorithm we cannot verify locally is
            # worth asking Auth about (the same rule get_actor applies). Expired,
            # forged or malformed tokens are rejected here, so garbage bearer
            # tokens cannot make every request call out to Auth.
            if (
                exc.code == "auth_token_invalid"
                and "algorithm" in exc.message.lower()
                and settings.supabase_url
                and settings.supabase_service_role_key
            ):
                return None, token
            return None, None
        except Exception:
            return None, None
        _remember_verified_token(request, verified)
        return f"user:{verified.user_id}", None
    if env_flag_enabled(os.getenv("SKIP_API_AUTH")) and settings.app_env in LOCAL_AUTH_BYPASS_ENVS:
        return "user:local-development", None
    return None, None


async def _resolve_rate_limit_identity(request: Request) -> str | None:
    identity, remote_token = _local_identity(request)
    if remote_token is None:
        return identity
    settings = request.app.state.settings
    try:
        # Synchronous HTTP to Supabase Auth (10s timeout): off the event loop.
        verified = await run_blocking(
            verify_supabase_token_with_auth_api,
            token=remote_token,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
    except Exception:
        return None
    _remember_verified_token(request, verified)
    return f"user:{verified.user_id}"


def _remember_verified_token(request: Request, verified: object) -> None:
    request_state = getattr(request, "state", None)
    if request_state is not None:
        request_state.verified_supabase_token = verified


API_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "img-src 'self' data: https://fastapi.tiangolo.com",
        "media-src 'self' blob:",
        "script-src 'self' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "connect-src 'self'",
    )
)


async def security_headers_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    response = await call_next(request)
    response.headers["content-security-policy"] = API_CONTENT_SECURITY_POLICY
    response.headers["x-frame-options"] = "DENY"
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["referrer-policy"] = "no-referrer"
    response.headers["permissions-policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    return response
