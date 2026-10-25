from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import Request

from app.api.operations import metrics
from app.core.middleware import _route_label, request_id_middleware
from app.services.operations import MetricsRegistry, OperationsRepository
from app.services.queue import QueueRegistry, QueueSnapshot
from app.services.supabase import SupabaseRestClient


class DummyApp:
    def __init__(self, settings, queue_registry, operations_repository, metrics_registry):
        self.state = SimpleNamespace(
            settings=settings,
            queue_registry=queue_registry,
            operations_repository=operations_repository,
            metrics_registry=metrics_registry,
        )


@pytest.mark.anyio
async def test_metrics_telemetry_rendering() -> None:
    settings = SimpleNamespace(metrics_bearer_token=None, app_env="test")
    queue_registry = QueueRegistry()
    ops_repo = OperationsRepository()
    metrics_reg = MetricsRegistry()

    # Observe some sample metrics
    metrics_reg.observe("job_duration_ms", 120, kind="plan", status="succeeded")
    metrics_reg.observe("job_retry_count", 2, kind="plan")
    metrics_reg.observe("job_failure_count", 1, kind="plan", error_code="ValueError")
    metrics_reg.observe(
        "fallback_degraded_mode_count",
        1,
        provider="provider-a",
        model="provider-a/free",
        reason="edit_plan_fallback",
    )

    # Seed a cost event
    workspace_id = uuid4()
    ops_repo.heartbeat(
        worker_id="worker-1",
        worker_role="render",
        queues=["render"],
        version="test",
        metadata={},
    )
    ops_repo.record_cost(
        workspace_id=workspace_id,
        project_id=uuid4(),
        provider="provider-a",
        category="tokens",
        quantity=5000,
        unit="token",
        unit_cost_usd=0.000002,
    )

    # Render metrics
    request = SimpleNamespace(
        app=DummyApp(
            settings=settings,
            queue_registry=queue_registry,
            operations_repository=ops_repo,
            metrics_registry=metrics_reg,
        )
    )

    output = await metrics(request=request)

    # Assert Promethean formatted metric outputs
    assert 'motionly_job_duration_ms_count{kind="plan",status="succeeded"} 1' in output
    assert 'motionly_job_retry_count_count{kind="plan"} 1' in output
    assert 'motionly_job_failure_count_count{error_code="ValueError",kind="plan"} 1' in output
    assert (
        "motionly_fallback_degraded_mode_count_count"
        '{model="provider-a/free",provider="provider-a",reason="edit_plan_fallback"} 1' in output
    )
    assert (
        f'motionly_cost_usd{{workspace_id="{workspace_id}",provider="provider-a"}} 0.010000'
        in output
    )
    assert 'motionly_queue_snapshot_duration_ms_count{backend="in_memory"} 1' in output
    assert 'motionly_worker_heartbeat_age_seconds{worker_id="worker-1",role="render"}' in output


@pytest.mark.anyio
async def test_request_middleware_records_event_loop_lag_metric() -> None:
    settings = SimpleNamespace(app_env="test")
    metrics_reg = MetricsRegistry()
    request = SimpleNamespace(
        headers={},
        method="GET",
        url=SimpleNamespace(path="/health/live"),
        scope={"route": SimpleNamespace(path="/health/live")},
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(settings=settings, metrics_registry=metrics_reg)),
    )

    async def call_next(_request):
        return SimpleNamespace(status_code=200, headers={})

    await request_id_middleware(request, call_next)
    output = metrics_reg.render()

    assert 'motionly_event_loop_lag_ms_count{route="/health/live"} 1' in output
    assert (
        "motionly_http_request_duration_ms_count"
        '{method="GET",route="/health/live",status="200"} 1' in output
    )


@pytest.mark.anyio
async def test_request_middleware_logs_slow_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(app_env="test")
    metrics_reg = MetricsRegistry()
    request = SimpleNamespace(
        headers={"x-motionly-workspace-id": "workspace-123"},
        method="GET",
        url=SimpleNamespace(path="/projects"),
        scope={"route": SimpleNamespace(path="/projects")},
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(settings=settings, metrics_registry=metrics_reg)),
    )
    perf_values = iter([0.0, 0.001, 0.001, 1.251])
    warnings: list[tuple[str, dict]] = []

    class FakeLogger:
        def warning(self, message, *, extra):
            warnings.append((message, extra))

        def info(self, message, *, extra):
            pass

    async def call_next(_request):
        from app.services.request_counters import (
            increment_queue_calls,
            increment_supabase_calls,
        )

        increment_supabase_calls()
        increment_supabase_calls()
        increment_queue_calls()
        return SimpleNamespace(status_code=200, headers={"content-length": "42"})

    monkeypatch.setattr("app.core.middleware.perf_counter", lambda: next(perf_values))
    monkeypatch.setattr("app.core.middleware.logger", FakeLogger())

    await request_id_middleware(request, call_next)

    assert warnings
    message, extra = warnings[0]
    assert message == "api_slow_request"
    assert extra["route"] == "/projects"
    assert extra["latency_ms"] == 1250
    assert extra["response_size"] == "42"
    assert extra["workspace_hash"] is not None
    assert len(extra["workspace_hash"]) == 12
    assert extra["supabase_call_count"] == 2
    assert extra["queue_call_count"] == 1


