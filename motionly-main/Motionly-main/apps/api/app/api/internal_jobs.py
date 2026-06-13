from typing import Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.schemas.renders import RenderJobStatus
from app.services.queue import BullMQQueueRegistry, EnqueuedJob, QueueJobStatus, QueueRegistry
from app.services.render_jobs import complete_render_job_by_id
from app.services.transcription_jobs import (
    queue_transcription_for_upload,
    run_transcription_for_upload,
)
from app.services.upload_jobs import complete_upload_preprocessing_by_id

router = APIRouter(prefix="/internal/jobs", tags=["internal jobs"])


class WorkerJobRunResponse(BaseModel):
    queue_job_id: str
    kind: str
    status: QueueJobStatus
    result: dict[str, Any] = Field(default_factory=dict)


async def require_worker_token(
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> None:
    settings = request.app.state.settings
    if not settings.admin_api_token:
        raise ApiError(500, "worker_token_not_configured", "Worker token is not configured.")
    if x_motionly_worker_token != settings.admin_api_token:
        raise ApiError(403, "worker_access_denied", "Worker access is required.")


@router.post("/{queue_job_id}/run", response_model=WorkerJobRunResponse)
async def run_queue_job(
    queue_job_id: str,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> WorkerJobRunResponse:
    await require_worker_token(request, x_motionly_worker_token)
    registry: QueueRegistry = request.app.state.queue_registry
    try:
        job = registry.jobs[queue_job_id]
    except KeyError as exc:
        raise ApiError(404, "queue_job_not_found", "Queue job was not found.") from exc

    if job.status == QueueJobStatus.SUCCEEDED:
        return _response(job, result={"message": "Job already succeeded."})
    if job.status in {QueueJobStatus.CANCELED, QueueJobStatus.DEAD_LETTER}:
        raise ApiError(409, "queue_job_not_runnable", "Queue job can no longer be run.")
    if job.status == QueueJobStatus.RUNNING and not isinstance(registry, BullMQQueueRegistry):
        raise ApiError(409, "queue_job_already_running", "Queue job is already running.")

    if job.status != QueueJobStatus.RUNNING:
        job = registry.start(queue_job_id)
    try:
        result = _execute_job(job=job, request=request)
    except Exception:
        registry.fail(queue_job_id)
        raise

    if result.get("failed"):
        failed = registry.fail(queue_job_id)
        return _response(failed, result=result)

    completed = registry.complete(queue_job_id)
    return _response(completed, result=result)


def _execute_job(*, job: EnqueuedJob, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not job.workspace_id or not job.project_id or not job.resource_id:
        raise ApiError(422, "queue_job_payload_invalid", "Queue job is missing resource context.")

    if job.kind == "video_preprocess":
        upload = complete_upload_preprocessing_by_id(
            repository=request.app.state.upload_repository,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            video_id=job.resource_id,
            media_work_dir=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
        )
        result = {
            "upload_id": str(upload.id),
            "upload_status": upload.status.value,
        }
        if all(
            hasattr(request.app.state, name)
            for name in (
                "caption_repository",
                "transcription_queue",
                "transcription_provider",
            )
        ):
            transcript = queue_transcription_for_upload(
                upload=upload,
                caption_repository=request.app.state.caption_repository,
                transcription_queue=request.app.state.transcription_queue,
                transcription_provider=request.app.state.transcription_provider,
            )
            result.update(
                {
                    "transcript_id": str(transcript.id),
                    "transcript_status": transcript.status.value,
                }
            )
        return result

    if job.kind == "transcription":
        transcript = run_transcription_for_upload(
            upload_repository=request.app.state.upload_repository,
            caption_repository=request.app.state.caption_repository,
            transcription_provider=request.app.state.transcription_provider,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            video_id=job.resource_id,
            media_work_dir=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
            retain_debug_audio=settings.transcription_debug_retain_audio,
        )
        return {
            "transcript_id": str(transcript.id),
            "transcript_status": transcript.status.value,
            "failed": transcript.status == "failed",
        }

    if job.kind == "render":
        render = complete_render_job_by_id(
            repository=request.app.state.render_repository,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            render_job_id=job.resource_id,
            export_bucket=settings.export_storage_bucket,
            storage_root=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=settings.supabase_service_role_key,
            execute_ffmpeg=settings.render_execute_ffmpeg,
            allow_fixture_media=(
                getattr(settings, "app_env", "test") != "production"
                or getattr(settings, "allow_fixture_providers", False)
            ),
        )
        return {
            "render_job_id": str(render.id),
            "render_status": render.status.value,
            "failed": render.status == RenderJobStatus.FAILED,
        }

    return {
        "message": "Queue item was already completed synchronously by the API.",
        "skipped": True,
    }


def _response(job: EnqueuedJob, *, result: dict[str, Any]) -> WorkerJobRunResponse:
    return WorkerJobRunResponse(
        queue_job_id=job.id,
        kind=job.kind,
        status=job.status,
        result=result,
    )
