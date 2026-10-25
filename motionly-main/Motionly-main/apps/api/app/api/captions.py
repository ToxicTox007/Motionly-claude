import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.dependencies import (
    Actor,
    get_actor,
    get_caption_repository,
    get_transcription_provider,
    get_transcription_queue,
)
from app.api.errors import ApiError
from app.repositories.captions import InMemoryCaptionRepository
from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer
from app.schemas.captions import (
    CaptionGenerateRequest,
    CaptionGenerateResponse,
    CaptionListResponse,
    CaptionSegmentsUpdateRequest,
    CaptionSegmentsUpdateResponse,
    CaptionUpdateRequest,
    TranscriptionCreateRequest,
    TranscriptionCreateResponse,
    TranscriptReadResponse,
    TranscriptStatus,
    TranscriptWord,
)
from app.schemas.uploads import UploadStatus
from app.services.ai_feedback import record_ai_feedback
from app.services.caption_chunking import chunk_caption_words
from app.services.caption_presets import (
    get_caption_preset,
    list_caption_presets,
)
from app.services.layer_job_enqueue import (
    build_queued_layer_response_fields,
    prepare_heavy_job_enqueue,
    serialize_layer_job_payload,
)
from app.services.model_gateway import (
    ChatMessage,
    ModelCallContext,
    ModelGateway,
    PlanningOutput,
    PlanningRequest,
)
from app.services.offload import run_blocking
from app.services.queue import TranscriptionQueue
from app.services.transcript_quality import is_transcript_usable_for_planning
from app.services.transcription_jobs import queue_transcription_for_upload
from app.services.transcription_provider import TranscriptionProvider

router = APIRouter(prefix="/projects/{project_id}", tags=["transcripts and captions"])

