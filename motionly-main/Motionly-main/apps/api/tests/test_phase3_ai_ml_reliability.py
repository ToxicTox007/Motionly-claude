import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.plans import create_plan
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository, UploadRecord
from app.repositories.visual import InMemoryVisualRepository
from app.schemas.captions import (
    LanguageHint,
    TranscriptQualityStatus,
    TranscriptWord,
)
from app.schemas.plans import PlanCreateRequest
from app.schemas.uploads import UploadStatus, VideoMetadata
from app.services.job_engine import run_queue_job
from app.services.local_storage import LocalObjectStorage
from app.services.ml_failures import CudaOomFailure, clear_cuda_cache, is_cuda_oom_error
from app.services.queue import PlanQueue, QueueRegistry, VisualQueue
from app.services.transcript_quality import assess_transcript_quality
from app.services.transcription_jobs import run_transcription_for_upload
from app.services.transcription_provider import (
    FixtureTranscriptionProvider,
    TranscriptionResult,
)


class DummyResponse:
    status_code = 200


class MetricsRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, float, dict[str, object]]] = []

    def observe(self, name: str, value: float, **labels: object) -> None:
        self.events.append((name, value, labels))


class EmptyTranscriptionProvider(FixtureTranscriptionProvider):
    def transcribe(self, *, audio_path, language_hint):  # noqa: ANN001
        return TranscriptionResult(
            language="en",
            confidence=0.94,
            provider=self.provider_name,
            model=self.model,
            words=[],
            raw_payload={"fixture": True},
        )


class AudioHttpProvider:
    provider_name = "provider-b"
    model = "whisper-test"

    def transcribe(self, *, audio_path, language_hint):  # noqa: ANN001
        raise AssertionError("audio extraction should fail before provider call")


def test_cuda_oom_helpers_detect_and_clear_cache() -> None:
    assert is_cuda_oom_error(RuntimeError("CUDA out of memory while allocating tensor"))

    class FakeCuda:
        cleared = False

        @staticmethod
        def is_available() -> bool:
            return True

        @classmethod
        def empty_cache(cls) -> None:
            cls.cleared = True

    fake_torch = SimpleNamespace(cuda=FakeCuda)

    assert clear_cuda_cache(fake_torch) is True
    assert FakeCuda.cleared is True


def test_forced_cuda_oom_retries_with_backoff_and_records_structured_failure(tmp_path) -> None:
    context = _make_visual_context(tmp_path)
    upload = _seed_upload(context)

    class OomVisualProvider:
        provider_name = "fixture_visual"
        model = "oom-fixture"

        def analyze(self, **_kwargs):  # noqa: ANN003
            raise CudaOomFailure()

    context.request.app.state.visual_intelligence_provider = OomVisualProvider()
    queue_job = context.visual_queue.enqueue_visual_analysis(
        project_id=context.project_id,
        workspace_id=context.workspace_id,
        video_id=upload.id,
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            run_queue_job(
                queue_job_id=queue_job.id,
                request=context.request,
                x_motionly_worker_token="worker-secret",
            )
        )

    assert exc_info.value.code == "cuda_oom"
    retried = context.registry.jobs[queue_job.id]
    assert retried.status == "queued"
    assert retried.attempts == 1
    assert retried.error_code == "cuda_oom"
    failed_manifest = context.visual_repository.latest_for_upload(
        uploaded_video_id=upload.id,
        project_id=context.project_id,
        workspace_id=context.workspace_id,
    )
    assert failed_manifest is not None
    assert failed_manifest.error_code == "cuda_oom"
    assert any(event[0] == "cuda_oom_count" for event in context.metrics.events)

    context.registry.fail(queue_job.id, error_code="cuda_oom")
    context.registry.fail(queue_job.id, error_code="cuda_oom")
    assert context.registry.jobs[queue_job.id].status == "dead_letter"


