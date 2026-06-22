import json
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request

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
    CAPTION_SCHEMA_VERSION,
    get_caption_preset,
    list_caption_presets,
)
from app.services.queue import TranscriptionQueue
from app.services.transcription_jobs import queue_transcription_for_upload
from app.services.transcription_provider import TranscriptionProvider

router = APIRouter(prefix="/projects/{project_id}", tags=["transcripts and captions"])

ActorDep = Annotated[Actor, Depends(get_actor)]
CaptionRepositoryDep = Annotated[InMemoryCaptionRepository, Depends(get_caption_repository)]
TranscriptionProviderDep = Annotated[TranscriptionProvider, Depends(get_transcription_provider)]
TranscriptionQueueDep = Annotated[TranscriptionQueue, Depends(get_transcription_queue)]


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
    transcript = repository.latest_transcript(
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


@router.post("/captions/generate", response_model=CaptionGenerateResponse, status_code=201)
async def generate_captions(
    project_id: UUID,
    payload: CaptionGenerateRequest,
    actor: ActorDep,
    repository: CaptionRepositoryDep,
    transcription_queue: TranscriptionQueueDep,
    request: Request = None,  # type: ignore[assignment]
) -> CaptionGenerateResponse:
    transcript = (
        repository.get_transcript(
            transcript_id=payload.transcript_id,
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
        if payload.transcript_id
        else repository.latest_transcript(project_id=project_id, workspace_id=actor.workspace_id)
    )
    if not transcript:
        raise ApiError(404, "transcript_not_found", "Generate a transcript before captions.")
    if transcript.status != TranscriptStatus.READY:
        raise ApiError(
            409,
            str(transcript.status),
            transcript.error_message or "Transcript is not ready.",
        )

    preset = get_caption_preset(payload.preset)
    segments = chunk_caption_words(
        transcript.words,
        hide_filler_words=payload.hide_filler_words,
        style=preset.style,
    )
    segments = translate_segments_if_requested(
        segments=segments,
        source_language=transcript.language,
        output_language=payload.output_language,
        settings=request.app.state.settings if request else None,
    )
    if not segments:
        raise ApiError(422, "caption_segments_empty", "No captionable words were found.")

    track = repository.create_caption_track(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        transcript_id=transcript.id,
        preset=payload.preset,
        schema_version=CAPTION_SCHEMA_VERSION,
        style=preset.style,
        hide_filler_words=payload.hide_filler_words,
        segments=segments,
    )
    return CaptionGenerateResponse(
        track=track.to_response(),
        job_id=f"sync-complete--captions--{project_id}--{track.id}",
    )


@router.get("/captions", response_model=CaptionListResponse)
async def list_captions(
    project_id: UUID,
    actor: ActorDep,
    repository: CaptionRepositoryDep,
) -> CaptionListResponse:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=actor.workspace_id)
    tracks = repository.list_caption_tracks(project_id=project_id, workspace_id=actor.workspace_id)
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
    track = repository.get_caption_track(
        track_id=track_id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    transcript = repository.get_transcript(
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
    segments = translate_segments_if_requested(
        segments=segments,
        source_language=transcript.language,
        output_language=payload.output_language or "original",
        settings=request.app.state.settings if request else None,
    )
    updated = repository.update_caption_track(
        track=track,
        preset=preset_key,
        style=style,
        hide_filler_words=hide_filler_words,
        segments=segments,
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
    track = repository.get_caption_track(
        track_id=track_id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    updated = repository.replace_caption_segments(
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
    settings,
):
    target = (output_language or "original").strip().lower()
    if target in {"original", "auto", source_language, ""}:
        return segments
    if target not in LANGUAGE_NAMES:
        raise ApiError(422, "caption_language_unsupported", "Choose a supported caption language.")
    if settings is None or not settings.openrouter_api_key:
        raise ApiError(
            503,
            "caption_translation_unavailable",
            "Caption translation is not configured for production yet.",
        )
    payload = {
        "model": settings.edit_planner_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Translate short video caption segments. Return only JSON with key "
                    "'segments', an array of translated strings in the same order. Keep meaning, "
                    "creator tone, punctuation, and short caption length."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "source_language": source_language,
                        "target_language": LANGUAGE_NAMES[target],
                        "segments": [segment.text for segment in segments],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }
    try:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "authorization": f"Bearer {settings.openrouter_api_key}",
                "content-type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        translated = json.loads(content).get("segments", [])
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
