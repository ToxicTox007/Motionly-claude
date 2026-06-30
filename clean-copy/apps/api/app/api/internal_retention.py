from fastapi import APIRouter, Header, Request

from app.api.internal_jobs import require_worker_token
from app.services.retention import RetentionService

router = APIRouter(prefix="/internal/retention", tags=["internal retention"])


@router.post("/run")
async def run_retention(
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> dict[str, object]:
    await require_worker_token(request, x_motionly_worker_token)
    recovered_analytics = request.app.state.analytics_recovery_queue.drain(
        request.app.state.analytics_repository
    )
    counts = RetentionService(
        settings=request.app.state.settings,
        storage_signer=request.app.state.storage_signer,
    ).run_scheduled()
    return {
        "status": "completed",
        "deleted_counts": counts,
        "recovered_analytics_events": recovered_analytics,
    }
