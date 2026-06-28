from fastapi import Request

from app.api.errors import ApiError
from app.services.queue import BullMQQueueRegistry, QueueRegistry


def require_worker_queue(request: Request | None) -> None:
    """Ensure heavy jobs are not executed inline when workers are unavailable."""
    if request is None or not hasattr(request, "app"):
        return
    state = getattr(request.app, "state", None)
    if state is None:
        return
    settings = getattr(state, "settings", None)
    registry = getattr(state, "queue_registry", None)
    if settings is None or registry is None:
        return

    app_env = getattr(settings, "app_env", "test")
    redis_url = getattr(settings, "redis_url", None)

    if app_env == "production":
        if not redis_url:
            raise ApiError(
                503,
                "queue_unavailable",
                "Redis is required for worker queueing in production.",
            )
        if not isinstance(registry, BullMQQueueRegistry):
            raise ApiError(
                503,
                "queue_unavailable",
                "Production requires BullMQ workers; in-process queueing is disabled.",
            )
        return

    if redis_url and not isinstance(registry, BullMQQueueRegistry):
        raise ApiError(
            503,
            "queue_unavailable",
            "Redis is configured but the BullMQ queue bridge is unavailable.",
        )
