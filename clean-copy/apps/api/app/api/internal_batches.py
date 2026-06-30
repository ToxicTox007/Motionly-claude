from uuid import UUID

from fastapi import APIRouter, Header, Request

from app.api.dependencies import Actor
from app.api.internal_jobs import require_worker_token
from app.api.plans import create_plan
from app.schemas.plans import PlanCreateRequest
from app.schemas.scale import BatchCreateRequest, ScaleResourceStatus

router = APIRouter(prefix="/internal/batches", tags=["internal batches"])


@router.post("/{workspace_id}/{batch_id}/run")
async def run_batch(
    workspace_id: UUID,
    batch_id: UUID,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> dict[str, object]:
    await require_worker_token(request, x_motionly_worker_token)
    repository = request.app.state.scale_repository
    record = repository.get(resource_id=batch_id, workspace_id=workspace_id)
    if record.status == ScaleResourceStatus.CANCELED:
        return {"status": "canceled", "completed": 0, "total": 0}
    payload = BatchCreateRequest.model_validate(record.payload)
    actor = Actor(
        user_id=UUID(str(record.payload["creator_user_id"])),
        workspace_id=record.workspace_id,
    )
    record = repository.update(record=record, status=ScaleResourceStatus.PROCESSING)
    items = list(record.result.get("items") or [])
    canceled = False
    for index, project_id in enumerate(payload.project_ids):
        current = repository.get(resource_id=batch_id, workspace_id=record.workspace_id)
        if current.status == ScaleResourceStatus.CANCELED:
            record = current
            canceled = True
            break
        items[index] = {"project_id": str(project_id), "status": "processing"}
        record = repository.update(record=record, result=_progress(items))
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
            items[index] = {
                "project_id": str(project_id),
                "status": "completed",
                "plan_id": str(result.plan.id),
            }
        except Exception as exc:
            items[index] = {
                "project_id": str(project_id),
                "status": "failed",
                "error": str(exc)[:300],
            }
        record = repository.update(
            record=record, result=_progress(items), revision=record.revision + 1
        )
    if canceled or record.status == ScaleResourceStatus.CANCELED:
        return {"status": "canceled", **_progress(items)}
    progress = _progress(items)
    final_status = (
        ScaleResourceStatus.COMPLETED
        if progress["completed"] == progress["total"]
        else ScaleResourceStatus.PARTIAL
    )
    repository.update(record=record, status=final_status, result=progress)
    return {"status": final_status.value, **progress}


def _progress(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "items": items,
        "completed": len([item for item in items if item.get("status") == "completed"]),
        "failed": len([item for item in items if item.get("status") == "failed"]),
        "total": len(items),
    }