@pytest.mark.anyio
async def test_metrics_records_queue_snapshot_errors() -> None:
    settings = SimpleNamespace(metrics_bearer_token=None, app_env="test")
    ops_repo = OperationsRepository()
    metrics_reg = MetricsRegistry()

    class BrokenQueueRegistry:
        def snapshots(self):
            raise RuntimeError("redis unavailable")

    request = SimpleNamespace(
        app=DummyApp(
            settings=settings,
            queue_registry=BrokenQueueRegistry(),
            operations_repository=ops_repo,
            metrics_registry=metrics_reg,
        )
    )

    output = await metrics(request=request)

    assert (
        "motionly_queue_snapshot_error_count_count"
        '{backend="in_memory",error="RuntimeError"} 1' in output
    )


@pytest.mark.anyio
async def test_metrics_records_worker_heartbeat_errors() -> None:
    settings = SimpleNamespace(metrics_bearer_token=None, app_env="test")
    metrics_reg = MetricsRegistry()

    class BrokenOperationsRepository:
        cost_events = []

        def latest_heartbeats(self):
            raise RuntimeError("supabase unavailable")

    request = SimpleNamespace(
        app=DummyApp(
            settings=settings,
            queue_registry=QueueRegistry(),
            operations_repository=BrokenOperationsRepository(),
            metrics_registry=metrics_reg,
        )
    )

    output = await metrics(request=request)

    assert 'motionly_worker_heartbeat_error_count{error="RuntimeError"} 1' in output


@pytest.mark.anyio
async def test_metrics_exports_queue_snapshot_values() -> None:
    settings = SimpleNamespace(metrics_bearer_token=None, app_env="test")
    ops_repo = OperationsRepository()
    metrics_reg = MetricsRegistry()

    class SnapshotQueueRegistry:
        def snapshots(self):
            return [
                QueueSnapshot(
                    queue_name="render",
                    queued=2,
                    running=1,
                    succeeded=0,
                    failed=0,
                    dead_letter=1,
                    canceled=0,
                    oldest_queued_seconds=45,
                )
            ]

    request = SimpleNamespace(
        app=DummyApp(
            settings=settings,
            queue_registry=SnapshotQueueRegistry(),
            operations_repository=ops_repo,
            metrics_registry=metrics_reg,
        )
    )

    output = await metrics(request=request)

    assert 'motionly_queue_queued{queue="render"} 2' in output
    assert 'motionly_queue_running{queue="render"} 1' in output
    assert 'motionly_queue_dead_letter{queue="render"} 1' in output
    assert 'motionly_queue_oldest_seconds{queue="render"} 45' in output
    assert 'motionly_queue_slo_breach{queue="render",type="queued"} 0' in output
    assert 'motionly_queue_slo_breach{queue="render",type="oldest_seconds"} 0' in output
    assert 'motionly_queue_backpressure{queue="render"} 0' in output


@pytest.mark.anyio
async def test_metrics_exports_queue_slo_breaches_and_backpressure() -> None:
    settings = SimpleNamespace(metrics_bearer_token=None, app_env="test")
    ops_repo = OperationsRepository()
    metrics_reg = MetricsRegistry()

    class SnapshotQueueRegistry:
        def snapshots(self):
            return [
                QueueSnapshot(
                    queue_name="render_gpu",
                    queued=600,
                    running=0,
                    succeeded=0,
                    failed=0,
                    dead_letter=0,
                    canceled=0,
                    oldest_queued_seconds=2_000,
                )
            ]

    request = SimpleNamespace(
        app=DummyApp(
            settings=settings,
            queue_registry=SnapshotQueueRegistry(),
            operations_repository=ops_repo,
            metrics_registry=metrics_reg,
        )
    )

    output = await metrics(request=request)

    assert 'motionly_queue_slo_breach{queue="render_gpu",type="queued"} 1' in output
    assert 'motionly_queue_slo_breach{queue="render_gpu",type="oldest_seconds"} 1' in output
    assert 'motionly_queue_backpressure{queue="render_gpu"} 1' in output


