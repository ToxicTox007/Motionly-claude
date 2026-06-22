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
    VideoManifestResponse,
    VisualAnalyzeRequest,
    VisualAnalyzeResponse,
    VisualStatusResponse,
)
from app.services.queue import VisualQueue
from app.services.visual_jobs import queue_visual_analysis_for_upload, visual_provider_identity

router = APIRouter(prefix="/projects/{project_id}/visual", tags=["visual intelligence"])

ActorDep = Annotated[Actor, Depends(get_actor)]
VisualRepositoryDep = Annotated[InMemoryVisualRepository, Depends(get_visual_repository)]
VisualQueueDep = Annotated[VisualQueue, Depends(get_visual_queue)]


@router.get("/status", response_model=VisualStatusResponse)
async def get_visual_status(
    project_id: UUID,
    actor: ActorDep,
    repository: VisualRepositoryDep,
) -> VisualStatusResponse:
    upload = repository.get_active_upload(project_id=project_id, workspace_id=actor.workspace_id)
    record = repository.latest_for_upload(
        uploaded_video_id=upload.id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    if record is None or record.status.value == "failed":
        return VisualStatusResponse(
            status="unavailable",
            manifest=record.to_response() if record else None,
            message=(record.error_message if record else None)
            or "Visual context is unavailable; editing will continue using transcript and audio.",
        )
    if record.status.value == "ready":
        return VisualStatusResponse(
            status="ready",
            manifest=record.to_response(),
            message="Visual context is ready and will inform the AI edit.",
        )
    return VisualStatusResponse(
        status="processing",
        manifest=record.to_response(),
        message="Visual context is processing; editing can continue with transcript and audio.",
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
    scheduled = queue_visual_analysis_for_upload(
        upload=upload,
        repository=repository,
        visual_queue=visual_queue,
        provider=provider_name,
        model=model,
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
    record = repository.latest_manifest(project_id=project_id, workspace_id=actor.workspace_id)
    if not record:
        raise ApiError(
            404,
            "visual_manifest_not_found",
            "Run visual analysis before requesting the video manifest.",
        )
    return record.to_response()
