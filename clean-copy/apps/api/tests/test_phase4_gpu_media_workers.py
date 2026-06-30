from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.repositories.uploads import InMemoryUploadRepository
from app.repositories.visual import InMemoryVisualRepository
from app.schemas.effects import BackgroundRemovalSettings
from app.schemas.uploads import UploadStatus
from app.schemas.visual import MatteStatus, SegmentationMatte
from app.services.gpu_autoscaling import (
    autoscale_decision,
    oom_alert_event,
    policy_from_settings,
    should_timeout_job,
)
from app.services.local_storage import LocalObjectStorage
from app.services.matte_jobs import (
    MATTE_MODEL_VERSION,
    build_matte_cache_key,
    enqueue_matte_for_upload,
    matte_availability,
    matte_segmentation_settings,
    run_matte_for_upload,
)
from app.services.queue import MatteQueue, QueueRegistry
from app.services.render_jobs import _segmentation_matte_path_from_manifest


@pytest.fixture
def matte_context(tmp_path):
    workspace_id = uuid4()
    project_id = uuid4()
    uploads = InMemoryUploadRepository()
    uploads.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = uploads.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="source.mp4",
        original_filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=128,
    )
    upload.status = UploadStatus.READY
    uploads.attach_active_video(project_id=project_id, video_id=upload.id)
    LocalObjectStorage(root=tmp_path).write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"fake-video-bytes",
    )
    repository = InMemoryVisualRepository(uploads)
    registry = QueueRegistry()
    return SimpleNamespace(
        workspace_id=workspace_id,
        project_id=project_id,
        upload=upload,
        repository=repository,
        registry=registry,
        matte_queue=MatteQueue(registry),
        media_root=str(tmp_path),
    )


def _completed_matte(cache_key: str = "matte:v1:test") -> SegmentationMatte:
    return SegmentationMatte(
        bucket="raw-uploads",
        path="matte.mp4",
        model=MATTE_MODEL_VERSION,
        fps=30.0,
        width=1920,
        height=1080,
        frame_count=12,
        cache_key=cache_key,
        status=MatteStatus.COMPLETED,
    )


def test_matte_jobs_enqueue_to_dedicated_queue(matte_context):
    record, job = enqueue_matte_for_upload(
        repository=matte_context.repository,
        matte_queue=matte_context.matte_queue,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        upload_id=matte_context.upload.id,
        background_removal=BackgroundRemovalSettings(enabled=True),
    )

    assert record.status == MatteStatus.PENDING
    assert job is not None
    assert job.queue_name == "matte_extraction"
    assert job.kind == "matte_extraction"
    assert all(
        queued.queue_name not in {"transcription", "planning"}
        for queued in matte_context.registry.jobs.values()
    )


def test_matte_cache_collapses_duplicate_pending_work(matte_context):
    first, first_job = enqueue_matte_for_upload(
        repository=matte_context.repository,
        matte_queue=matte_context.matte_queue,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        upload_id=matte_context.upload.id,
    )
    second, second_job = enqueue_matte_for_upload(
        repository=matte_context.repository,
        matte_queue=matte_context.matte_queue,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        upload_id=matte_context.upload.id,
    )

    assert second.id == first.id
    assert first_job is not None
    assert second_job is None
    assert len(matte_context.registry.jobs) == 1


