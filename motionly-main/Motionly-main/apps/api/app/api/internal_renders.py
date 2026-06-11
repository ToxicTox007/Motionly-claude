"""
Internal endpoints called exclusively by the Remotion worker service.
All routes require the X-Motionly-Worker-Token header.

Routes
------
GET  /internal/renders/{render_job_id}            – returns the full job manifest
POST /internal/renders/{render_job_id}/complete   – marks job COMPLETE after worker uploads MP4
"""

from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.repositories.renders import InMemoryRenderRepository
from app.schemas.renders import RenderJobResponse, RenderJobStatus
from app.services.render_jobs import complete_remotion_render_job

router = APIRouter(prefix="/internal/renders", tags=["internal renders"])


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def _require_worker_token(
    request: Request,
    x_motionly_worker_token: str | None,
) -> None:
    settings = request.app.state.settings
    if not settings.admin_api_token:
        raise ApiError(500, "worker_token_not_configured", "Worker token is not configured.")
    if x_motionly_worker_token != settings.admin_api_token:
        raise ApiError(403, "worker_access_denied", "Worker access is required.")


# ---------------------------------------------------------------------------
# GET /internal/renders/{render_job_id}
# ---------------------------------------------------------------------------


@router.get("/{render_job_id}", response_model=RenderJobResponse)
async def get_render_job_for_worker(
    render_job_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> RenderJobResponse:
    """
    Returns the full render job record (including manifest) for a given job ID.
    Used by the Remotion worker to assemble MotionlyRenderProps before spawning
    the renderer CLI.

    Query parameters:
        workspace_id – must match the job's workspace
        project_id   – must match the job's project
    """
    await _require_worker_token(request, x_motionly_worker_token)
    repository: InMemoryRenderRepository = request.app.state.render_repository
    job = repository.get_render_job(
        project_id=project_id,
        render_job_id=render_job_id,
        workspace_id=workspace_id,
    )
    return job.to_response(repository.events_for_job(job.id))


# ---------------------------------------------------------------------------
# POST /internal/renders/{render_job_id}/complete
# ---------------------------------------------------------------------------


class RemotionCompleteRequest(BaseModel):
    """Payload sent by the worker after it has uploaded the rendered MP4."""

    workspace_id: UUID
    project_id: UUID
    export_storage_path: str = Field(
        ...,
        description="Path inside the export bucket, e.g. workspaces/{wid}/exports/{id}.mp4",
    )
    artifact_bucket: str = Field(default="exports")
    benchmark_ms: int = Field(ge=0)
    file_size_bytes: int = Field(ge=0)
    width: int = Field(default=1080, gt=0)
    height: int = Field(default=1920, gt=0)
    fps: int = Field(default=30, gt=0)
    duration_ms: int | None = Field(default=None, ge=0)


class RemotionCompleteResponse(BaseModel):
    render_job_id: UUID
    export_id: UUID
    status: RenderJobStatus
    benchmark_ms: int


class RenderProgressRequest(BaseModel):
    workspace_id: UUID
    project_id: UUID
    status: RenderJobStatus = RenderJobStatus.RENDERING
    progress_pct: int = Field(ge=0, le=100)
    message: str
    event_type: str = Field(default="worker_progress")
    metadata: dict[str, object] = Field(default_factory=dict)


class RenderFailureRequest(BaseModel):
    workspace_id: UUID
    project_id: UUID
    error_code: str = Field(default="render_worker_failed")
    error_message: str
    retrying: bool = False
    metadata: dict[str, object] = Field(default_factory=dict)


class RenderStateResponse(BaseModel):
    render_job_id: UUID
    status: RenderJobStatus
    progress_pct: int


@router.post("/{render_job_id}/progress", response_model=RenderStateResponse)
async def update_render_progress(
    render_job_id: UUID,
    payload: RenderProgressRequest,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> RenderStateResponse:
    await _require_worker_token(request, x_motionly_worker_token)
    repository: InMemoryRenderRepository = request.app.state.render_repository
    job = repository.get_render_job(
        project_id=payload.project_id,
        render_job_id=render_job_id,
        workspace_id=payload.workspace_id,
    )
    if job.status in {RenderJobStatus.COMPLETE, RenderJobStatus.CANCELED, RenderJobStatus.FAILED}:
        return RenderStateResponse(
            render_job_id=job.id,
            status=job.status,
            progress_pct=job.progress_pct,
        )
    updated = repository.transition_job(
        job=job,
        status=payload.status,
        progress_pct=max(job.progress_pct, payload.progress_pct),
        message=payload.message,
        event_type=payload.event_type,
        metadata=payload.metadata,
    )
    return RenderStateResponse(
        render_job_id=updated.id,
        status=updated.status,
        progress_pct=updated.progress_pct,
    )


@router.post("/{render_job_id}/failed", response_model=RenderStateResponse)
async def fail_render_job(
    render_job_id: UUID,
    payload: RenderFailureRequest,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> RenderStateResponse:
    await _require_worker_token(request, x_motionly_worker_token)
    repository: InMemoryRenderRepository = request.app.state.render_repository
    job = repository.get_render_job(
        project_id=payload.project_id,
        render_job_id=render_job_id,
        workspace_id=payload.workspace_id,
    )
    if job.status in {RenderJobStatus.COMPLETE, RenderJobStatus.CANCELED}:
        return RenderStateResponse(
            render_job_id=job.id,
            status=job.status,
            progress_pct=job.progress_pct,
        )
    next_status = RenderJobStatus.QUEUED if payload.retrying else RenderJobStatus.FAILED
    next_progress = 5 if payload.retrying else job.progress_pct
    job.logs = [
        *(job.logs or []),
        f"{'retryable_' if payload.retrying else ''}worker_failure:{payload.error_code}",
        payload.error_message[:500],
    ]
    updated = repository.transition_job(
        job=job,
        status=next_status,
        progress_pct=next_progress,
        message=(
            "Render worker failed; BullMQ will retry."
            if payload.retrying
            else payload.error_message
        ),
        event_type="worker_retrying" if payload.retrying else "worker_failed",
        error_code=None if payload.retrying else payload.error_code,
        error_message=None if payload.retrying else payload.error_message,
        metadata=payload.metadata,
    )
    return RenderStateResponse(
        render_job_id=updated.id,
        status=updated.status,
        progress_pct=updated.progress_pct,
    )


@router.post("/{render_job_id}/complete", response_model=RemotionCompleteResponse)
async def complete_remotion_job(
    render_job_id: UUID,
    payload: RemotionCompleteRequest,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> RemotionCompleteResponse:
    """
    Called by the Remotion worker after it has:
    1. Rendered the composition to an MP4 file
    2. Uploaded that MP4 to Supabase Storage at payload.export_storage_path

    This endpoint transitions the render job to COMPLETE and creates the export
    record, making the MP4 available for download.
    """
    await _require_worker_token(request, x_motionly_worker_token)
    repository: InMemoryRenderRepository = request.app.state.render_repository
    job = repository.get_render_job(
        project_id=payload.project_id,
        render_job_id=render_job_id,
        workspace_id=payload.workspace_id,
    )
    if job.status == RenderJobStatus.COMPLETE:
        # Idempotent: return success if already completed (worker retry safety)
        exports = repository.list_exports(
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
        )
        latest = next(
            (e for e in reversed(exports) if e.render_job_id == render_job_id),
            None,
        )
        if latest:
            return RemotionCompleteResponse(
                render_job_id=render_job_id,
                export_id=latest.id,
                status=job.status,
                benchmark_ms=job.benchmark_ms or payload.benchmark_ms,
            )

    job, export = complete_remotion_render_job(
        job=job,
        repository=repository,
        export_bucket=payload.artifact_bucket,
        export_storage_path=payload.export_storage_path,
        benchmark_ms=payload.benchmark_ms,
        file_size_bytes=payload.file_size_bytes,
        width=payload.width,
        height=payload.height,
        fps=payload.fps,
        duration_ms=payload.duration_ms,
    )
    return RemotionCompleteResponse(
        render_job_id=render_job_id,
        export_id=export.id,
        status=job.status,
        benchmark_ms=payload.benchmark_ms,
    )
