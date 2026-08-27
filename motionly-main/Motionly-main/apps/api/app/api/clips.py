from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import (
    Actor,
    get_actor,
    get_caption_repository,
    get_visual_repository,
)
from app.api.errors import ApiError
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.visual import InMemoryVisualRepository
from app.schemas.captions import TranscriptStatus
from app.schemas.clips import ClipGoal, ClipRecommendationsResponse
from app.schemas.visual import VisualManifestStatus
from app.services.clip_finder import find_best_clips

router = APIRouter(prefix="/projects/{project_id}/clips", tags=["clip recommendations"])

ActorDep = Annotated[Actor, Depends(get_actor)]
CaptionRepositoryDep = Annotated[InMemoryCaptionRepository, Depends(get_caption_repository)]
VisualRepositoryDep = Annotated[InMemoryVisualRepository, Depends(get_visual_repository)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


@router.get("/recommendations", response_model=ClipRecommendationsResponse)
async def get_clip_recommendations(
    project_id: UUID,
    actor: ActorDep,
    caption_repository: CaptionRepositoryDep,
    visual_repository: VisualRepositoryDep,
    goal: ClipGoal = ClipGoal.VIRAL_HOOK,
    limit_per_duration: Annotated[int, Query(ge=1, le=5)] = 3,
) -> ClipRecommendationsResponse:
    await _repository_call(
        caption_repository,
        "get_project_for_workspace_async",
        "get_project_for_workspace",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    transcript = await _repository_call(
        caption_repository,
        "latest_transcript_async",
        "latest_transcript",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    if transcript is None:
        raise ApiError(
            404,
            "transcript_not_found",
            "Generate a transcript before finding the best moments.",
        )
    if transcript.status != TranscriptStatus.READY:
        raise ApiError(
            409,
            str(transcript.status),
            transcript.error_message or "Transcript is not ready for clip recommendations.",
        )
    if not transcript.words:
        raise ApiError(
            409,
            "transcript_empty",
            "A word-level transcript is required to find the best moments.",
        )

    visual_record = await _repository_call(
        visual_repository,
        "latest_ready_for_upload_async",
        "latest_ready_for_upload",
        uploaded_video_id=transcript.uploaded_video_id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    visual_manifest = (
        visual_record.manifest
        if visual_record
        and visual_record.status == VisualManifestStatus.READY
        and visual_record.manifest is not None
        else None
    )
    raw_payload = transcript.raw_payload if isinstance(transcript.raw_payload, dict) else {}
    audio_intelligence = raw_payload.get("audio_intelligence")
    groups, signal_availability, source_duration_ms = find_best_clips(
        words=transcript.words,
        source_duration_ms=transcript.duration_ms,
        goal=goal,
        audio_intelligence=(audio_intelligence if isinstance(audio_intelligence, dict) else None),
        visual_manifest=visual_manifest,
        limit_per_duration=limit_per_duration,
    )
    if not groups or not any(group.clips for group in groups):
        raise ApiError(
            409,
            "source_too_short",
            "The source needs at least 15 seconds of timed transcript to suggest clips.",
        )
    return ClipRecommendationsResponse(
        goal=goal,
        source_duration_ms=source_duration_ms,
        signal_availability=signal_availability,
        groups=groups,
    )
