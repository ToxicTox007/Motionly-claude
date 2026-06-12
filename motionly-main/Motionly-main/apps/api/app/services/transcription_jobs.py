from uuid import UUID

from app.repositories.captions import InMemoryCaptionRepository, TranscriptRecord
from app.repositories.uploads import InMemoryUploadRepository, UploadRecord
from app.schemas.captions import LanguageHint, TranscriptStatus
from app.services.audio_extraction import FFmpegAudioExtractor
from app.services.local_storage import create_object_storage
from app.services.queue import TranscriptionQueue
from app.services.transcription_provider import FixtureTranscriptionProvider, TranscriptionProvider


def queue_transcription_for_upload(
    *,
    upload: UploadRecord,
    caption_repository: InMemoryCaptionRepository,
    transcription_queue: TranscriptionQueue,
    transcription_provider: TranscriptionProvider,
    language_hint: LanguageHint = LanguageHint.AUTO,
    force: bool = False,
) -> TranscriptRecord:
    existing = caption_repository.latest_transcript(
        project_id=upload.project_id,
        workspace_id=upload.workspace_id,
    )
    if (
        existing
        and existing.uploaded_video_id == upload.id
        and existing.status in {TranscriptStatus.QUEUED, TranscriptStatus.PROCESSING, TranscriptStatus.READY}
        and not force
    ):
        return existing

    transcript = caption_repository.create_transcript(
        workspace_id=upload.workspace_id,
        project_id=upload.project_id,
        uploaded_video_id=upload.id,
        status=TranscriptStatus.QUEUED,
        language="unknown",
        language_hint=language_hint,
        provider=transcription_provider.provider_name,
        model=transcription_provider.model,
        words=[],
        confidence=None,
        duration_ms=upload.metadata.duration_ms,
        raw_payload={},
        error_code=None,
        error_message=None,
    )
    transcription_queue.enqueue_transcription(
        project_id=upload.project_id,
        workspace_id=upload.workspace_id,
        video_id=upload.id,
    )
    return transcript


def run_transcription_for_upload(
    *,
    upload_repository: InMemoryUploadRepository,
    caption_repository: InMemoryCaptionRepository,
    transcription_provider: TranscriptionProvider,
    workspace_id: UUID,
    project_id: UUID,
    video_id: UUID,
    media_work_dir: str,
    supabase_url: str | None,
    service_role_key: str | None,
    retain_debug_audio: bool,
) -> TranscriptRecord:
    upload = upload_repository.get_upload_for_project(
        project_id=project_id,
        video_id=video_id,
        workspace_id=workspace_id,
    )
    transcript = caption_repository.latest_transcript(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    if transcript is None or transcript.uploaded_video_id != upload.id:
        transcript = caption_repository.create_transcript(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=upload.id,
            status=TranscriptStatus.QUEUED,
            language="unknown",
            language_hint=LanguageHint.AUTO,
            provider=transcription_provider.provider_name,
            model=transcription_provider.model,
            words=[],
            confidence=None,
            duration_ms=upload.metadata.duration_ms,
            raw_payload={},
            error_code=None,
            error_message=None,
        )

    if upload.metadata.has_audio is False:
        return caption_repository.update_transcript(
            transcript=transcript,
            status=TranscriptStatus.CAPTIONS_UNAVAILABLE,
            words=[],
            language="unknown",
            confidence=None,
            raw_payload={},
            error_code="audio_track_missing",
            error_message="This video has no audio track, so captions cannot be generated.",
            duration_ms=upload.metadata.duration_ms,
        )

    caption_repository.update_transcript(
        transcript=transcript,
        status=TranscriptStatus.PROCESSING,
        error_code=None,
        error_message=None,
        words=[],
    )

    audio_path = None
    extractor = FFmpegAudioExtractor()
    try:
        if not isinstance(transcription_provider, FixtureTranscriptionProvider):
            storage = create_object_storage(
                root=media_work_dir,
                supabase_url=supabase_url,
                service_role_key=service_role_key,
            )
            source_path = storage.ensure_object(
                bucket=upload.storage_bucket,
                path=upload.storage_path,
            )
            audio_path = source_path.with_suffix(".transcription.wav")
            extractor.extract_mono_wav(source_path=source_path, output_path=audio_path)

        result = transcription_provider.transcribe(
            audio_path=audio_path,
            language_hint=transcript.language_hint,
        )
        return caption_repository.update_transcript(
            transcript=transcript,
            status=TranscriptStatus.READY,
            language=result.language,
            provider=result.provider,
            model=result.model,
            confidence=result.confidence,
            words=result.words,
            raw_payload=result.raw_payload,
            error_code=None,
            error_message=None,
            duration_ms=upload.metadata.duration_ms,
        )
    except Exception as exc:
        caption_repository.update_transcript(
            transcript=transcript,
            status=TranscriptStatus.FAILED,
            words=[],
            confidence=None,
            raw_payload={},
            error_code="transcription_failed",
            error_message=str(exc)[:300],
        )
        raise
    finally:
        if audio_path is not None:
            extractor.cleanup(audio_path=audio_path, retain_debug_copy=retain_debug_audio)
