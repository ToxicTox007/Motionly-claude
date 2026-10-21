import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.api.dependencies import Actor
from app.api.visual import get_visual_status
from app.repositories.uploads import InMemoryUploadRepository, UploadRecord
from app.repositories.visual import (
    SEGMENTATION_MATTE_COLUMNS,
    VIDEO_MANIFEST_COLUMNS,
    InMemoryVisualRepository,
    SupabaseVisualRepository,
)
from app.schemas.uploads import UploadStatus, VideoMetadata
from app.schemas.visual import (
    CameraMotion,
    MatteStatus,
    SceneType,
    SegmentationMatte,
    VisualManifestStatus,
)
from app.services.job_engine import run_queue_job
from app.services.local_storage import LocalObjectStorage
from app.services.queue import QueueRegistry, VisualQueue
from app.services.storage import StorageSigner
from app.services.visual_intelligence import FixtureVisualIntelligenceProvider
from app.services.visual_jobs import (
    build_visual_cache_key,
    queue_visual_analysis_for_upload,
    visual_provider_identity,
)


def _schedule_visual_analysis(
    context,
    *,
    force: bool = False,
    sample_rate_fps: float | None = None,
    scene_threshold: float | None = None,
):
    """Schedule visual analysis the same way the production pipeline does.

    Mirrors the service orchestration (provider identity + cache key + queue)
    that the worker-driven CPU pipeline performs; the worker job itself is run
    separately via ``run_queue_job``. Returns the ``VisualScheduleResult``.
    """
    settings = context.request.app.state.settings
    upload = context.visual_repository.get_active_upload(
        project_id=context.project_id, workspace_id=context.workspace_id
    )
    provider_name, model = visual_provider_identity(settings)
    cache_key = build_visual_cache_key(
        upload=upload,
        provider=provider_name,
        model=model,
        sample_rate_fps=(
            sample_rate_fps if sample_rate_fps is not None else settings.visual_sample_fps
        ),
        scene_threshold=(
            scene_threshold if scene_threshold is not None else settings.visual_scene_threshold
        ),
    )
    return queue_visual_analysis_for_upload(
        upload=upload,
        repository=context.visual_repository,
        visual_queue=context.visual_queue,
        provider=provider_name,
        model=model,
        cache_key=cache_key,
        force=force,
    )


def test_visual_analysis_builds_manifest_and_reuses_cached_result(tmp_path) -> None:
    context = _make_visual_context(tmp_path)
    upload = _seed_upload(context, status=UploadStatus.READY)

    first = _schedule_visual_analysis(context)

    assert first.cached is False
    assert first.job is not None
    assert first.job.id == f"visual--{context.project_id}--{upload.id}--{context.workspace_id}"
    assert first.manifest.status == VisualManifestStatus.QUEUED
    assert first.manifest.uploaded_video_id == upload.id
    assert first.manifest.cache_key == build_visual_cache_key(
        upload=upload,
        provider="fixture",
        model="fixture-visual-v1",
        sample_rate_fps=2.0,
        scene_threshold=0.42,
    )
    assert first.manifest.manifest is None

    asyncio.run(
        run_queue_job(
            queue_job_id=first.job.id,
            request=context.request,
            x_motionly_worker_token="worker-secret",
        )
    )
    ready = context.visual_repository.latest_ready_for_upload(
        uploaded_video_id=upload.id,
        project_id=context.project_id,
        workspace_id=context.workspace_id,
    )
    assert ready is not None and ready.manifest is not None

    manifest = ready.manifest
    assert manifest.provider == "fixture_visual"
    assert manifest.sample_rate_fps == 2.0
    assert manifest.frame_count == len(manifest.frame_samples)
    assert manifest.frame_count >= 2
    assert manifest.scenes
    assert manifest.summary["scene_count"] == len(manifest.scenes)
    assert manifest.summary["sample_count"] == manifest.frame_count
    assert manifest.model_versions == {
        "clip": "fixture-hash-embedding",
        "blip": "fixture-captioner",
        "object_detection": "fixture-yolo",
        "face_detection": "fixture-mediapipe",
    }

    sample = manifest.frame_samples[0]
    assert sample.embedding
    assert sample.caption
    assert sample.objects
    assert sample.dominant_colors
    assert sample.camera_motion in {
        CameraMotion.PAN,
        CameraMotion.STATIC,
        CameraMotion.UNKNOWN,
    }
    assert sample.scene_type in {SceneType.TALKING_HEAD, SceneType.PRODUCT}
    assert 0 < sample.visual_importance_score <= 1

    representative_scene = manifest.scenes[0]
    assert representative_scene.end_ms > representative_scene.start_ms
    assert representative_scene.embedding
    assert representative_scene.dominant_colors
    assert 0 < representative_scene.visual_importance_score <= 1

    second = _schedule_visual_analysis(context)

    assert second.cached is True
    assert second.manifest.id == ready.id
    assert second.manifest.version == first.manifest.version
    assert second.manifest.cache_key == first.manifest.cache_key

    fetched = context.visual_repository.latest_manifest(
        project_id=context.project_id,
        workspace_id=context.workspace_id,
    )
    assert fetched.id == ready.id
    assert fetched.manifest is not None
    assert fetched.manifest.summary["top_objects"]


