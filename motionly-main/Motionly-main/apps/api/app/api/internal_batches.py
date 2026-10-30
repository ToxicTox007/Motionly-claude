from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Header, Request

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.internal_jobs import require_worker_token
from app.api.plans import create_plan
from app.schemas.plans import PlanCreateRequest
from app.schemas.scale import BatchCreateRequest, ScaleResourceStatus
from app.services.offload import run_blocking

router = APIRouter(prefix="/internal/batches", tags=["internal batches"])

# A PROCESSING batch whose record was updated within this window is owned by a
# live run (every finished item refreshes ``updated_at``). A retry arriving in
# that window -- the worker's HTTP call timed out while the API kept working --
# is answered with ``already_running`` instead of re-planning every project
# (audit WJ-10). An older PROCESSING record belongs to a run that died, and is
# resumed.
BATCH_RUN_LEASE = timedelta(minutes=10)
_FINISHED = {ScaleResourceStatus.COMPLETED, ScaleResourceStatus.PARTIAL}


@router.post("/{workspace_id}/{batch_id}/run")
async def run_batch(
    workspace_id: UUID,
    batch_id: UUID,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> dict[str, object]:
    await require_worker_token(request, x_motionly_worker_token)
    repository = request.app.state.scale_repository
    record = await run_blocking(repository.get, resource_id=batch_id, workspace_id=workspace_id)
    items = list(record.result.get("items") or [])
    if record.status == ScaleResourceStatus.CANCELED:
        return {"status": "canceled", "completed": 0, "total": 0}
    if record.status in _FINISHED:
        return {"status": record.status.value, **_progress(items)}
    if record.status == ScaleResourceStatus.PROCESSING and _recently_updated(record):
        return {"status": "already_running", **_progress(items)}
    try:
        # Claim the run with a compare-and-set on the revision, so two
        # concurrent deliveries cannot both start it.
        record = await run_blocking(
            repository.update_if_revision,
            record=record,
            expected_revision=record.revision,
            status=ScaleResourceStatus.PROCESSING,
            revision=record.revision + 1,
        )
    except ApiError as exc:
        if exc.status_code == 409:
            return {"status": "already_running", **_progress(items)}
        raise
    payload = BatchCreateRequest.model_validate(record.payload)
    actor = Actor(
        user_id=UUID(str(record.payload["creator_user_id"])),
        workspace_id=record.workspace_id,
    )
    items = list(record.result.get("items") or [])
    canceled = False
    for index, project_id in enumerate(payload.project_ids):
        if index < len(items) and items[index].get("status") == "completed":
            # Already planned by an earlier (interrupted) run: a resumed batch
            # must not pay for the same plan twice.
            continue
        current = await run_blocking(
            repository.get, resource_id=batch_id, workspace_id=record.workspace_id
        )
        if current.status == ScaleResourceStatus.CANCELED:
            record = current
            canceled = True
            break
        _set_item(items, index, {"project_id": str(project_id), "status": "processing"})
        record = await run_blocking(repository.update, record=record, result=_progress(items))
        try:
            result = await create_plan(
                project_id=project_id,
                payload=PlanCreateRequest(
                    prompt=payload.prompt,
                    style=payload.style,
                    target_duration_s=payload.target_duration_s,
                    broll_density=payload.broll_density,
                    force=True,
                    style_profile_id=payload.style_profile_id,
                    saved_template_id=payload.saved_template_id,
                ),
                actor=actor,
                repository=request.app.state.plan_repository,
                planner_provider=request.app.state.edit_planner_provider,
                plan_queue=request.app.state.plan_queue,
                request=request,
            )
            _set_item(
                items,
                index,
                {
                    "project_id": str(project_id),
                    "status": "completed",
                    "plan_id": str(result.plan.id),
                },
            )
        except Exception as exc:
            # Only a stable error code reaches the user-visible batch result;
            # raw exception text can carry internal detail.
            _set_item(
                items,
                index,
                {
                    "project_id": str(project_id),
                    "status": "failed",
                    "error_code": exc.code if isinstance(exc, ApiError) else "plan_failed",
                },
            )
        record = await run_blocking(
            repository.update,
            record=record,
            result=_progress(items),
            revision=record.revision + 1,
        )
    if canceled or record.status == ScaleResourceStatus.CANCELED:
        return {"status": "canceled", **_progress(items)}
    progress = _progress(items)
    final_status = (
        ScaleResourceStatus.COMPLETED
        if progress["completed"] == progress["total"]
        else ScaleResourceStatus.PARTIAL
    )
    await run_blocking(repository.update, record=record, status=final_status, result=progress)
    return {"status": final_status.value, **progress}


def _recently_updated(record, *, now: datetime | None = None) -> bool:
    updated_at = record.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - updated_at < BATCH_RUN_LEASE


def _set_item(items: list[dict[str, object]], index: int, value: dict[str, object]) -> None:
    while len(items) <= index:
        items.append({"status": "queued"})
    items[index] = value


def _progress(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "items": items,
        "completed": len([item for item in items if item.get("status") == "completed"]),
        "failed": len([item for item in items if item.get("status") == "failed"]),
        "total": len(items),
    }
