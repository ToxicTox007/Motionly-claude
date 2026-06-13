import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.internal_jobs import run_queue_job
from app.api.visual import analyze_visual_manifest, get_visual_manifest
from app.repositories.uploads import InMemoryUploadRepository, UploadRecord
from app.repositories.visual import InMemoryVisualRepository
from app.schemas.uploads import UploadStatus, VideoMetadata
from app.schemas.visual import (
    CameraMotion,
    SceneType,
    VisualAnalyzeRequest,
    VisualManifestStatus,
)
from app.services.local_storage import LocalObjectStorage
from app.services.queue import QueueRegistry, VisualQueue
from app.services.visual_intelligence import FixtureVisualIntelligenceProvider


def test_visual_analysis_builds_manifest_and_reuses_cached_result(tmp_path) -> None:
    context = _make_visual_context(tmp_path)
    upload = _seed_upload(context, status=UploadStatus.READY)

    first = asyncio.run(
        analyze_visual_manifest(
            project_id=context.project_id,
            payload=VisualAnalyzeRequest(),
            request=context.request,
            actor=context.actor,
            repository=context.visual_repository,
            visual_queue=context.visual_queue,
            provider=context.provider,
        )
    )

    assert first.cached is False
    assert first.job_id == f"visual--{context.project_id}--{upload.id}--{context.workspace_id}"
    assert first.manifest.status == VisualManifestStatus.READY
    assert first.manifest.uploaded_video_id == upload.id
    assert first.manifest.manifest is not None

    manifest = first.manifest.manifest
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

    second = asyncio.run(
        analyze_visual_manifest(
            project_id=context.project_id,
            payload=VisualAnalyzeRequest(),
            request=context.request,
            actor=context.actor,
            repository=context.visual_repository,
            visual_queue=context.visual_queue,
            provider=context.provider,
        )
    )

    assert second.cached is True
    assert second.manifest.id == first.manifest.id
    assert second.manifest.version == first.manifest.version

    fetched = asyncio.run(
        get_visual_manifest(
            project_id=context.project_id,
            actor=context.actor,
            repository=context.visual_repository,
        )
    )
    assert fetched.id == first.manifest.id
    assert fetched.manifest is not None
    assert fetched.manifest.summary["top_objects"]


def test_visual_analysis_force_creates_new_manifest_version(tmp_path) -> None:
    context = _make_visual_context(tmp_path)
    _seed_upload(context, status=UploadStatus.READY)

    first = asyncio.run(
        analyze_visual_manifest(
            project_id=context.project_id,
            payload=VisualAnalyzeRequest(),
            request=context.request,
            actor=context.actor,
            repository=context.visual_repository,
            visual_queue=context.visual_queue,
            provider=context.provider,
        )
    )
    forced = asyncio.run(
        analyze_visual_manifest(
            project_id=context.project_id,
            payload=VisualAnalyzeRequest(force=True, sample_rate_fps=1.0, scene_threshold=0.25),
            request=context.request,
            actor=context.actor,
            repository=context.visual_repository,
            visual_queue=context.visual_queue,
            provider=context.provider,
        )
    )

    assert forced.cached is False
    assert forced.manifest.id != first.manifest.id
    assert forced.manifest.version == first.manifest.version + 1
    assert forced.manifest.manifest is not None
    assert forced.manifest.manifest.sample_rate_fps == 1.0


def test_visual_analysis_rejects_upload_that_is_not_ready(tmp_path) -> None:
    context = _make_visual_context(tmp_path)
    _seed_upload(context, status=UploadStatus.PROCESSING)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            analyze_visual_manifest(
                project_id=context.project_id,
                payload=VisualAnalyzeRequest(),
                request=context.request,
                actor=context.actor,
                repository=context.visual_repository,
                visual_queue=context.visual_queue,
                provider=context.provider,
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "source_video_not_ready"


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


def _make_visual_context(tmp_path) -> SimpleNamespace:
    upload_repository = InMemoryUploadRepository()
    visual_repository = InMemoryVisualRepository(upload_repository)
    registry = QueueRegistry()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    settings = SimpleNamespace(
        admin_api_token="worker-secret",
        media_work_dir=str(tmp_path),
        supabase_url=None,
        supabase_service_role_key=None,
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
