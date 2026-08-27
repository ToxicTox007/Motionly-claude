from pathlib import Path
from uuid import UUID

from app.repositories.captions import InMemoryCaptionRepository, TranscriptRecord
from app.repositories.uploads import InMemoryUploadRepository, UploadRecord
from app.schemas.captions import LanguageHint, TranscriptQualityStatus, TranscriptStatus
from app.services.audio_extraction import FFmpegAudioExtractor
from app.services.audio_intelligence import merge_audio_intelligence
from app.services.local_storage import create_object_storage
from app.services.provider_resilience import ProviderCallFailure, classify_provider_exception
from app.services.queue import TranscriptionQueue
from app.services.transcript_quality import assess_transcript_quality
from app.services.transcription_provider import FixtureTranscriptionProvider, TranscriptionProvider
from app.services.worker_media import materialize_upload_source


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
        and existing.status
        in {TranscriptStatus.QUEUED, TranscriptStatus.PROCESSING, TranscriptStatus.READY}
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
    if upload.metadata.has_audio is False:
        return caption_repository.update_transcript(
            transcript=transcript,
            status=TranscriptStatus.CAPTIONS_UNAVAILABLE,
            words=[],
            language="unknown",
            confidence=None,
            raw_payload=merge_audio_intelligence(
                raw_payload={},
                words=[],
                duration_ms=upload.metadata.duration_ms,
                has_audio=False,
            ),
            error_code="audio_track_missing",
            error_message="This video has no audio track, so captions cannot be generated.",
            duration_ms=upload.metadata.duration_ms,
            quality_status=TranscriptQualityStatus.AUDIO_FAILED,
            quality_reasons=["audio_extraction_failed"],
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
    audio_ml_providers: dict[str, object] | None = None,
    audio_intelligence_provider: object | None = None,
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
    elif transcript.status == TranscriptStatus.READY:
        return transcript

    if upload.metadata.has_audio is False:
        return caption_repository.update_transcript(
            transcript=transcript,
            status=TranscriptStatus.CAPTIONS_UNAVAILABLE,
            words=[],
            language="unknown",
            confidence=None,
            raw_payload=merge_audio_intelligence(
                raw_payload={},
                words=[],
                duration_ms=upload.metadata.duration_ms,
                has_audio=False,
            ),
            error_code="audio_track_missing",
            error_message="This video has no audio track, so captions cannot be generated.",
            duration_ms=upload.metadata.duration_ms,
            quality_status=TranscriptQualityStatus.AUDIO_FAILED,
            quality_reasons=["audio_extraction_failed"],
        )

    caption_repository.update_transcript(
        transcript=transcript,
        status=TranscriptStatus.PROCESSING,
        error_code=None,
        error_message=None,
        words=[],
        quality_status=TranscriptQualityStatus.OK,
        quality_reasons=[],
    )

    audio_path = None
    stage = "transcription"
    extractor = FFmpegAudioExtractor()
    try:
        if not isinstance(transcription_provider, FixtureTranscriptionProvider):
            stage = "audio_extraction"
            storage = create_object_storage(
                root=media_work_dir,
                supabase_url=supabase_url,
                service_role_key=service_role_key,
            )
            source_path = materialize_upload_source(
                upload=upload,
                storage=storage,
            )
            audio_path = source_path.with_suffix(".transcription.wav")
            extractor.extract_mono_wav(source_path=source_path, output_path=audio_path)
            stage = "transcription"

        contextual_transcribe = getattr(
            transcription_provider,
            "transcribe_with_context",
            None,
        )
        if callable(contextual_transcribe):
            result = contextual_transcribe(
                audio_path=audio_path,
                language_hint=transcript.language_hint,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        else:
            result = transcription_provider.transcribe(
                audio_path=audio_path,
                language_hint=transcript.language_hint,
            )
        raw_payload = merge_audio_intelligence(
            raw_payload=result.raw_payload,
            words=result.words,
            duration_ms=upload.metadata.duration_ms,
            has_audio=upload.metadata.has_audio,
            ml_insights=(
                audio_intelligence_provider.analyze_with_context(
                    audio_path=audio_path,
                    workspace_id=workspace_id,
                    project_id=project_id,
                )
                if audio_intelligence_provider is not None and audio_path is not None
                else _run_audio_ml_providers(
                    providers=audio_ml_providers,
                    audio_path=audio_path,
                )
            ),
        )
        quality = assess_transcript_quality(
            words=result.words,
            duration_ms=upload.metadata.duration_ms,
            confidence=result.confidence,
            language=result.language,
            language_hint=transcript.language_hint,
            raw_payload=raw_payload,
        )
        error_code = None
        error_message = None
        if quality.status in {TranscriptQualityStatus.EMPTY, TranscriptQualityStatus.AUDIO_FAILED}:
            error_code = quality.reasons[0] if quality.reasons else quality.status.value
            error_message = "Transcript quality must be recovered before automatic planning."
        elif quality.status == TranscriptQualityStatus.LOW_CONFIDENCE:
            error_code = quality.reasons[0] if quality.reasons else "low_confidence"
            error_message = "Transcript quality is low and should be reviewed."
        return caption_repository.update_transcript(
            transcript=transcript,
            status=TranscriptStatus.READY,
            language=result.language,
            provider=result.provider,
            model=result.model,
            confidence=result.confidence,
            words=result.words,
            raw_payload=raw_payload,
            error_code=error_code,
            error_message=error_message,
            duration_ms=upload.metadata.duration_ms,
            quality_status=quality.status,
            quality_reasons=quality.reasons,
        )
    except ProviderCallFailure as exc:
        error_info = classify_provider_exception(exc)
        caption_repository.update_transcript(
            transcript=transcript,
            status=TranscriptStatus.FAILED,
            words=[],
            confidence=None,
            raw_payload={},
            error_code=error_info.code,
            error_message=exc.safe_message,
            quality_status=TranscriptQualityStatus.AUDIO_FAILED,
            quality_reasons=[error_info.code],
        )
        raise
    except Exception:
        error_code = (
            "audio_extraction_failed"
            if stage == "audio_extraction"
            else "transcription_failed"
        )
        error_message = (
            "Audio extraction failed. Upload a replacement file or retry transcription."
            if error_code == "audio_extraction_failed"
            else "Transcription failed. Retry the job or use a different provider."
        )
        caption_repository.update_transcript(
            transcript=transcript,
            status=TranscriptStatus.FAILED,
            words=[],
            confidence=None,
            raw_payload={"audio_extraction_failed": error_code == "audio_extraction_failed"},
            error_code=error_code,
            error_message=error_message,
            quality_status=TranscriptQualityStatus.AUDIO_FAILED,
            quality_reasons=[error_code],
        )
        raise
    finally:
        if audio_path is not None:
            extractor.cleanup(audio_path=audio_path, retain_debug_copy=retain_debug_audio)


def _run_audio_ml_providers(
    *,
    providers: dict[str, object] | None,
    audio_path: Path | None,
) -> dict[str, object]:
    if not providers or audio_path is None:
        return {}
    import logging
    logger = logging.getLogger(__name__)

    insights: dict[str, object] = {}
    diarization = providers.get("speaker_diarization")
    if diarization is not None and hasattr(diarization, "diarize"):
        try:
            insights["speaker_diarization"] = diarization.diarize(audio_path=audio_path)
        except Exception as exc:
            logger.error(
                "audio_ml_provider_failed",
                extra={
                    "provider": "speaker_diarization",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            insights["speaker_diarization"] = {
                "provider": getattr(diarization, "provider_name", "pyannote"),
                "model": getattr(diarization, "model", "unknown"),
                "speaker_count": 0,
                "segments": [],
                "confidence": None,
                "degraded": True,
            }
    emotion = providers.get("emotion")
    if emotion is not None and hasattr(emotion, "classify_windows"):
        try:
            insights["emotion_windows"] = emotion.classify_windows(audio_path=audio_path)
        except Exception as exc:
            logger.error(
                "audio_ml_provider_failed",
                extra={"provider": "emotion", "error_type": type(exc).__name__, "error": str(exc)},
            )
            insights["emotion_windows"] = []
    sound = providers.get("sound")
    if sound is not None and hasattr(sound, "classify"):
        try:
            insights["sound_classification"] = sound.classify(audio_path=audio_path)
        except Exception as exc:
            logger.error(
                "audio_ml_provider_failed",
                extra={"provider": "sound", "error_type": type(exc).__name__, "error": str(exc)},
            )
            insights["sound_classification"] = {
                "provider": getattr(sound, "provider_name", "yamnet"),
                "model": getattr(sound, "model_url", "unknown"),
                "labels": [],
                "degraded": True,
            }
    return insights
