import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.admin import (
    cancel_queue_job,
    dead_letter_stuck_queue_jobs,
    inspect_queue_job,
    ops_overview,
    require_admin,
    retry_render_job,
)
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.schemas.admin import QueueDeadLetterRequest
from app.schemas.renders import RenderJobStatus
from app.services.operations import MetricsRegistry, OperationsRepository
from app.services.queue import QueueJobStatus, QueueRegistry, RenderQueue

from .test_renders import make_render_repository


def make_admin_request(workspace_id):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env="test",
                    gpu_stage="enabled",
                    admin_api_token=None,
                    admin_user_id_list=set(),
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


def test_admin_overview_reports_provider_slo_and_cost_panels() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    request = make_admin_request(workspace_id)
    operations = OperationsRepository()
    request.app.state.operations_repository = operations
    operations.record_provider_slo(
        workspace_id=workspace_id,
        project_id=project_id,
        provider="openrouter",
        operation="edit_plan",
        success=False,
        latency_ms=2500,
        error_code="rate_limit_exceeded",
    )
    operations.record_cost(
        workspace_id=workspace_id,
        project_id=project_id,
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
            status=RenderJobStatus.COMPLETE,
            planner_version="test",
            manifest={},
            keep_segments=[],
        ).id,
        provider="openrouter",
        category="tokens",
        quantity=1000,
        unit="token",
        unit_cost_usd=0.000002,
    )

    response = asyncio.run(
        ops_overview(
            request=request,
            _actor=Actor(user_id=user_id, workspace_id=workspace_id),
            render_repository=repository,
        )
    )

    assert response.provider_slos[0]["provider"] == "openrouter"
    assert response.provider_slos[0]["operation"] == "edit_plan"
    assert response.provider_slos[0]["errors"] == 1
    assert response.provider_slos[0]["p95_latency_ms"] == 2500
    assert response.provider_costs["by_provider"]["openrouter"] == 0.002
    assert response.provider_costs["by_category"]["tokens"] == 0.002


def test_admin_overview_reports_operations_dashboard_signals() -> None:
    repository, _project_id, workspace_id, user_id = make_render_repository()
    request = make_admin_request(workspace_id)
    operations = OperationsRepository()
    metrics = MetricsRegistry()
    request.app.state.operations_repository = operations
    request.app.state.metrics_registry = metrics
    request.app.state.settings.worker_heartbeat_max_age_seconds = 60
    request.app.state.queue_registry.enqueue(
        job_id="visual-1",
        kind="visual_analysis",
        queue_name="visual_analysis",
        workspace_id=workspace_id,
    )
    request.app.state.queue_registry.enqueue(
        job_id="matte-1",
        kind="matte_extraction",
        queue_name="matte_extraction",
        workspace_id=workspace_id,
    )
    operations.heartbeat(
        worker_id="fresh-worker",
        worker_role="visual",
        queues=["visual_analysis"],
        version="test",
        metadata={},
        last_seen_at=datetime.now(UTC),
    )
    operations.heartbeat(
        worker_id="stale-worker",
        worker_role="render",
        queues=["render_gpu"],
        version="test",
        metadata={},
        last_seen_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    metrics.observe(
        "http_request_duration_ms",
        120,
        method="GET",
        route="/projects",
        status="200",
    )
    metrics.observe(
        "http_request_duration_ms",
        1_500,
        method="POST",
        route="/renders",
        status="500",
    )
    metrics.observe(
        "supabase_request_duration_ms",
        1_250,
        method="GET",
        repository="renders",
        table="render_jobs",
        status="200",
    )

    response = asyncio.run(
        ops_overview(
            request=request,
            _actor=Actor(user_id=user_id, workspace_id=workspace_id),
            render_repository=repository,
        )
    )

    dashboard = response.operations_dashboard
    assert dashboard["api_p95_ms"] == 1_500
    assert dashboard["api_p99_ms"] == 1_500
    assert dashboard["route_errors_5xx"] == 1
    assert dashboard["slow_supabase_calls"] == 1
    assert dashboard["gpu_backlog"] == 2
    assert dashboard["fresh_worker_count"] == 1
    assert dashboard["stale_worker_count"] == 1
    assert dashboard["max_queue_age_seconds"] is not None


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


def test_queue_inspect_action_returns_job_diagnostics() -> None:
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
            render_engine="remotion_premium",
        ).id,
        project_id=project_id,
        workspace_id=workspace_id,
        render_engine="remotion_premium",
    )

    response = asyncio.run(
        inspect_queue_job(
            queue_job_id=job.id,
            request=request,
            _actor=Actor(user_id=user_id, workspace_id=workspace_id),
        )
    )

    assert response.job.id == job.id
    assert response.job.queue_name == "render_remotion"
    assert response.job.render_engine == "remotion_premium"