def test_transcript_quality_flags_empty_low_repeated_silent_and_partial() -> None:
    words = [
        TranscriptWord(index=0, text="yes", start_ms=0, end_ms=300, confidence=0.4),
        TranscriptWord(index=1, text="yes", start_ms=8000, end_ms=8300, confidence=0.4),
        TranscriptWord(index=2, text="yes", start_ms=8500, end_ms=8800, confidence=0.4),
        TranscriptWord(index=3, text="yes", start_ms=9000, end_ms=9300, confidence=0.4),
        TranscriptWord(index=4, text="yes", start_ms=9500, end_ms=9800, confidence=0.4),
        TranscriptWord(index=5, text="go", start_ms=10000, end_ms=10300, confidence=0.4),
    ]

    empty = assess_transcript_quality(
        words=[],
        duration_ms=12_000,
        confidence=None,
        language="en",
        language_hint=LanguageHint.AUTO,
    )
    assert empty.status == TranscriptQualityStatus.EMPTY
    assert "empty_transcript" in empty.reasons

    low = assess_transcript_quality(
        words=words[:1],
        duration_ms=12_000,
        confidence=0.4,
        language="en",
        language_hint=LanguageHint.AUTO,
    )
    assert low.status == TranscriptQualityStatus.LOW_CONFIDENCE
    assert "suspiciously_low_word_count" in low.reasons
    assert "provider_low_confidence" in low.reasons

    repeated = assess_transcript_quality(
        words=words,
        duration_ms=12_000,
        confidence=0.4,
        language="es",
        language_hint=LanguageHint.ENGLISH,
    )
    assert repeated.status == TranscriptQualityStatus.LOW_CONFIDENCE
    assert "repeated_text" in repeated.reasons
    assert "long_silent_gaps" in repeated.reasons
    assert "language_mismatch" in repeated.reasons

    partial = assess_transcript_quality(
        words=words,
        duration_ms=12_000,
        confidence=0.9,
        language="en",
        language_hint=LanguageHint.AUTO,
        raw_payload={"partial_audio": True},
    )
    assert partial.status == TranscriptQualityStatus.PARTIAL
    assert "partial_audio" in partial.reasons


def test_empty_transcript_is_visible_and_blocks_automatic_planning(tmp_path) -> None:
    repository, project_id, workspace_id, user_id, upload = _make_caption_context(tmp_path)
    transcript = run_transcription_for_upload(
        upload_repository=repository.upload_repository,
        caption_repository=repository,
        transcription_provider=EmptyTranscriptionProvider(),
        workspace_id=workspace_id,
        project_id=project_id,
        video_id=upload.id,
        media_work_dir=str(tmp_path),
        supabase_url=None,
        service_role_key=None,
        retain_debug_audio=False,
    )
    assert transcript.status == "ready"
    assert transcript.quality_status == TranscriptQualityStatus.EMPTY
    assert transcript.quality_reasons == ["empty_transcript"]

    plan_repository = InMemoryPlanRepository(repository)
    registry = QueueRegistry()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env="test",
                    billing_enabled=False,
                    worker_api_token="worker-secret",
                ),
                queue_registry=registry,
                plan_repository=plan_repository,
                edit_planner_provider=SimpleNamespace(provider_name="fixture", model="fixture"),
                operations_repository=None,
            )
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            create_plan(
                project_id=project_id,
                payload=PlanCreateRequest(),
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                repository=plan_repository,
                planner_provider=FixtureTranscriptionProvider(),
                plan_queue=PlanQueue(registry=registry),
                response=DummyResponse(),
                request=request,
            )
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "empty_transcript"