def test_visual_analysis_force_creates_new_manifest_version(tmp_path) -> None:
    context = _make_visual_context(tmp_path)
    _seed_upload(context, status=UploadStatus.READY)

    first = _schedule_visual_analysis(context)
    asyncio.run(
        run_queue_job(
            queue_job_id=first.job.id,
            request=context.request,
            x_motionly_worker_token="worker-secret",
        )
    )
    forced = _schedule_visual_analysis(
        context, force=True, sample_rate_fps=1.0, scene_threshold=0.25
    )

    assert forced.cached is False
    assert forced.manifest.id != first.manifest.id
    assert forced.manifest.version == first.manifest.version + 1
    assert forced.manifest.status == VisualManifestStatus.QUEUED
    assert forced.manifest.manifest is None


def test_worker_runs_visual_analysis_job(tmp_path) -> None:
    context = _make_visual_context(tmp_path)
    upload = _seed_upload(context, status=UploadStatus.READY)

    queue_job = context.visual_queue.enqueue_visual_analysis(
        project_id=context.project_id,
        workspace_id=context.workspace_id,
        video_id=upload.id,
    )

    response = asyncio.run(
        run_queue_job(
            queue_job_id=queue_job.id,
            request=context.request,
            x_motionly_worker_token="worker-secret",
        )
    )

    latest = context.visual_repository.latest_ready_for_upload(
        uploaded_video_id=upload.id,
        project_id=context.project_id,
        workspace_id=context.workspace_id,
    )

    assert response.status == "succeeded"
    assert response.result["visual_status"] == "ready"
    assert response.result["scene_count"] > 0
    assert latest is not None
    assert latest.manifest is not None
    assert latest.manifest.scenes


def test_visual_status_is_processing_then_ready(tmp_path) -> None:
    context = _make_visual_context(tmp_path)
    _seed_upload(context, status=UploadStatus.READY)
    scheduled = _schedule_visual_analysis(context)
    processing = asyncio.run(
        get_visual_status(
            project_id=context.project_id,
            request=context.request,
            actor=context.actor,
            repository=context.visual_repository,
        )
    )
    assert processing.status == "processing"
    asyncio.run(
        run_queue_job(
            queue_job_id=scheduled.job.id,
            request=context.request,
            x_motionly_worker_token="worker-secret",
        )
    )
    ready = asyncio.run(
        get_visual_status(
            project_id=context.project_id,
            request=context.request,
            actor=context.actor,
            repository=context.visual_repository,
        )
    )
    assert ready.status == "ready"
    assert ready.manifest and ready.manifest.manifest
    assert ready.matte_preview.status in {"skipped", "approximate"}


def test_visual_status_returns_signed_matte_preview_when_available(tmp_path) -> None:
    context = _make_visual_context(tmp_path)
    upload = _seed_upload(context, status=UploadStatus.READY)
    context.visual_repository.create_matte(
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        uploaded_video_id=upload.id,
        status=MatteStatus.COMPLETED,
        cache_key="matte-cache",
        source_file_hash="hash",
        segmentation_settings={"enabled": True},
        model_version="matte-v1",
        matte=SegmentationMatte(
            bucket="raw-uploads",
            path="workspaces/matte-preview.mp4",
            fps=30,
            width=320,
            height=180,
            frame_count=30,
            cache_key="matte-cache",
        ),
    )

    status = asyncio.run(
        get_visual_status(
            project_id=context.project_id,
            request=context.request,
            actor=context.actor,
            repository=context.visual_repository,
        )
    )

    assert status.matte_preview.status == "available"
    assert status.matte_preview.preview_url.endswith(
        "/_local/storage/raw-uploads/workspaces/matte-preview.mp4"
    )


