from typing import Annotated
from uuid import UUID

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
from app.schemas.captions import (
    CaptionGenerateRequest,
    CaptionGenerateResponse,
    CaptionListResponse,
    CaptionUpdateRequest,
    TranscriptionCreateRequest,
    TranscriptionCreateResponse,
    TranscriptReadResponse,
    TranscriptStatus,
)
from app.schemas.uploads import UploadStatus
from app.services.audio_extraction import FFmpegAudioExtractor
from app.services.caption_chunking import chunk_caption_words
from app.services.caption_presets import (
    CAPTION_SCHEMA_VERSION,
    get_caption_preset,
    list_caption_presets,
)
from app.services.local_storage import create_object_storage
from app.services.queue import TranscriptionQueue
from app.services.transcription_provider import FixtureTranscriptionProvider, TranscriptionProvider

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
    existing = repository.latest_transcript(project_id=project_id, workspace_id=actor.workspace_id)
    if existing and existing.uploaded_video_id == upload.id and not payload.force:
        job = transcription_queue.enqueue_transcription(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            video_id=upload.id,
        )
        return TranscriptionCreateResponse(
            transcript_id=existing.id,
            status=existing.status,
            job_id=job.id,
            message="Existing transcript is ready. Use force to regenerate.",
        )

    job = transcription_queue.enqueue_transcription(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        video_id=upload.id,
    )
    if upload.metadata.has_audio is False:
        transcript = repository.create_transcript(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            uploaded_video_id=upload.id,
            status=TranscriptStatus.CAPTIONS_UNAVAILABLE,
            language="unknown",
            language_hint=payload.language_hint,
            provider=transcription_provider.provider_name,
            model=transcription_provider.model,
            words=[],
            confidence=None,
            duration_ms=upload.metadata.duration_ms,
            raw_payload={},
            error_code="audio_track_missing",
            error_message="This video has no audio track, so captions cannot be generated.",
        )
        return TranscriptionCreateResponse(
            transcript_id=transcript.id,
            status=transcript.status,
            job_id=job.id,
            message="Captions are unavailable because this video has no audio.",
        )

    audio_path = None
    extractor = FFmpegAudioExtractor()
    if not isinstance(transcription_provider, FixtureTranscriptionProvider):
        settings = request.app.state.settings
        storage = create_object_storage(
            root=settings.media_work_dir,
            supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
            service_role_key=getattr(settings, "supabase_service_role_key", None),
        )
        source_path = storage.ensure_object(bucket=upload.storage_bucket, path=upload.storage_path)
        audio_path = source_path.with_suffix(".transcription.wav")
        extractor.extract_mono_wav(source_path=source_path, output_path=audio_path)
    try:
        result = transcription_provider.transcribe(
            audio_path=audio_path,
            language_hint=payload.language_hint,
        )
    finally:
        if audio_path is not None:
            extractor.cleanup(
                audio_path=audio_path,
                retain_debug_copy=request.app.state.settings.transcription_debug_retain_audio,
            )
    transcript = repository.create_transcript(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status=TranscriptStatus.READY,
        language=result.language,
        language_hint=payload.language_hint,
        provider=result.provider,
        model=result.model,
        words=result.words,
        confidence=result.confidence,
        duration_ms=upload.metadata.duration_ms,
        raw_payload=result.raw_payload,
    )
    return TranscriptionCreateResponse(
        transcript_id=transcript.id,
        status=transcript.status,
        job_id=job.id,
        message="Transcript generated with word-level timings.",
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
    segments = chunk_caption_words(transcript.words, hide_filler_words=payload.hide_filler_words)
    if not segments:
        raise ApiError(422, "caption_segments_empty", "No captionable words were found.")

    job = transcription_queue.enqueue_caption_generation(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        transcript_id=transcript.id,
    )
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
    return CaptionGenerateResponse(track=track.to_response(), job_id=job.id)


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
    segments = chunk_caption_words(transcript.words, hide_filler_words=hide_filler_words)
    job = transcription_queue.enqueue_caption_generation(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        transcript_id=transcript.id,
    )
    updated = repository.update_caption_track(
        track=track,
        preset=preset_key,
        style=preset.style,
        hide_filler_words=hide_filler_words,
        segments=segments,
    )
    return CaptionGenerateResponse(track=updated.to_response(), job_id=job.id)