def test_completed_matte_reuse_requires_matching_settings_and_model(matte_context):
    settings = matte_segmentation_settings({"enabled": True, "feather_px": 18})
    cache_key = build_matte_cache_key(
        upload_id=matte_context.upload.id,
        source_file_hash="hash-1",
        segmentation_settings=settings,
    )
    matte_context.repository.create_matte(
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        uploaded_video_id=matte_context.upload.id,
        status=MatteStatus.COMPLETED,
        cache_key=cache_key,
        source_file_hash="hash-1",
        segmentation_settings=settings,
        model_version=MATTE_MODEL_VERSION,
        matte=_completed_matte(cache_key),
    )

    reused, reused_job = enqueue_matte_for_upload(
        repository=matte_context.repository,
        matte_queue=matte_context.matte_queue,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        upload_id=matte_context.upload.id,
        background_removal=settings,
    )
    changed, changed_job = enqueue_matte_for_upload(
        repository=matte_context.repository,
        matte_queue=matte_context.matte_queue,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        upload_id=matte_context.upload.id,
        background_removal={"enabled": True, "feather_px": 30},
    )

    changed_settings_key = build_matte_cache_key(
        upload_id=matte_context.upload.id,
        source_file_hash="hash-1",
        segmentation_settings=matte_segmentation_settings({"enabled": True, "feather_px": 30}),
    )
    changed_model_key = build_matte_cache_key(
        upload_id=matte_context.upload.id,
        source_file_hash="hash-1",
        segmentation_settings=settings,
        model_version="different-model:v2",
    )
    assert reused_job is None
    assert reused.status == MatteStatus.COMPLETED
    assert changed_job is not None
    assert changed.status == MatteStatus.PENDING
    assert changed_settings_key != cache_key
    assert changed_model_key != cache_key


def test_run_matte_status_transitions_to_completed(matte_context, monkeypatch):
    record, _job = enqueue_matte_for_upload(
        repository=matte_context.repository,
        matte_queue=matte_context.matte_queue,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        upload_id=matte_context.upload.id,
    )

    def fake_generate(**kwargs):
        kwargs["output_path"].write_bytes(b"matte-bytes")
        return {"fps": 30.0, "width": 1280, "height": 720, "frame_count": 9}

    monkeypatch.setattr("app.services.matte_jobs.generate_selfie_segmentation_matte", fake_generate)
    completed = run_matte_for_upload(
        repository=matte_context.repository,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        video_id=matte_context.upload.id,
        matte_id=record.id,
        media_work_dir=matte_context.media_root,
        supabase_url=None,
        service_role_key=None,
        provider_models={"cv2": object(), "np": object(), "selfie_segmentation": object()},
    )

    assert completed.status == MatteStatus.COMPLETED
    assert completed.matte is not None
    assert completed.matte.cache_key == completed.cache_key
    assert completed.source_file_hash is not None


def test_matte_failure_is_recoverable_and_retryable(matte_context, monkeypatch):
    record, _job = enqueue_matte_for_upload(
        repository=matte_context.repository,
        matte_queue=matte_context.matte_queue,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        upload_id=matte_context.upload.id,
    )

    def fail_generate(**_kwargs):
        raise ApiError(422, "segmentation_frames_empty", "No foreground frames were found.")

    monkeypatch.setattr("app.services.matte_jobs.generate_selfie_segmentation_matte", fail_generate)
    with pytest.raises(ApiError):
        run_matte_for_upload(
            repository=matte_context.repository,
            workspace_id=matte_context.workspace_id,
            project_id=matte_context.project_id,
            video_id=matte_context.upload.id,
            matte_id=record.id,
            media_work_dir=matte_context.media_root,
            supabase_url=None,
            service_role_key=None,
            provider_models={"cv2": object(), "np": object(), "selfie_segmentation": object()},
        )

    failed = matte_context.repository.mattes[record.id]
    assert failed.status == MatteStatus.FAILED
    assert failed.error_code == "segmentation_frames_empty"
    assert failed.retry_count == 1

    retry_record, retry_job = enqueue_matte_for_upload(
        repository=matte_context.repository,
        matte_queue=matte_context.matte_queue,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        upload_id=matte_context.upload.id,
        force=True,
    )
    assert retry_record.id == failed.id
    assert retry_record.status == MatteStatus.PENDING
    assert retry_job is not None