def test_queue_dead_letter_triage_marks_stuck_jobs() -> None:
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
    request.app.state.queue_registry.jobs[job.id] = replace(
        job,
        status=QueueJobStatus.RUNNING,
        enqueued_at=datetime.now(UTC) - timedelta(seconds=5),
    )

    response = asyncio.run(
        dead_letter_stuck_queue_jobs(
            payload=QueueDeadLetterRequest(timeout_seconds=1),
            request=request,
            _actor=Actor(user_id=user_id, workspace_id=workspace_id),
        )
    )

    assert response.jobs[0].id == job.id
    assert response.jobs[0].status == "dead_letter"


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


def test_admin_access_accepts_explicitly_allowed_user() -> None:
    _repository, _project_id, workspace_id, user_id = make_render_repository()
    request = make_admin_request(workspace_id)
    request.app.state.settings.admin_workspace_id_list = set()
    request.app.state.settings.admin_user_id_list = {str(user_id)}
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    assert (
        asyncio.run(
            require_admin(
                request=request,
                actor=actor,
                x_motionly_admin_token=None,
            )
        )
        == actor
    )


def test_admin_access_rejects_non_admin_with_different_allowlisted_identity() -> None:
    _repository, _project_id, workspace_id, user_id = make_render_repository()
    request = make_admin_request(workspace_id)
    request.app.state.settings.admin_workspace_id_list = {"00000000-0000-4000-8000-000000000098"}
    request.app.state.settings.admin_user_id_list = {"00000000-0000-4000-8000-000000000099"}

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            require_admin(
                request=request,
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                x_motionly_admin_token=None,
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "admin_access_denied"


def test_every_admin_route_requires_the_admin_authorization_dependency(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    try:
        routes = []
        for route in app.routes:
            if hasattr(route, "path") and route.path.startswith("/admin"):
                routes.append(route)
            elif hasattr(route, "original_router"):
                for sub in route.original_router.routes:
                    if hasattr(sub, "path") and sub.path.startswith("/admin"):
                        routes.append(sub)
        assert routes
        for route in routes:
            dependencies = {dependency.call for dependency in route.dependant.dependencies}
            assert require_admin in dependencies, f"{route.path} was not protected"
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("owner", True), ("admin", True), ("member", False)],
)
def test_admin_workspace_allowlist_admits_only_its_owners_and_admins(role, allowed) -> None:
    _repository, _project_id, workspace_id, user_id = make_render_repository()
    request = make_admin_request(workspace_id)
    actor = Actor(user_id=user_id, workspace_id=workspace_id, role=role)

    if allowed:
        assert (
            asyncio.run(require_admin(request=request, actor=actor, x_motionly_admin_token=None))
            == actor
        )
        return
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(require_admin(request=request, actor=actor, x_motionly_admin_token=None))
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "admin_access_denied"


def test_api_keys_are_never_platform_admins() -> None:
    _repository, _project_id, workspace_id, user_id = make_render_repository()
    request = make_admin_request(workspace_id)
    request.app.state.settings.admin_user_id_list = {str(user_id)}
    actor = Actor(user_id=user_id, workspace_id=workspace_id, role="owner", kind="api_key")

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(require_admin(request=request, actor=actor, x_motionly_admin_token=None))
    assert exc_info.value.code == "admin_access_denied"


def test_allowlisted_user_is_admin_whatever_their_workspace_role() -> None:
    _repository, _project_id, workspace_id, user_id = make_render_repository()
    request = make_admin_request(workspace_id)
    request.app.state.settings.admin_workspace_id_list = set()
    request.app.state.settings.admin_user_id_list = {str(user_id)}
    actor = Actor(user_id=user_id, workspace_id=workspace_id, role="member")

    assert (
        asyncio.run(require_admin(request=request, actor=actor, x_motionly_admin_token=None))
        == actor
    )
