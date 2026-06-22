import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.internal_jobs import run_queue_job
from app.api.internal_renders import fail_render_job, update_render_progress
from app.api.renders import create_render
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.renders import RenderCreateRequest, RenderJobStatus
from app.schemas.uploads import UploadStatus, VideoMetadata
from app.services.local_storage import LocalObjectStorage
from app.services.queue import PreprocessQueue, QueueRegistry, RenderQueue
from tests.test_renders import make_render_repository


def test_production_api_refuses_media_job_execution() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(app_env="production", admin_api_token="worker-secret"),
                queue_registry=QueueRegistry(),
            )
        )
    )
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            run_queue_job(
                queue_job_id="any-job",
                request=request,
                x_motionly_worker_token="worker-secret",
            )
        )
    assert exc_info.value.status_code == 410
    assert exc_info.value.code == "direct_worker_execution_required"


def test_worker_runs_upload_preprocessing_job(tmp_path, monkeypatch) -> None:
    repository = InMemoryUploadRepository()
    registry = QueueRegistry()
    project_id = uuid4()
    workspace_id = uuid4()
    repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="workspaces/test/projects/test/raw/video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=100,
    )
    upload.status = UploadStatus.PROCESSING
    LocalObjectStorage(root=tmp_path).write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"fake mp4",
    )

    class FakePreprocessor:
        def __init__(self, **_kwargs):
            pass

        def process(self, **kwargs):
            return (
                VideoMetadata(
                    duration_ms=3000,
                    width=1080,
                    height=1920,
                    has_audio=True,
                    orientation="vertical",
                ),
                kwargs["thumbnail_path"],
            )

    monkeypatch.setattr("app.services.upload_jobs.VideoPreprocessor", FakePreprocessor)
    queue_job = PreprocessQueue(registry).enqueue_video_preprocess(
        video_id=upload.id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    admin_api_token="worker-secret",
                    media_work_dir=str(tmp_path),
                    supabase_url=None,
                    supabase_service_role_key=None,
                ),
                queue_registry=registry,
                upload_repository=repository,
            ),
        ),
    )

    response = asyncio.run(
        run_queue_job(
            queue_job_id=queue_job.id,
            request=request,
            x_motionly_worker_token="worker-secret",
        )
    )

    processed = repository.get_upload_for_project(
        project_id=project_id,
        video_id=upload.id,
        workspace_id=workspace_id,
    )
    assert response.status == "succeeded"
    assert processed.status == "ready"
    assert processed.metadata.duration_ms == 3000
    assert processed.thumbnail_path == (
        f"workspaces/{workspace_id}/projects/{project_id}/thumbnails/{upload.id}.jpg"
    )


def test_worker_runs_render_job_to_export() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    registry = QueueRegistry()
    settings = SimpleNamespace(
        admin_api_token="worker-secret",
        export_storage_bucket="exports",
        export_signed_url_ttl_seconds=300,
        should_complete_renders_synchronously=False,
        render_execute_ffmpeg=False,
        media_work_dir=".motionly/test-storage",
        billing_enabled=False,
        supabase_url=None,
        supabase_service_role_key=None,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                queue_registry=registry,
                render_repository=repository,
            ),
        ),
    )
    render_response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(registry),
        )
    )

    worker_response = asyncio.run(
        run_queue_job(
            queue_job_id=render_response.queue_job_id,
            request=request,
            x_motionly_worker_token="worker-secret",
        )
    )

    job = repository.get_render_job(
        project_id=project_id,
        render_job_id=render_response.job_id,
        workspace_id=workspace_id,
    )
    assert worker_response.status == "succeeded"
    assert job.status == RenderJobStatus.COMPLETE
    assert repository.list_exports(project_id=project_id, workspace_id=workspace_id)


def test_worker_progress_callback_updates_render_job() -> None:
    repository, project_id, workspace_id, _user_id = make_render_repository()
    render = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        caption_track_id=repository.latest_caption_track(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        status=RenderJobStatus.QUEUED,
        planner_version="test",
        manifest={},
        keep_segments=[],
        progress_pct=5,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(admin_api_token="worker-secret"),
                render_repository=repository,
            ),
        ),
    )

    response = asyncio.run(
        update_render_progress(
            render_job_id=render.id,
            payload=SimpleNamespace(
                workspace_id=workspace_id,
                project_id=project_id,
                status=RenderJobStatus.RENDERING,
                progress_pct=45,
                message="Rendering premium video composition.",
                event_type="remotion_rendering",
                metadata={"phase": "render"},
            ),
            request=request,
            x_motionly_worker_token="worker-secret",
        )
    )

    assert response.status == RenderJobStatus.RENDERING
    assert response.progress_pct == 45
    assert repository.events_for_job(render.id)[-1].event_type == "remotion_rendering"


def test_worker_failure_callback_marks_terminal_failure() -> None:
    repository, project_id, workspace_id, _user_id = make_render_repository()
    render = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        caption_track_id=repository.latest_caption_track(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        status=RenderJobStatus.RENDERING,
        planner_version="test",
        manifest={},
        keep_segments=[],
        progress_pct=45,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(admin_api_token="worker-secret"),
                render_repository=repository,
            ),
        ),
    )

    response = asyncio.run(
        fail_render_job(
            render_job_id=render.id,
            payload=SimpleNamespace(
                workspace_id=workspace_id,
                project_id=project_id,
                error_code="render_worker_failed",
                error_message="render-cli.ts exited with code 1",
                retrying=False,
                metadata={"attempts": 3},
            ),
            request=request,
            x_motionly_worker_token="worker-secret",
        )
    )

    assert response.status == RenderJobStatus.FAILED
    assert render.error_code == "render_worker_failed"
    assert "render-cli.ts exited with code 1" in render.logs[-1]
