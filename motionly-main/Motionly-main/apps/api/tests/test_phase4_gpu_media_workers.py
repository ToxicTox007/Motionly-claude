import json
import os
import time
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
from app.services.compute_backend import (
    StaticGpuPoolBackend,
    compute_backend_from_settings,
)
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
from app.services.model_domain_adapters import register_matte_route
from app.services.model_gateway import ModelGateway, OperationsGatewayTelemetry
from app.services.operations import OperationsRepository
from app.services.queue import MatteQueue, QueueRegistry, QueueSnapshot
from app.services.render_jobs import _segmentation_matte_path_from_manifest
from app.services.visual_intelligence import _resize_frame_for_analysis
from app.workers import prefetch_models


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


def test_production_matte_routes_through_gateway_with_provenance_and_context(
    matte_context,
    monkeypatch,
):
    record, _job = enqueue_matte_for_upload(
        repository=matte_context.repository,
        matte_queue=matte_context.matte_queue,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        upload_id=matte_context.upload.id,
    )

    def fake_generate(**kwargs):
        kwargs["output_path"].write_bytes(b"gateway-matte")
        return {"fps": 30.0, "width": 1280, "height": 720, "frame_count": 9}

    monkeypatch.setattr(
        "app.services.segmentation_matte.generate_selfie_segmentation_matte",
        fake_generate,
    )
    operations = OperationsRepository()
    gateway = ModelGateway(
        adapters={},
        routes=[],
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=None,
            operations_repository=operations,
        ),
    )
    route = register_matte_route(
        gateway,
        provider_models={"cv2": object(), "np": object(), "selfie_segmentation": object()},
        revision="mediapipe-selfie-segmentation-v1",
    )

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
        model_gateway=gateway,
        matte_gateway_route=route,
        production=True,
    )

    assert completed.matte is not None
    assert completed.matte.model == f"{route.route_id}@{route.model_revision}"
    assert operations.provider_events[0]["operation"] == "vision.matte_segmentation"
    assert operations.provider_events[0]["workspace_id"] == matte_context.workspace_id
    assert operations.provider_events[0]["project_id"] == matte_context.project_id


def test_production_matte_fails_closed_before_direct_inference(
    matte_context,
    monkeypatch,
):
    record, _job = enqueue_matte_for_upload(
        repository=matte_context.repository,
        matte_queue=matte_context.matte_queue,
        workspace_id=matte_context.workspace_id,
        project_id=matte_context.project_id,
        upload_id=matte_context.upload.id,
    )

    def forbidden_direct_call(**_kwargs):
        raise AssertionError("production matte must not call the model directly")

    monkeypatch.setattr(
        "app.services.matte_jobs.generate_selfie_segmentation_matte",
        forbidden_direct_call,
    )
    with pytest.raises(ApiError) as exc_info:
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
            production=True,
        )

    assert exc_info.value.code == "matte_gateway_route_required"


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
    assert policy.max_worker_count == 3
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


