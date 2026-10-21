"""Worker-token authentication dependency for the internal control plane.

The dead ``POST /internal/jobs/{queue_job_id}/run`` route was removed in
Task 32 (no production HTTP caller — the worker invokes the queue bridge, not a
literal URL). The in-process job engine it used to wrap now lives as a plain,
non-route module in :mod:`app.services.job_engine`.

What remains here is :func:`require_worker_token`, the shared dependency that
guards the internal routes still mounted (``/internal/batches/.../run``,
``/internal/retention/run``, ``/internal/workflows/...``, ``/internal/media/...``,
``/internal/operations/...``).
"""

from fastapi import Header, Request

from app.api.errors import ApiError
from app.core.security import constant_time_token_matches


async def require_worker_token(
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> None:
    settings = request.app.state.settings
    if not settings.worker_api_token:
        raise ApiError(500, "worker_token_not_configured", "Worker token is not configured.")
    if not constant_time_token_matches(x_motionly_worker_token, settings.worker_api_token):
        raise ApiError(403, "worker_access_denied", "Worker access is required.")
