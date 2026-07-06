from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import (
    Actor,
    get_actor,
    get_visual_queue,
    get_visual_repository,
)
from app.api.errors import ApiError
from app.repositories.visual import InMemoryVisualRepository
from app.schemas.uploads import UploadStatus
from app.schemas.visual import (
    MattePreviewResponse,
    VideoManifestResponse,
    VisualAnalyzeRequest,
    VisualAnalyzeResponse,
    VisualStatusResponse,
)
from app.services.matte_jobs import matte_availability
from app.services.queue import VisualQueue
from app.services.visual_jobs import (
    build_visual_cache_key,
    queue_visual_analysis_for_upload,
    visual_provider_identity,
)

router = APIRouter(prefix="/projects/{project_id}/visual", tags=["visual intelligence"])

ActorDep = Annotated[Actor, Depends(get_actor)]
VisualRepositoryDep = Annotated[InMemoryVisualRepository, Depends(get_visual_repository)]
VisualQueueDep = Annotated[VisualQueue, Depends(get_visual_queue)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


@router.get("/status", response_model=VisualStatusResponse)
async def get_visual_status(
    project_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: VisualRepositoryDep,
) -> VisualStatusResponse:
    upload = repository.get_active_upload(project_id=project_id, workspace_id=actor.workspace_id)
    record = await _repository_call(
        repository,
        "latest_for_upload_async",
        "latest_for_upload",
        uploaded_video_id=upload.id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    matte_record = await _repository_call(
        repository,
        "latest_matte_for_upload_async",
        "latest_matte_for_upload",
        uploaded_video_id=upload.id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    matte_preview = _matte_preview_response(
        request=request,
        availability=matte_availability(matte_record),
    )
    if record is None or record.status.value == "failed":
        return VisualStatusResponse(
            status="unavailable",
            manifest=record.to_response() if record else None,
            message=(record.error_message if record else None)
            or "Visual context is unavailable; editing will continue using transcript and audio.",
            matte_preview=matte_preview,
        )
    if record.status.value == "ready":
        return VisualStatusResponse(
            status="ready",
            manifest=record.to_response(),
            message="Visual context is ready and will inform the AI edit.",
            matte_preview=matte_preview,
        )
    return VisualStatusResponse(
        status="processing",
        manifest=record.to_response(),
        message="Visual context is processing; editing can continue with transcript and audio.",
        matte_preview=matte_preview,
    )


@router.post("/analyze", response_model=VisualAnalyzeResponse, status_code=202)
async def analyze_visual_manifest(
    project_id: UUID,
    payload: VisualAnalyzeRequest,
    request: Request,
    actor: ActorDep,
    repository: VisualRepositoryDep,
    visual_queue: VisualQueueDep,
) -> VisualAnalyzeResponse:
    upload = repository.get_active_upload(project_id=project_id, workspace_id=actor.workspace_id)
    if upload.status != UploadStatus.READY:
        raise ApiError(
            409,
            "source_video_not_ready",
            "Finish upload preprocessing before running visual analysis.",
        )
    settings = request.app.state.settings
    provider_name, model = visual_provider_identity(settings)
    cache_key = build_visual_cache_key(
        upload=upload,
        provider=provider_name,
        model=model,
        sample_rate_fps=settings.visual_sample_fps,
        scene_threshold=settings.visual_scene_threshold,
    )
    scheduled = queue_visual_analysis_for_upload(
        upload=upload,
        repository=repository,
        visual_queue=visual_queue,
        provider=provider_name,
        model=model,
        cache_key=cache_key,
        force=payload.force,
    )
    return VisualAnalyzeResponse(
        manifest=scheduled.manifest.to_response(),
        job_id=(scheduled.job.id if scheduled.job else f"visual-cached--{scheduled.manifest.id}"),
        status=("ready" if scheduled.cached else "queued"),
        cached=scheduled.cached,
    )


@router.get("/manifest", response_model=VideoManifestResponse)
async def get_visual_manifest(
    project_id: UUID,
    actor: ActorDep,
    repository: VisualRepositoryDep,
) -> VideoManifestResponse:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=actor.workspace_id)
    record = await _repository_call(
        repository,
        "latest_manifest_async",
        "latest_manifest",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    if not record:
        raise ApiError(
            404,
            "visual_manifest_not_found",
            "Run visual analysis before requesting the video manifest.",
        )
    return record.to_response()


def _matte_preview_response(
    *,
    request: Request,
    availability: dict[str, object],
) -> MattePreviewResponse:
    status = str(availability.get("status") or "skipped")
    reason = availability.get("reason")
    matte = availability.get("matte")
    if status == "available" and hasattr(matte, "bucket") and hasattr(matte, "path"):
        storage_signer = getattr(request.app.state, "storage_signer", None)
        signed_url = (
            storage_signer.create_signed_download(
                bucket=matte.bucket,
                path=matte.path,
                expires_in_seconds=300,
            ).url
            if storage_signer is not None
            else None
        )
        return MattePreviewResponse(
            status="available",
            reason=None,
            preview_url=signed_url,
            bucket=matte.bucket,
            path=matte.path,
            cache_key=getattr(matte, "cache_key", None),
            model=matte.model,
            source="segmentation_matte",
        )
    if status == "pending":
        return MattePreviewResponse(
            status="approximate",
            reason=str(reason or "matte_pending"),
            source="approximate_preview",
        )
    return MattePreviewResponse(
        status=status if status in {"failed", "skipped"} else "skipped",
        reason=str(reason) if reason else None,
        source="none",
    )
