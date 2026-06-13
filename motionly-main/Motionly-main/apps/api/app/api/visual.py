from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import (
    Actor,
    get_actor,
    get_visual_intelligence_provider,
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
)
from app.services.queue import VisualQueue
from app.services.visual_intelligence import (
    VisualIntelligenceProvider,
    run_visual_analysis_for_upload,
)

router = APIRouter(prefix="/projects/{project_id}/visual", tags=["visual intelligence"])

ActorDep = Annotated[Actor, Depends(get_actor)]
VisualRepositoryDep = Annotated[InMemoryVisualRepository, Depends(get_visual_repository)]
VisualQueueDep = Annotated[VisualQueue, Depends(get_visual_queue)]
VisualProviderDep = Annotated[VisualIntelligenceProvider, Depends(get_visual_intelligence_provider)]


@router.post("/analyze", response_model=VisualAnalyzeResponse, status_code=202)
async def analyze_visual_manifest(
    project_id: UUID,
    payload: VisualAnalyzeRequest,
    request: Request,
    actor: ActorDep,
    repository: VisualRepositoryDep,
    visual_queue: VisualQueueDep,
    provider: VisualProviderDep,
) -> VisualAnalyzeResponse:
    upload = repository.get_active_upload(project_id=project_id, workspace_id=actor.workspace_id)
    if upload.status != UploadStatus.READY:
        raise ApiError(
            409,
            "source_video_not_ready",
            "Finish upload preprocessing before running visual analysis.",
        )
    cached = repository.latest_ready_for_upload(
        uploaded_video_id=upload.id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    job = visual_queue.enqueue_visual_analysis(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        video_id=upload.id,
    )
    if cached and not payload.force:
        return VisualAnalyzeResponse(manifest=cached.to_response(), job_id=job.id, cached=True)

    settings = request.app.state.settings
    record = run_visual_analysis_for_upload(
        repository=repository,
        provider=provider,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        video_id=upload.id,
        media_work_dir=settings.media_work_dir,
        supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
        service_role_key=settings.supabase_service_role_key,
        sample_rate_fps=payload.sample_rate_fps or settings.visual_sample_fps,
        scene_threshold=payload.scene_threshold or settings.visual_scene_threshold,
    )
    return VisualAnalyzeResponse(manifest=record.to_response(), job_id=job.id)


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
