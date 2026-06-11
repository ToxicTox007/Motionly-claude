from collections.abc import Awaitable, Callable
from time import monotonic, perf_counter
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

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

    def allow(self, *, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
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


async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if request.url.path.startswith("/health"):
        return await call_next(request)

    settings = request.app.state.settings
    limiter: InMemoryRateLimiter = request.app.state.rate_limiter
    client_host = request.client.host if request.client else "anonymous"
    actor_key = (
        request.headers.get("x-motionly-workspace-id")
        or request.headers.get("x-motionly-user-id")
        or client_host
    )
    key = f"{actor_key}:{request.url.path}"
    allowed, remaining = limiter.allow(key=key, limit=settings.rate_limit_per_minute)
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
