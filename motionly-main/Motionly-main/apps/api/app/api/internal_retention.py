from fastapi import APIRouter, Header, Request

from app.api.internal_jobs import require_worker_token
from app.services.offload import run_blocking
from app.services.retention import RetentionAlreadyRunning, RetentionService

router = APIRouter(prefix="/internal/retention", tags=["internal retention"])


def _run_retention(state) -> dict[str, object]:
    recovered_analytics = state.analytics_recovery_queue.drain(state.analytics_repository)
    counts = RetentionService(
        settings=state.settings,
        storage_signer=state.storage_signer,
    ).run_scheduled()
    return {
        "status": "completed",
        "deleted_counts": counts,
        "recovered_analytics_events": recovered_analytics,
    }


@router.post("/run")
async def run_retention(
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> dict[str, object]:
    await require_worker_token(request, x_motionly_worker_token)
    try:
        # Retention is synchronous, long-running I/O: run it off the event loop
        # so this API process keeps serving requests meanwhile (audit WJ-11).
        return await run_blocking(_run_retention, request.app.state)
    except RetentionAlreadyRunning:
        # A retry that arrives while the first run is still working (its HTTP
        # call timed out on the worker side) is acknowledged, not repeated.
        return {"status": "already_running", "deleted_counts": {}, "recovered_analytics_events": 0}
