from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from app.api.dependencies import Actor, get_actor, get_render_repository
from app.api.errors import ApiError
from app.repositories.renders import InMemoryRenderRepository
from app.schemas.admin import (
    AdminOverviewResponse,
    QueueActionResponse,
    QueueJobResponse,
    QueueListResponse,
    QueueSnapshotResponse,
    RenderAdminActionResponse,
    RenderJobAdminDetailResponse,
)
from app.schemas.renders import RenderJobStatus
from app.services.queue import EnqueuedJob, QueueRegistry

router = APIRouter(prefix="/admin", tags=["admin"])

ActorDep = Annotated[Actor, Depends(get_actor)]
RenderRepositoryDep = Annotated[InMemoryRenderRepository, Depends(get_render_repository)]


async def require_admin(
    request: Request,
    actor: ActorDep,
    x_motionly_admin_token: str | None = Header(default=None),
) -> Actor:
    settings = request.app.state.settings
    if settings.admin_api_token and x_motionly_admin_token == settings.admin_api_token:
        return actor
    if str(actor.workspace_id).lower() in settings.admin_workspace_id_list:
        return actor
    raise ApiError(403, "admin_access_denied", "Admin access is required.")


AdminActorDep = Annotated[Actor, Depends(require_admin)]


@router.get("/ops/overview", response_model=AdminOverviewResponse)
async def ops_overview(
    request: Request,
    _actor: AdminActorDep,
    render_repository: RenderRepositoryDep,
) -> AdminOverviewResponse:
    recent_renders = render_repository.list_recent_render_jobs(limit=20)
    render_failures = len([job for job in recent_renders if job.status == RenderJobStatus.FAILED])
    completed = len([job for job in recent_renders if job.status == RenderJobStatus.COMPLETE])
    settings = request.app.state.settings
    return AdminOverviewResponse(
        environment=settings.app_env,
        request_id=request.state.request_id,
        queues=_queue_snapshots(request.app.state.queue_registry),
        recent_renders=[
            job.to_response(render_repository.events_for_job(job.id)) for job in recent_renders
        ],
        launch_metrics={
            "recent_render_count": len(recent_renders),
            "recent_render_failures": render_failures,
            "recent_render_completions": completed,
            "render_success_rate": (
                round(completed / len(recent_renders), 4) if recent_renders else None
            ),
        },
        provider_costs={
            "status": "placeholder",
            "currency": "usd",
            "transcription": None,
            "edit_planning": None,
            "stock_video": None,
            "music": None,
            "rendering": None,
        },
        render_failure_diagnostics=[
            {
                "render_job_id": str(job.id),
                "project_id": str(job.project_id),
                "error_code": job.error_code,
                "error_message": job.error_message,
                "last_log": job.logs[-1] if job.logs else None,
                "updated_at": job.updated_at.isoformat(),
            }
            for job in recent_renders
            if job.status == RenderJobStatus.FAILED
        ][:10],
        health={
            "api": "ok",
            "worker": "ok" if getattr(settings, "admin_api_token", None) else "degraded",
            "renderer": (
                "ok"
                if getattr(settings, "render_execute_ffmpeg", True)
                or getattr(settings, "remotion_enabled", False)
                else "degraded"
            ),
            "redis": "configured" if getattr(settings, "redis_url", None) else "in_memory",
            "supabase": (
                "configured"
                if getattr(settings, "supabase_url", None)
                and getattr(settings, "supabase_service_role_key", None)
                else "not_configured"
            ),
        },
    )


@router.get("/queues", response_model=QueueListResponse)
async def list_queues(request: Request, _actor: AdminActorDep) -> QueueListResponse:
    registry: QueueRegistry = request.app.state.queue_registry
    return QueueListResponse(
        queues=_queue_snapshots(registry),
        jobs=[_queue_job_response(job) for job in registry.jobs.values()],
    )


