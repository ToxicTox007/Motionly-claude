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