def test_render_detects_matte_availability_states(matte_context):
    storage = LocalObjectStorage(root=matte_context.media_root)
    storage.write_object(bucket="raw-uploads", path="matte.mp4", content=b"matte")
    completed = matte_context.repository.create_matte(
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        uploaded_video_id=matte_context.upload.id,
        status=MatteStatus.COMPLETED,
        cache_key="matte:v1:available",
        source_file_hash="hash",
        segmentation_settings=matte_segmentation_settings(),
        model_version=MATTE_MODEL_VERSION,
        matte=_completed_matte("matte:v1:available"),
    )
    pending = matte_context.repository.create_matte(
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        uploaded_video_id=matte_context.upload.id,
        status=MatteStatus.PENDING,
        cache_key="matte:v1:pending",
        source_file_hash=None,
        segmentation_settings=matte_segmentation_settings(),
        model_version=MATTE_MODEL_VERSION,
    )
    failed = matte_context.repository.transition_matte(
        record=pending,
        status=MatteStatus.FAILED,
        error_code="matte_generation_failed",
        error_message="retryable",
    )

    assert matte_availability(completed)["status"] == "available"
    assert matte_availability(failed)["status"] == "failed"
    assert matte_availability(None)["status"] == "skipped"
    base_manifest = {
        "render_props": {
            "effectsConfig": {"background_removal": {"enabled": True}},
            "segmentationMatte": {"bucket": "raw-uploads", "path": "matte.mp4"},
        }
    }
    for status in ("pending", "failed", "skipped"):
        manifest = {
            "render_props": {
                **base_manifest["render_props"],
                "segmentationMatteStatus": {"status": status, "reason": f"matte_{status}"},
            }
        }
        assert _segmentation_matte_path_from_manifest(manifest, storage=storage) is None

    available_manifest = {
        "render_props": {
            **base_manifest["render_props"],
            "segmentationMatteStatus": {"status": "available", "reason": None},
            "segmentationMatte": {
                "bucket": "raw-uploads",
                "path": "matte.mp4",
                "status": "completed",
            },
        }
    }
    assert _segmentation_matte_path_from_manifest(available_manifest, storage=storage).exists()


def test_gpu_autoscaling_policy_and_resource_limits():
    settings = SimpleNamespace(
        gpu_worker_instance_type="g5.xlarge",
        gpu_max_concurrent_jobs_per_gpu="0",
        gpu_memory_ceiling_mb="bad",
        matte_worker_concurrency=2,
        visual_worker_concurrency=1,
        gpu_scale_up_queue_depth=4,
        gpu_scale_down_queue_depth=1,
        gpu_autoscale_cooldown_seconds=300,
        gpu_max_worker_count=3,
        gpu_oom_alert_threshold=2,
        gpu_long_job_timeout_seconds=30,
    )
    policy = policy_from_settings(settings)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    assert policy.max_concurrent_jobs_per_gpu == 1
    assert policy.gpu_memory_ceiling_mb == 16000
    assert policy.matte_worker_concurrency == 2
    assert autoscale_decision(
        policy=policy,
        queue_depth=5,
        current_workers=1,
        last_scaled_at=None,
        now=now,
    ) == "scale_up"
    assert autoscale_decision(
        policy=policy,
        queue_depth=8,
        current_workers=3,
        last_scaled_at=None,
        now=now,
    ) == "hold"
    assert autoscale_decision(
        policy=policy,
        queue_depth=0,
        current_workers=2,
        last_scaled_at=now - timedelta(seconds=301),
        now=now,
    ) == "scale_down"
    assert autoscale_decision(
        policy=policy,
        queue_depth=0,
        current_workers=2,
        last_scaled_at=now - timedelta(seconds=10),
        now=now,
    ) == "hold_cooldown"
    assert should_timeout_job(started_at=now - timedelta(seconds=31), now=now, policy=policy)
    assert oom_alert_event(oom_count=1, policy=policy) is None
    assert oom_alert_event(oom_count=2, policy=policy)["event"] == "gpu_oom_threshold_exceeded"