@router.post("/queues/jobs/{queue_job_id}/retry", response_model=QueueActionResponse)
async def retry_queue_job(
    queue_job_id: str,
    request: Request,
    _actor: AdminActorDep,
) -> QueueActionResponse:
    registry: QueueRegistry = request.app.state.queue_registry
    if queue_job_id not in registry.jobs:
        raise ApiError(404, "queue_job_not_found", "Queue job was not found.")
    return QueueActionResponse(job=_queue_job_response(registry.retry(queue_job_id)))


@router.post("/queues/jobs/{queue_job_id}/cancel", response_model=QueueActionResponse)
async def cancel_queue_job(
    queue_job_id: str,
    request: Request,
    _actor: AdminActorDep,
) -> QueueActionResponse:
    registry: QueueRegistry = request.app.state.queue_registry
    if queue_job_id not in registry.jobs:
        raise ApiError(404, "queue_job_not_found", "Queue job was not found.")
    return QueueActionResponse(job=_queue_job_response(registry.cancel(queue_job_id)))


@router.get("/renders/{render_job_id}", response_model=RenderJobAdminDetailResponse)
async def get_render_job_detail(
    render_job_id: UUID,
    _actor: AdminActorDep,
    render_repository: RenderRepositoryDep,
) -> RenderJobAdminDetailResponse:
    job = render_repository.get_render_job_by_id(render_job_id=render_job_id)
    events = render_repository.events_for_job(job.id)
    return RenderJobAdminDetailResponse(
        job=job.to_response(events),
        diagnostics=_render_diagnostics(job, events),
        provider_costs={
            "currency": "usd",
            "estimated_total_usd": None,
            "status": "placeholder",
            "render_engine": job.render_engine.value if job.render_engine else None,
        },
    )


@router.post("/renders/{render_job_id}/retry", response_model=RenderAdminActionResponse)
async def retry_render_job(
    render_job_id: UUID,
    _actor: AdminActorDep,
    render_repository: RenderRepositoryDep,
) -> RenderAdminActionResponse:
    job = render_repository.retry_render_job(render_job_id=render_job_id)
    return RenderAdminActionResponse(
        job=job.to_response(render_repository.events_for_job(job.id)),
    )


@router.post("/renders/{render_job_id}/cancel", response_model=RenderAdminActionResponse)
async def cancel_render_job(
    render_job_id: UUID,
    _actor: AdminActorDep,
    render_repository: RenderRepositoryDep,
) -> RenderAdminActionResponse:
    job = render_repository.cancel_render_job_by_id(render_job_id=render_job_id)
    return RenderAdminActionResponse(
        job=job.to_response(render_repository.events_for_job(job.id)),
    )


def _queue_snapshots(registry: QueueRegistry) -> list[QueueSnapshotResponse]:
    return [
        QueueSnapshotResponse(
            queue_name=snapshot.queue_name,
            queued=snapshot.queued,
            running=snapshot.running,
            succeeded=snapshot.succeeded,
            failed=snapshot.failed,
            dead_letter=snapshot.dead_letter,
            canceled=snapshot.canceled,
            oldest_queued_seconds=snapshot.oldest_queued_seconds,
        )
        for snapshot in registry.snapshots()
    ]


def _queue_job_response(job: EnqueuedJob) -> QueueJobResponse:
    return QueueJobResponse(
        id=job.id,
        kind=job.kind,
        queue_name=job.queue_name,
        priority=job.priority,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        status=job.status,
        enqueued_at=job.enqueued_at,
        workspace_id=str(job.workspace_id) if job.workspace_id else None,
        project_id=str(job.project_id) if job.project_id else None,
        resource_id=str(job.resource_id) if job.resource_id else None,
    )


def _render_diagnostics(job, events) -> dict[str, object]:
    manifest = job.manifest if isinstance(job.manifest, dict) else {}
    return {
        "event_count": len(events),
        "last_event": events[-1].message if events else None,
        "render_engine": job.render_engine.value if job.render_engine else None,
        "renderer_version": job.renderer_version,
        "artifact_path": job.artifact_path,
        "benchmark_ms": job.benchmark_ms,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "source": manifest.get("source"),
        "warnings": manifest.get("warnings") or [],
        "logs": job.logs[-20:],
    }
