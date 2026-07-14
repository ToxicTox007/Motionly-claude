import asyncio
import tracemalloc
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.api.dependencies import Actor
from app.api.internal_batches import run_batch
from app.api.scale import cancel_batch, create_batch
from app.repositories.scale import InMemoryScaleRepository
from app.schemas.scale import BatchCreateRequest, ScaleResourceStatus
from app.services.local_storage import LocalObjectStorage, SupabaseBackedObjectStorage
from app.services.operations import (
    COST_ATTRIBUTION_SUMMARY_COLUMNS,
    WORKER_HEARTBEAT_COLUMNS,
    MetricsRegistry,
    OperationsRepository,
)
from app.services.queue import QueueRegistry, ScaleQueue
from app.services.tracing import current_trace_id


def test_512_mib_upload_is_streamed_with_bounded_python_memory(monkeypatch, tmp_path) -> None:
    source = tmp_path / "large.mp4"
    with source.open("wb") as file:
        file.truncate(512 * 1024 * 1024)
    observed = {"bytes": 0, "chunks": 0}

    def fake_post(_url, **kwargs):
        for chunk in kwargs["content"]:
            observed["bytes"] += len(chunk)
            observed["chunks"] += 1
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("app.services.local_storage.httpx.post", fake_post)
    storage = SupabaseBackedObjectStorage(
        root=tmp_path / "cache",
        supabase_url="https://storage.test",
        service_role_key="secret",
    )
    tracemalloc.start()
    storage.upload_file(bucket="exports", path="large.mp4", source_path=source)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert observed == {"bytes": 512 * 1024 * 1024, "chunks": 512}
    assert peak < 8 * 1024 * 1024


def test_cache_eviction_enforces_age_and_size_bounds(tmp_path) -> None:
    storage = LocalObjectStorage(root=tmp_path)
    oldest = storage.write_object(bucket="raw", path="old.mp4", content=b"a" * 6)
    newest = storage.write_object(bucket="raw", path="new.mp4", content=b"b" * 6)
    oldest.touch()
    newest.touch()
    oldest_time = oldest.stat().st_mtime - 100
    import os

    os.utime(oldest, (oldest_time, oldest_time))

    result = storage.evict_cache(max_bytes=6, max_age_seconds=10_000)

    assert result == {"removed_files": 1, "removed_bytes": 6}
    assert not oldest.exists()
    assert newest.exists()