ActorDep = Annotated[Actor, Depends(get_actor)]
CaptionRepositoryDep = Annotated[InMemoryCaptionRepository, Depends(get_caption_repository)]
TranscriptionProviderDep = Annotated[TranscriptionProvider, Depends(get_transcription_provider)]
TranscriptionQueueDep = Annotated[TranscriptionQueue, Depends(get_transcription_queue)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


@router.post("/transcriptions", response_model=TranscriptionCreateResponse, status_code=202)
async def create_transcription(
    project_id: UUID,
    payload: TranscriptionCreateRequest,
    request: Request,
    actor: ActorDep,
    repository: CaptionRepositoryDep,
    transcription_provider: TranscriptionProviderDep,
    transcription_queue: TranscriptionQueueDep,
) -> TranscriptionCreateResponse:
    upload = (
        repository.get_upload(
            project_id=project_id,
            video_id=payload.video_id,
            workspace_id=actor.workspace_id,
        )
        if payload.video_id
        else repository.get_active_upload(project_id=project_id, workspace_id=actor.workspace_id)
    )
    if upload.status != UploadStatus.READY:
        raise ApiError(
            409,
            "source_video_not_ready",
            "Finish upload preprocessing before generating a transcript.",
        )
    transcript = queue_transcription_for_upload(
        upload=upload,
        caption_repository=repository,
        transcription_queue=transcription_queue,
        transcription_provider=transcription_provider,
        language_hint=payload.language_hint,
        force=payload.force,
    )
    return TranscriptionCreateResponse(
        transcript_id=transcript.id,
        status=transcript.status,
        job_id=f"transcribe--{project_id}--{upload.id}--{actor.workspace_id}",
        message="Transcript queued for background generation.",
    )


@router.get("/transcript", response_model=TranscriptReadResponse)
async def get_transcript(
    project_id: UUID,
    actor: ActorDep,
    repository: CaptionRepositoryDep,
) -> TranscriptReadResponse:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=actor.workspace_id)
    transcript = await _repository_call(
        repository,
        "latest_transcript_async",
        "latest_transcript",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    if not transcript:
        raise ApiError(
            404,
            "transcript_not_found",
            "Generate a transcript before opening captions.",
        )
    return transcript.to_response()


@router.post("/captions/generate", response_model=CaptionGenerateResponse, status_code=202)
async def generate_captions(
    project_id: UUID,
    payload: CaptionGenerateRequest,
    actor: ActorDep,
    repository: CaptionRepositoryDep,
    transcription_queue: TranscriptionQueueDep,
    request: Request,
    response: Response,
) -> CaptionGenerateResponse:
    transcript = (
        await _repository_call(
            repository,
            "get_transcript_async",
            "get_transcript",
            transcript_id=payload.transcript_id,
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
        if payload.transcript_id
        else await _repository_call(
            repository,
            "latest_transcript_async",
            "latest_transcript",
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
    )
    if not transcript:
        raise ApiError(404, "transcript_not_found", "Generate a transcript before captions.")
    if transcript.status != TranscriptStatus.READY:
        raise ApiError(
            409,
            str(transcript.status),
            transcript.error_message or "Transcript is not ready.",
        )
    if not transcript.words or not is_transcript_usable_for_planning(transcript.quality_status):
        reason = (
            transcript.quality_reasons[0]
            if transcript.quality_reasons
            else "transcript_quality_blocked"
        )
        raise ApiError(
            409,
            reason,
            transcript.error_message or "Transcript quality must be recovered before captions.",
        )

    prepare_heavy_job_enqueue(request=request)
    job = await run_blocking(
        transcription_queue.enqueue_caption_generation,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        transcript_id=transcript.id,
        job_payload=serialize_layer_job_payload(payload=payload, user_id=actor.user_id),
    )
    return CaptionGenerateResponse(
        **build_queued_layer_response_fields(
            project_id=project_id,
            job_id=job.id,
            response=response,
        )
    )


@router.get("/captions", response_model=CaptionListResponse)
async def list_captions(
    project_id: UUID,
    actor: ActorDep,
    repository: CaptionRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> CaptionListResponse:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=actor.workspace_id)
    tracks = await _repository_call(
        repository,
        "list_caption_tracks_async",
        "list_caption_tracks",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    return CaptionListResponse(
        presets=list_caption_presets(),
        tracks=[track.to_response() for track in tracks],
    )


@router.patch("/captions/{track_id}", response_model=CaptionGenerateResponse)
async def update_caption_track(
    project_id: UUID,
    track_id: UUID,
    payload: CaptionUpdateRequest,
    actor: ActorDep,
    repository: CaptionRepositoryDep,
    transcription_queue: TranscriptionQueueDep,
    request: Request = None,  # type: ignore[assignment]
) -> CaptionGenerateResponse:
    track = await _repository_call(
        repository,
        "get_caption_track_async",
        "get_caption_track",
        track_id=track_id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    transcript = await _repository_call(
        repository,
        "get_transcript_async",
        "get_transcript",
        transcript_id=track.transcript_id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    preset_key = payload.preset or track.preset
    hide_filler_words = (
        payload.hide_filler_words
        if payload.hide_filler_words is not None
        else track.hide_filler_words
    )
    preset = get_caption_preset(preset_key)
    style = payload.style or preset.style
    should_rechunk = (
        hide_filler_words != track.hide_filler_words
        or preset_key != track.preset
        or style.mode != track.style.mode
    )
    segments = (
        chunk_caption_words(
            transcript.words,
            hide_filler_words=hide_filler_words,
            style=style,
        )
        if should_rechunk
        else track.segments
    )
    requested_language = (payload.output_language or "original").strip().lower()
    segments = translate_segments_if_requested(
        segments=segments,
        source_language=transcript.language,
        output_language=requested_language,
        model_gateway=(getattr(request.app.state, "model_gateway", None) if request else None),
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    if requested_language not in {
        "",
        "original",
        "auto",
        getattr(track, "language", transcript.language),
    }:
        translated = repository.create_caption_track(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            transcript_id=track.transcript_id,
            preset=preset_key,
            schema_version=track.schema_version,
            style=style,
            hide_filler_words=hide_filler_words,
            language=requested_language,
            source_track_id=track.id,
            review_status="needs_review",
            segments=segments,
        )
        return CaptionGenerateResponse(
            track=translated.to_response(),
            job_id=f"sync-complete--captions--{project_id}--{translated.id}",
            status="ready",
        )
    updated = await _repository_call(
        repository,
        "update_caption_track_async",
        "update_caption_track",
        track=track,
        preset=preset_key,
        style=style,
        hide_filler_words=hide_filler_words,
        segments=segments,
        language=getattr(track, "language", transcript.language),
        source_track_id=getattr(track, "source_track_id", None),
        review_status=getattr(track, "review_status", "approved"),
    )
    record_ai_feedback(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=None,
        event_type=AIEditFeedbackEventType.CHANGED,
        layer=AIEditLayer.CAPTIONS,
        source="caption_editor",
        decision_id=f"{track_id}:style",
        action="update_caption_style",
        ai_payload={
            "preset": track.preset.value,
            "style": track.style.model_dump(mode="json"),
            "hide_filler_words": track.hide_filler_words,
        },
        user_payload={
            "preset": preset_key.value,
            "style": style.model_dump(mode="json"),
            "hide_filler_words": hide_filler_words,
        },
    )
    return CaptionGenerateResponse(
        track=updated.to_response(),
        job_id=f"sync-complete--captions--{project_id}--{updated.id}",
        status="ready",
    )


@router.patch("/captions/{track_id}/segments", response_model=CaptionSegmentsUpdateResponse)
async def update_caption_segments(
    project_id: UUID,
    track_id: UUID,
    payload: CaptionSegmentsUpdateRequest,
    actor: ActorDep,
    repository: CaptionRepositoryDep,
    request: Request = None,  # type: ignore[assignment]
) -> CaptionSegmentsUpdateResponse:
    track = await _repository_call(
        repository,
        "get_caption_track_async",
        "get_caption_track",
        track_id=track_id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    updated = await _repository_call(
        repository,
        "replace_caption_segments_async",
        "replace_caption_segments",
        track=track,
        segments=payload.segments,
    )
    record_ai_feedback(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=None,
        event_type=AIEditFeedbackEventType.CHANGED,
        layer=AIEditLayer.CAPTIONS,
        source="caption_editor",
        decision_id=f"{track_id}:segments",
        action="update_caption_segments",
        ai_payload={
            "segments": [segment.model_dump(mode="json") for segment in track.segments],
        },
        user_payload={
            "segments": [segment.model_dump(mode="json") for segment in payload.segments],
        },
    )
    return CaptionSegmentsUpdateResponse(track=updated.to_response())


LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "ar": "Arabic",
}


def translate_segments_if_requested(
    *,
    segments,
    source_language: str,
    output_language: str,
    model_gateway: ModelGateway | None = None,
    workspace_id: object | None = None,
    project_id: object | None = None,
):
    target = (output_language or "original").strip().lower()
    if target in {"original", "auto", source_language, ""}:
        return segments
    if target not in LANGUAGE_NAMES:
        raise ApiError(422, "caption_language_unsupported", "Choose a supported caption language.")
    if model_gateway is None:
        raise ApiError(
            503,
            "caption_translation_unavailable",
            "Caption translation is not configured for production yet.",
        )
    try:
        result = model_gateway.invoke(
            PlanningRequest(
                messages=(
                    ChatMessage(
                        role="system",
                        content=(
                            "Translate short video caption segments. Return only JSON with key "
                            "'segments', an array of translated strings in the same order. Keep "
                            "meaning, creator tone, punctuation, and short caption length."
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "source_language": source_language,
                                "target_language": LANGUAGE_NAMES[target],
                                "segments": [segment.text for segment in segments],
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ),
                response_format={"type": "json_object"},
                temperature=0.1,
                max_output_units=2048,
            ),
            context=ModelCallContext(
                workspace_id=workspace_id,
                project_id=project_id,
            ),
        )
        if not isinstance(result.output, PlanningOutput):
            raise TypeError("caption translation route returned the wrong typed output")
        translated = json.loads(result.output.content).get("segments", [])
    except Exception as exc:
        raise ApiError(
            502,
            "caption_translation_failed",
            "Caption translation failed. Please try again.",
        ) from exc
    if not isinstance(translated, list) or len(translated) != len(segments):
        raise ApiError(
            502,
            "caption_translation_invalid",
            "Caption translation returned an invalid response.",
        )
    return [
        segment.model_copy(
            update={
                "text": str(translated[index]).strip() or segment.text,
                "words": words_for_segment_text(segment, str(translated[index]).strip()),
                "line_count": 2 if len(str(translated[index])) > 28 else 1,
            }
        )
        for index, segment in enumerate(segments)
    ]


def words_for_segment_text(segment, text: str):
    tokens = [token for token in text.split() if token.strip()]
    if not tokens:
        return []
    duration = max(len(tokens), segment.end_ms - segment.start_ms)
    step = max(1, duration // len(tokens))
    return [
        TranscriptWord(
            index=index,
            text=token,
            start_ms=segment.start_ms + index * step,
            end_ms=(
                segment.end_ms
                if index == len(tokens) - 1
                else segment.start_ms + (index + 1) * step
            ),
            confidence=None,
        )
        for index, token in enumerate(tokens)
    ]