def test_route_metric_label_uses_fastapi_template() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/projects/123/renders/456",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
            "route": SimpleNamespace(path="/projects/{project_id}/renders/{render_id}"),
        }
    )

    assert _route_label(request) == "/projects/{project_id}/renders/{render_id}"


def test_supabase_rest_client_records_request_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = MetricsRegistry()

    def fake_request(*_args, **_kwargs):
        return httpx.Response(200, json=[{"ok": True}])

    monkeypatch.setattr("app.services.supabase.httpx.request", fake_request)
    client = SupabaseRestClient(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        repository="projects",
        metrics_registry=registry,
    )

    response = client.request("GET", "/projects", params={"select": "id"})

    assert response.status_code == 200
    rendered = registry.render()
    labels = 'method="GET",repository="projects",status="200",table="projects"'
    assert f"motionly_supabase_request_duration_ms_count{{{labels}}} 1" in rendered
    assert f"motionly_supabase_request_count_sum{{{labels}}} 1.000000" in rendered


@pytest.mark.anyio
async def test_supabase_rest_client_async_request_records_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = MetricsRegistry()
    calls: list[dict[str, object]] = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, **kwargs):
            calls.append({"method": method, "url": url, "kwargs": kwargs, "timeout": self.timeout})
            return httpx.Response(200, json=[{"ok": True}])

    monkeypatch.setattr("app.services.supabase.httpx.AsyncClient", FakeAsyncClient)
    client = SupabaseRestClient(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        repository="projects",
        metrics_registry=registry,
    )

    response = await client.async_request("GET", "/projects", params={"select": "id"})

    assert response.status_code == 200
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == "https://supabase.example.com/rest/v1/projects"
    rendered = registry.render()
    labels = 'method="GET",repository="projects",status="200",table="projects"'
    assert f"motionly_supabase_request_duration_ms_count{{{labels}}} 1" in rendered
    assert f"motionly_supabase_request_count_sum{{{labels}}} 1.000000" in rendered


def test_metrics_registry_summary_reports_admin_dashboard_signals() -> None:
    registry = MetricsRegistry()

    for value in [50, 100, 200, 400, 800]:
        registry.observe(
            "http_request_duration_ms",
            value,
            method="GET",
            route="/projects",
            status="200",
        )
    registry.observe(
        "http_request_duration_ms",
        1_200,
        method="GET",
        route="/renders",
        status="503",
    )
    registry.observe(
        "supabase_request_duration_ms",
        1_500,
        method="GET",
        repository="projects",
        table="projects",
        status="200",
    )
    registry.observe("render_latency_ms", 2_000, engine="ffmpeg")
    registry.observe("export_latency_ms", 3_000, format="mp4")

    summary = registry.summary()

    assert summary["api"]["p95_ms"] == 1_200
    assert summary["api"]["p99_ms"] == 1_200
    assert summary["api"]["route_errors_5xx"] == 1
    assert summary["api"]["route_errors_by_route"] == {"/renders": 1}
    assert summary["supabase"]["p95_ms"] == 1_500
    assert summary["supabase"]["slow_call_count"] == 1
    assert summary["render"]["p95_latency_ms"] == 2_000
    assert summary["export"]["p95_latency_ms"] == 3_000


def test_supabase_rest_client_retries_and_counts_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = MetricsRegistry()
    responses = [httpx.Response(503, json={"message": "unavailable"}), httpx.Response(200, json=[])]

    def fake_request(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr("app.services.supabase.httpx.request", fake_request)
    client = SupabaseRestClient(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        repository="uploads",
        metrics_registry=registry,
        max_retries=1,
    )

    response = client.request("PATCH", "/uploaded_videos", json={"status": "uploaded"})

    assert response.status_code == 200
    rendered = registry.render()
    error_labels = (
        'error="http_status",method="PATCH",repository="uploads",'
        'status="503",table="uploaded_videos"'
    )
    assert f"motionly_supabase_error_count_count{{{error_labels}}} 1" in rendered
    assert responses == []


def test_supabase_rest_client_does_not_retry_unsafe_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"message": "ambiguous failure"})

    monkeypatch.setattr("app.services.supabase.httpx.request", fake_request)
    client = SupabaseRestClient(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        repository="projects",
        max_retries=3,
    )

    response = client.request("POST", "/projects", json={"title": "One write"})

    assert response.status_code == 503
    assert calls == 1


@pytest.mark.anyio
async def test_supabase_rest_client_async_does_not_retry_unsafe_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return httpx.Response(503, json={"message": "ambiguous failure"})

    monkeypatch.setattr("app.services.supabase.httpx.AsyncClient", FakeAsyncClient)
    client = SupabaseRestClient(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        repository="projects",
        max_retries=3,
    )

    response = await client.async_request("POST", "/projects", json={"title": "One write"})

    assert response.status_code == 503
    assert calls == 1
