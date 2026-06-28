from fastapi import Request

from app.api.errors import ApiError
from app.services.queue import BullMQQueueRegistry, QueueRegistry


def require_worker_queue(request: Request) -> None:
    """Ensure heavy jobs are not executed inline when workers are unavailable."""
    settings = request.app.state.settings
    registry = request.app.state.queue_registry

    if settings.app_env == "production":
        if not settings.redis_url:
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

    if settings.redis_url and not isinstance(registry, BullMQQueueRegistry):
        raise ApiError(
            503,
            "queue_unavailable",
            "Redis is configured but the BullMQ queue bridge is unavailable.",
        )