def test_batch_creation_is_queued_progressive_and_cancelable() -> None:
    workspace_id, user_id = uuid4(), uuid4()
    project_ids = [uuid4(), uuid4()]
    scale = InMemoryScaleRepository()
    registry = QueueRegistry()
    state = SimpleNamespace(
        project_repository=SimpleNamespace(get_project=lambda **_kwargs: object()),
        scale_repository=scale,
        scale_queue=ScaleQueue(registry),
        queue_registry=registry,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    created = asyncio.run(
        create_batch(
            payload=BatchCreateRequest(project_ids=project_ids),
            actor=actor,
            request=request,
        )
    )

    assert created.status == ScaleResourceStatus.QUEUED
    assert [item["status"] for item in created.result["items"]] == ["queued", "queued"]
    job = registry.jobs[f"phase5--{created.id}"]
    assert job.kind == "batch_plan"
    assert job.queue_name == "planning"

    canceled = asyncio.run(cancel_batch(created.id, actor, request))
    assert canceled.status == ScaleResourceStatus.CANCELED
    assert registry.jobs[job.id].status.value == "canceled"


def test_batch_worker_persists_item_progress(monkeypatch) -> None:
    workspace_id, user_id = uuid4(), uuid4()
    project_ids = [uuid4(), uuid4()]
    scale = InMemoryScaleRepository()
    batch = scale.create(
        workspace_id=workspace_id,
        project_id=project_ids[0],
        kind="batch",
        name="Batch",
        status=ScaleResourceStatus.QUEUED,
        payload={
            **BatchCreateRequest(project_ids=project_ids).model_dump(mode="json"),
            "creator_user_id": str(user_id),
        },
        result={
            "items": [
                {"project_id": str(project_id), "status": "queued"}
                for project_id in project_ids
            ]
        },
    )

    async def fake_create_plan(*, project_id, **_kwargs):
        return SimpleNamespace(plan=SimpleNamespace(id=project_id))

    monkeypatch.setattr("app.api.internal_batches.create_plan", fake_create_plan)
    state = SimpleNamespace(
        settings=SimpleNamespace(worker_api_token="worker-secret"),
        scale_repository=scale,
        plan_repository=object(),
        edit_planner_provider=object(),
        plan_queue=object(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    result = asyncio.run(
        run_batch(
            workspace_id=workspace_id,
            batch_id=batch.id,
            request=request,
            x_motionly_worker_token="worker-secret",
        )
    )

    assert result["status"] == "completed"
    assert result["completed"] == 2
    assert scale.get(resource_id=batch.id, workspace_id=workspace_id).revision == 3


def test_cost_summary_tracks_cost_per_approved_export() -> None:
    repository = OperationsRepository()
    workspace_id, project_id, render_id, export_id = uuid4(), uuid4(), uuid4(), uuid4()
    repository.record_cost(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=render_id,
        provider="renderer",
        category="compute_gpu",
        quantity=2,
        unit="second",
        unit_cost_usd=0.25,
        metadata={},
    )
    repository.approve_export(
        workspace_id=workspace_id, render_job_id=render_id, export_id=export_id
    )

    assert repository.cost_summary()["cost_per_approved_export_usd"] == 0.5


def test_metrics_heartbeats_and_trace_context_are_operational() -> None:
    metrics = MetricsRegistry()
    metrics.observe("provider_latency_ms", 125, provider="openrouter", success="true")
    rendered = metrics.render()
    assert 'provider="openrouter"' in rendered
    assert "motionly_provider_latency_ms_sum" in rendered

    operations = OperationsRepository()
    heartbeat = operations.heartbeat(
        worker_id="worker-1",
        worker_role="provider",
        queues=["provider_calls"],
        version="test",
        metadata={},
    )
    assert operations.latest_heartbeats() == [heartbeat]

    token = current_trace_id.set("a" * 32)
    try:
        job = QueueRegistry().enqueue(
            job_id="trace-job", kind="test", queue_name="planning"
        )
    finally:
        current_trace_id.reset(token)
    assert job.trace_id == "a" * 32


def test_supabase_heartbeats_use_explicit_projection(monkeypatch) -> None:
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return [
                {
                    "worker_id": "worker-1",
                    "worker_role": "render",
                    "queues": ["render"],
                    "version": "test",
                    "metadata": {},
                    "last_seen_at": "2026-07-05T00:00:00+00:00",
                }
            ]

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("app.services.operations.httpx.get", fake_get)
    operations = OperationsRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )

    heartbeats = operations.latest_heartbeats()

    assert [heartbeat.worker_id for heartbeat in heartbeats] == ["worker-1"]
    assert calls == [
        (
            "https://supabase.example.com/rest/v1/worker_heartbeats",
            {
                "headers": operations.headers,
                "params": {
                    "select": WORKER_HEARTBEAT_COLUMNS,
                    "order": "last_seen_at.desc",
                    "limit": "100",
                },
                "timeout": 5,
            },
        )
    ]


def test_supabase_cost_summary_uses_explicit_projection(monkeypatch) -> None:
    render_job_id = uuid4()
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return [
                {
                    "workspace_id": str(uuid4()),
                    "render_job_id": str(render_job_id),
                    "export_id": str(uuid4()),
                    "provider": "renderer",
                    "category": "compute_gpu",
                    "quantity": 2,
                    "unit_cost_usd": 0.25,
                    "approved_at": "2026-07-05T00:00:00+00:00",
                }
            ]

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("app.services.operations.httpx.get", fake_get)
    operations = OperationsRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )

    summary = operations.cost_summary(render_job_id=render_job_id)

    assert summary["total_usd"] == 0.5
    assert calls == [
        (
            "https://supabase.example.com/rest/v1/cost_attribution_events",
            {
                "headers": operations.headers,
                "params": {
                    "select": COST_ATTRIBUTION_SUMMARY_COLUMNS,
                    "limit": "10000",
                    "render_job_id": f"eq.{render_job_id}",
                },
                "timeout": 10,
            },
        )
    ]


def test_gpu_compose_provisions_complete_topology() -> None:
    root = Path(__file__).resolve().parents[3]
    compose = (root / "infra/docker-compose.gpu.yml").read_text()
    for service in (
        "postgres:",
        "redis:",
        "supabase-auth:",
        "supabase-rest:",
        "supabase-storage:",
        "api:",
        "web:",
        "ingress:",
        "planning-worker:",
        "gpu-inference-worker:",
        "remotion-worker:",
    ):
        assert service in compose
    assert "secrets:" in compose