def test_supabase_visual_repository_reads_with_explicit_projections(monkeypatch) -> None:
    upload_repository = InMemoryUploadRepository()
    repository = SupabaseVisualRepository(
        upload_repository=upload_repository,
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    upload_id = uuid4()
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: [])

    monkeypatch.setattr("app.repositories.visual.SupabaseRestClient.request", fake_request)

    assert (
        repository.latest_for_upload(
            uploaded_video_id=upload_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        is None
    )
    assert (
        repository.latest_matte_for_upload(
            uploaded_video_id=upload_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        is None
    )

    assert calls[0] == (
        "GET",
        "/video_manifests",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{upload_id}",
                "select": VIDEO_MANIFEST_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            }
        },
    )
    assert calls[1] == (
        "GET",
        "/segmentation_mattes",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{upload_id}",
                "select": SEGMENTATION_MATTE_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            }
        },
    )


def test_supabase_visual_async_reads_use_bounded_projections(monkeypatch) -> None:
    upload_repository = InMemoryUploadRepository()
    repository = SupabaseVisualRepository(
        upload_repository=upload_repository,
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    upload_id = uuid4()
    calls = []

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: [])

    monkeypatch.setattr(
        "app.repositories.visual.SupabaseRestClient.async_request",
        fake_async_request,
    )

    assert (
        asyncio.run(
            repository.latest_for_upload_async(
                uploaded_video_id=upload_id,
                project_id=project_id,
                workspace_id=workspace_id,
            )
        )
        is None
    )
    assert (
        asyncio.run(
            repository.latest_matte_for_upload_async(
                uploaded_video_id=upload_id,
                project_id=project_id,
                workspace_id=workspace_id,
            )
        )
        is None
    )

    assert calls[0] == (
        "GET",
        "/video_manifests",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{upload_id}",
                "select": VIDEO_MANIFEST_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            }
        },
    )
    assert calls[1] == (
        "GET",
        "/segmentation_mattes",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{upload_id}",
                "select": SEGMENTATION_MATTE_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            }
        },
    )


def test_visual_api_prefers_async_repository_methods(tmp_path) -> None:
    context = _make_visual_context(tmp_path)
    upload = _seed_upload(context, status=UploadStatus.READY)

    class AsyncVisualRepository(InMemoryVisualRepository):
        def __init__(self, upload_repository):
            super().__init__(upload_repository)
            self.async_calls: list[str] = []

        async def latest_for_upload_async(self, **kwargs):
            self.async_calls.append("latest-for-upload")
            return self.latest_for_upload(**kwargs)

        async def latest_matte_for_upload_async(self, **kwargs):
            self.async_calls.append("latest-matte-for-upload")
            return self.latest_matte_for_upload(**kwargs)

    repository = AsyncVisualRepository(context.upload_repository)
    context.request.app.state.visual_repository = repository
    repository.create_manifest(
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        uploaded_video_id=upload.id,
        status=VisualManifestStatus.QUEUED,
        provider="fixture",
        model="fixture-visual-v1",
        manifest=None,
        cache_key="visual-cache",
    )
    repository.create_matte(
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        uploaded_video_id=upload.id,
        status=MatteStatus.PENDING,
        cache_key="matte-cache",
        source_file_hash="hash",
        segmentation_settings={"enabled": True},
        model_version="matte-v1",
    )

    status = asyncio.run(
        get_visual_status(
            project_id=context.project_id,
            request=context.request,
            actor=context.actor,
            repository=repository,
        )
    )

    assert status.status == "processing"
    assert repository.async_calls == [
        "latest-for-upload",
        "latest-matte-for-upload",
    ]


def _make_visual_context(tmp_path) -> SimpleNamespace:
    upload_repository = InMemoryUploadRepository()
    visual_repository = InMemoryVisualRepository(upload_repository)
    registry = QueueRegistry()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    settings = SimpleNamespace(
        worker_api_token="worker-secret",
        media_work_dir=str(tmp_path),
        supabase_url=None,
        supabase_service_role_key=None,
        visual_intelligence_provider="fixture",
        visual_sample_fps=2.0,
        visual_scene_threshold=0.42,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                queue_registry=registry,
                visual_repository=visual_repository,
                visual_intelligence_provider=FixtureVisualIntelligenceProvider(),
                storage_signer=StorageSigner(base_url="https://api.motionly.test"),
            )
        )
    )
    return SimpleNamespace(
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        request=request,
        upload_repository=upload_repository,
        visual_repository=visual_repository,
        visual_queue=VisualQueue(registry),
        provider=request.app.state.visual_intelligence_provider,
    )


def _seed_upload(context: SimpleNamespace, *, status: UploadStatus) -> UploadRecord:
    upload = context.upload_repository.create_upload(
        project_id=context.project_id,
        workspace_id=context.workspace_id,
        storage_bucket="raw-uploads",
        storage_path=f"workspaces/{context.workspace_id}/projects/{context.project_id}/raw/video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=100,
    )
    upload.status = status
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