def _static_pool_settings(**overrides):
    base = dict(
        gpu_pool_visual_replicas=1,
        gpu_pool_matte_replicas=1,
        gpu_pool_render_replicas=1,
        gpu_worker_instance_type="g5.xlarge",
        gpu_max_concurrent_jobs_per_gpu=2,
        gpu_memory_ceiling_mb=16000,
        matte_worker_concurrency=1,
        visual_worker_concurrency=1,
        gpu_scale_up_queue_depth=4,
        gpu_scale_down_queue_depth=0,
        gpu_autoscale_cooldown_seconds=300,
        gpu_max_worker_count=4,
        gpu_oom_alert_threshold=2,
        gpu_long_job_timeout_seconds=1800,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _snapshot(queue_name, queued, running=0):
    return QueueSnapshot(
        queue_name=queue_name,
        queued=queued,
        running=running,
        succeeded=0,
        failed=0,
        dead_letter=0,
        canceled=0,
        oldest_queued_seconds=60,
    )


def test_compute_backend_defaults_to_static_gpu_pool():
    settings = _static_pool_settings()
    backend = compute_backend_from_settings(settings)
    assert isinstance(backend, StaticGpuPoolBackend)
    assert backend.backend_name == "static_gpu_pool"


def test_compute_backend_uses_static_pool_without_a_backend_selector():
    settings = _static_pool_settings()
    delattr(settings, "gpu_worker_instance_type")
    backend = compute_backend_from_settings(settings)
    assert isinstance(backend, StaticGpuPoolBackend)


def test_compute_backend_disabled_when_static_pool_empty():
    settings = _static_pool_settings(
        gpu_pool_visual_replicas=0,
        gpu_pool_matte_replicas=0,
        gpu_pool_render_replicas=0,
    )
    assert StaticGpuPoolBackend.is_configured(settings) is False
    assert compute_backend_from_settings(settings) is None


def test_static_pool_reports_backlog_and_capacity_without_remote_calls():
    settings = _static_pool_settings(gpu_pool_visual_replicas=2)
    backend = StaticGpuPoolBackend(settings)
    result = backend.run([_snapshot("visual_analysis", queued=7, running=1)])

    assert result["status"] == "ok"
    assert result["backend"] == "static_gpu_pool"
    visual = next(p for p in result["pools"] if p["pool"] == "visual")
    assert visual["queues"] == ["visual_analysis", "transcription"]
    assert visual["backlog"] == 8
    assert visual["static_replicas"] == 2
    # 2 replicas * 2 jobs/replica = capacity 4; backlog 8 exceeds capacity.
    assert visual["capacity"] == 4
    assert visual["saturated"] is True
    # desired = ceil(8 / 2) = 4, capped at gpu_max_worker_count (4).
    assert visual["desired_replicas"] == 4


def test_static_pool_not_saturated_within_capacity():
    settings = _static_pool_settings(gpu_pool_render_replicas=2)
    backend = StaticGpuPoolBackend(settings)
    result = backend.run([_snapshot("render_gpu", queued=1)])

    render = next(p for p in result["pools"] if p["pool"] == "render")
    assert render["backlog"] == 1
    assert render["capacity"] == 4
    assert render["saturated"] is False
    assert render["desired_replicas"] == 1


def test_static_pool_desired_replicas_capped_by_max_worker_count():
    settings = _static_pool_settings(gpu_max_worker_count=3, gpu_pool_visual_replicas=1)
    backend = StaticGpuPoolBackend(settings)
    result = backend.run([_snapshot("visual_analysis", queued=100)])

    visual = next(p for p in result["pools"] if p["pool"] == "visual")
    assert visual["desired_replicas"] == 3


def test_model_prefetch_emits_readiness_telemetry(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MODEL_PREFETCH", "true")
    monkeypatch.setattr(prefetch_models.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(prefetch_models, "_disk_free_bytes", lambda _path: 2048)
    monkeypatch.setattr(prefetch_models, "_gpu_memory_allocated_mb", lambda: 128)
    monkeypatch.setattr(
        prefetch_models,
        "get_settings",
        lambda: SimpleNamespace(app_env="test", media_work_dir=str(tmp_path)),
    )

    prefetch_models.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "model_prefetch_completed"
    assert payload["models_attempted"] == 0
    assert payload["failure_count"] == 0
    assert payload["disk_free_bytes"] == 2048
    assert payload["gpu_memory_allocated_mb"] == 128
    assert payload["cache_budget"]["budget_bytes"] == 0
    assert payload["tmp_cleanup"]["removed"] >= 0


def test_worker_cache_budget_evicts_oldest_files(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    old_file = cache_dir / "old.bin"
    new_file = cache_dir / "new.bin"
    old_file.write_bytes(b"a" * 8)
    new_file.write_bytes(b"b" * 8)
    os.utime(old_file, (time.time() - 120, time.time() - 120))
    os.utime(new_file, (time.time(), time.time()))

    result = prefetch_models.enforce_worker_cache_budget(
        cache_dirs=[str(cache_dir)],
        max_bytes=8,
    )

    assert result["budget_bytes"] == 8
    assert result["total_bytes"] == 8
    assert result["removed"] == 1
    assert result["bytes_removed"] == 8
    assert not old_file.exists()
    assert new_file.exists()


def test_worker_tmp_cleanup_only_removes_old_motionly_paths(tmp_path):
    stale = tmp_path / "motionly-stale"
    fresh = tmp_path / "motionly-fresh"
    unrelated = tmp_path / "other-stale"
    stale.mkdir()
    fresh.mkdir()
    unrelated.mkdir()
    (stale / "payload.bin").write_bytes(b"x" * 4)
    (fresh / "payload.bin").write_bytes(b"x" * 4)
    (unrelated / "payload.bin").write_bytes(b"x" * 4)
    old_time = time.time() - 120
    os.utime(stale, (old_time, old_time))
    os.utime(stale / "payload.bin", (old_time, old_time))
    os.utime(unrelated, (old_time, old_time))

    result = prefetch_models.cleanup_worker_tmp(
        tmp_dir=str(tmp_path),
        max_age_seconds=60,
    )

    assert result["removed"] == 1
    assert result["bytes_removed"] == 4
    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_visual_analysis_resizes_large_frames_before_inference():
    calls = []
    large_frame = SimpleNamespace(shape=(1080, 1920, 3))
    resized_frame = SimpleNamespace(shape=(360, 640, 3))

    class FakeCv2:
        INTER_AREA = "area"

        @staticmethod
        def resize(frame, target_size, interpolation):
            calls.append((frame, target_size, interpolation))
            return resized_frame

    result = _resize_frame_for_analysis(cv2=FakeCv2, frame=large_frame)

    assert result is resized_frame
    assert calls == [(large_frame, (640, 360), "area")]


def test_visual_analysis_keeps_small_frames_without_copying():
    small_frame = SimpleNamespace(shape=(360, 640, 3))

    class FakeCv2:
        INTER_AREA = "area"

        @staticmethod
        def resize(*_args, **_kwargs):
            raise AssertionError("small frames should not be resized")

    assert _resize_frame_for_analysis(cv2=FakeCv2, frame=small_frame) is small_frame