def test_audio_extraction_failure_creates_recoverable_quality_state(tmp_path, monkeypatch) -> None:
    repository, project_id, workspace_id, _user_id, upload = _make_caption_context(tmp_path)

    def fail_extract(*_args, **_kwargs) -> None:
        raise RuntimeError("ffmpeg failed with signed url")

    monkeypatch.setattr(
        "app.services.transcription_jobs.FFmpegAudioExtractor.extract_mono_wav",
        fail_extract,
    )

    with pytest.raises(RuntimeError):
        run_transcription_for_upload(
            upload_repository=repository.upload_repository,
            caption_repository=repository,
            transcription_provider=AudioHttpProvider(),
            workspace_id=workspace_id,
            project_id=project_id,
            video_id=upload.id,
            media_work_dir=str(tmp_path),
            supabase_url=None,
            service_role_key=None,
            retain_debug_audio=False,
        )

    transcript = repository.latest_transcript(project_id=project_id, workspace_id=workspace_id)
    assert transcript is not None
    assert transcript.status == "failed"
    assert transcript.error_code == "audio_extraction_failed"
    assert transcript.quality_status == TranscriptQualityStatus.AUDIO_FAILED
    assert (
        transcript.error_message
        == "Audio extraction failed. Upload a replacement file or retry transcription."
    )


def test_valid_transcript_still_proceeds_normally(tmp_path) -> None:
    repository, project_id, workspace_id, _user_id, upload = _make_caption_context(tmp_path)
    transcript = run_transcription_for_upload(
        upload_repository=repository.upload_repository,
        caption_repository=repository,
        transcription_provider=FixtureTranscriptionProvider(),
        workspace_id=workspace_id,
        project_id=project_id,
        video_id=upload.id,
        media_work_dir=str(tmp_path),
        supabase_url=None,
        service_role_key=None,
        retain_debug_audio=False,
    )
    assert transcript.status == "ready"
    assert transcript.quality_status == TranscriptQualityStatus.OK
    assert transcript.words


def _make_caption_context(tmp_path) -> tuple[
    InMemoryCaptionRepository,
    object,
    object,
    object,
    UploadRecord,
]:
    upload_repository = InMemoryUploadRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = upload_repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path=f"workspaces/{workspace_id}/projects/{project_id}/raw/video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=1000,
    )
    upload.status = UploadStatus.READY
    upload.metadata.duration_ms = 12_000
    upload.metadata.has_audio = True
    upload_repository.attach_active_video(project_id=project_id, video_id=upload.id)
    LocalObjectStorage(root=str(tmp_path)).write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"fake mp4",
    )
    return InMemoryCaptionRepository(upload_repository), project_id, workspace_id, user_id, upload


def _make_visual_context(tmp_path) -> SimpleNamespace:
    upload_repository = InMemoryUploadRepository()
    visual_repository = InMemoryVisualRepository(upload_repository)
    registry = QueueRegistry()
    metrics = MetricsRecorder()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env="test",
                    worker_api_token="worker-secret",
                    media_work_dir=str(tmp_path),
                    supabase_url=None,
                    supabase_service_role_key=None,
                    visual_sample_fps=2.0,
                    visual_scene_threshold=0.42,
                ),
                queue_registry=registry,
                metrics_registry=metrics,
                visual_repository=visual_repository,
                visual_intelligence_provider=None,
            )
        )
    )
    return SimpleNamespace(
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        request=request,
        registry=registry,
        metrics=metrics,
        upload_repository=upload_repository,
        visual_repository=visual_repository,
        visual_queue=VisualQueue(registry),
    )


def _seed_upload(context: SimpleNamespace) -> UploadRecord:
    upload = context.upload_repository.create_upload(
        project_id=context.project_id,
        workspace_id=context.workspace_id,
        storage_bucket="raw-uploads",
        storage_path=f"workspaces/{context.workspace_id}/projects/{context.project_id}/raw/video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=100,
    )
    upload.status = UploadStatus.READY
    upload.metadata = VideoMetadata(
        duration_ms=6000,
        width=1080,
        height=1920,
        fps=30,
        codec="h264",
        has_audio=True,
        orientation="vertical",
    )
    context.upload_repository.attach_active_video(
        project_id=context.project_id,
        video_id=upload.id,
    )
    LocalObjectStorage(root=context.request.app.state.settings.media_work_dir).write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"fake mp4",
    )
    return upload
