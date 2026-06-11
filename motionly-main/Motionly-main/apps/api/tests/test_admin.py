import asyncio
from types import SimpleNamespace

import pytest
from test_renders import make_render_repository

from app.api.admin import cancel_queue_job, ops_overview, require_admin, retry_render_job
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.schemas.renders import RenderJobStatus
from app.services.queue import QueueRegistry, RenderQueue


def make_admin_request(workspace_id):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env="test",
                    admin_api_token=None,
                    admin_workspace_id_list={str(workspace_id)},
                ),
                queue_registry=QueueRegistry(),
            ),
        ),
        state=SimpleNamespace(request_id="req_admin"),
    )


def test_admin_overview_reports_queue_and_render_metrics() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_admin_request(workspace_id)
    queue = RenderQueue(request.app.state.queue_registry)
    queue.enqueue_render(
        render_job_id=repository.create_render_job(
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
        ).id,
        project_id=project_id,
        workspace_id=workspace_id,
        priority=10,
    )

    response = asyncio.run(
        ops_overview(
            request=request,
            _actor=Actor(user_id=user_id, workspace_id=workspace_id),
            render_repository=repository,
        )
    )

    assert response.environment == "test"
    assert response.queues[0].queue_name == "render"
    assert response.queues[0].queued == 1
    assert response.launch_metrics["recent_render_count"] == 1


def test_queue_cancel_action_updates_registry_job() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_admin_request(workspace_id)
    queue = RenderQueue(request.app.state.queue_registry)
    job = queue.enqueue_render(
        render_job_id=repository.create_render_job(
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
        ).id,
        project_id=project_id,
        workspace_id=workspace_id,
    )

    response = asyncio.run(
        cancel_queue_job(
            queue_job_id=job.id,
            request=request,
            _actor=Actor(user_id=user_id, workspace_id=workspace_id),
        )
    )

    assert response.job.status == "canceled"


def test_admin_can_retry_canceled_render() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
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
        status=RenderJobStatus.CANCELED,
        planner_version="test",
        manifest={},
        keep_segments=[],
        progress_pct=5,
    )

    response = asyncio.run(
        retry_render_job(
            render_job_id=render.id,
            _actor=Actor(user_id=user_id, workspace_id=workspace_id),
            render_repository=repository,
        )
    )

    assert response.job.status == "queued"
    assert response.job.events[-1].event_type == "admin_retry"


def test_admin_access_requires_allowed_workspace_or_token() -> None:
    repository, _project_id, workspace_id, user_id = make_render_repository()
    request = make_admin_request(workspace_id)
    request.app.state.settings.admin_workspace_id_list = set()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            require_admin(
                request=request,
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                x_motionly_admin_token=None,
            )
        )

    assert repository
    assert exc_info.value.status_code == 403
