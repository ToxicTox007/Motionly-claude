import os
from collections.abc import Awaitable, Callable
from time import monotonic, perf_counter, time
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.services.auth import verify_supabase_jwt, verify_supabase_token_with_auth_api

logger = get_logger(__name__)


async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = request.headers.get("x-request-id", f"req_{uuid4().hex}")
    request.state.request_id = request_id
    started_at = perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    latency_ms = round((perf_counter() - started_at) * 1000)
    logger.info(
        "api_request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "user_id": request.headers.get("x-motionly-user-id"),
            "workspace_id": request.headers.get("x-motionly-workspace-id"),
            "service": "motionly-api",
            "environment": request.app.state.settings.app_env,
        },
    )
    return response


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

        self.redis = Redis.from_url(redis_url, decode_responses=True)

    async def allow(
        self, *, key: str, limit: int, window_seconds: int = 60
    ) -> tuple[bool, int]:
        if limit <= 0:
            return True, limit
        window = int(time()) // window_seconds
        redis_key = f"motionly:rate-limit:{window}:{key}"
        async with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(redis_key)
            pipeline.expire(redis_key, window_seconds + 2)
            count, _ = await pipeline.execute()
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
    actor_key = _verified_identity(request) or f"ip:{client_host}"
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
    settings = request.app.state.settings
    authorization = request.headers.get("authorization")
    if (
        settings.supabase_jwt_secret
        and authorization
        and authorization.lower().startswith("bearer ")
    ):
        token = authorization.split(" ", 1)[1].strip()
        try:
            verified = verify_supabase_jwt(token=token, jwt_secret=settings.supabase_jwt_secret)
        except Exception:
            if not settings.supabase_url or not settings.supabase_service_role_key:
                return None
            try:
                verified = verify_supabase_token_with_auth_api(
                    token=token,
                    supabase_url=str(settings.supabase_url),
                    service_role_key=settings.supabase_service_role_key,
                )
            except Exception:
                return None
        return f"user:{verified.user_id}"
    if os.getenv("SKIP_API_AUTH") == "true" and settings.app_env != "production":
        return "user:local-development"
    return None
