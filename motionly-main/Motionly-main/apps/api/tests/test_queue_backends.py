import json
from collections import deque
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.services.queue import (
    BullMQQueueRegistry,
    QueueJobStatus,
    QueueRegistry,
    QueueSnapshot,
    RenderQueue,
    ScaleQueue,
)


def _record(*, status: str = "queued") -> dict[str, object]:
    return {
        "id": "job-1",
        "kind": "render",
        "queue_name": "render",
        "priority": 0,
        "attempts": 0,
        "max_attempts": 3,
        "status": status,
        "enqueued_at": "2026-06-22T00:00:00+00:00",
        "workspace_id": None,
        "project_id": None,
        "resource_id": None,
        "render_engine": "ffmpeg_basic",
    }


def test_bullmq_registry_reuses_one_persistent_bridge(monkeypatch) -> None:
    responses = deque(
        [
            json.dumps({"id": 1, "result": [_record()]}) + "\n",
            json.dumps({"id": 2, "result": [_record()]}) + "\n",
        ]
    )

    class Input:
        def write(self, _value):
            return None

        def flush(self):
            return None

    class Output:
        def readline(self):
            return responses.popleft()

    class Process:
        stdin = Input()
        stdout = Output()
        stderr = Output()

        def poll(self):
            return None

    created = []

    def popen(*args, **kwargs):
        created.append((args, kwargs))
        return Process()

    monkeypatch.setattr("app.services.queue.subprocess.Popen", popen)
    registry = BullMQQueueRegistry(redis_url="redis://queue.test")
    assert registry.jobs["job-1"].status == QueueJobStatus.QUEUED
    assert registry.jobs["job-1"].status == QueueJobStatus.QUEUED
    assert len(created) == 1
    assert created[0][0][0][-1] == "serve"


def test_bullmq_stuck_transition_returns_persisted_dead_letter(monkeypatch) -> None:
    response = json.dumps({"id": 1, "result": [_record(status="dead_letter")]}) + "\n"

    class Stream:
        def write(self, _value):
            return None

        def flush(self):
            return None

        def readline(self):
            return response

    class Process:
        stdin = Stream()
        stdout = Stream()
        stderr = Stream()

        def poll(self):
            return None

    monkeypatch.setattr("app.services.queue.subprocess.Popen", lambda *args, **kwargs: Process())
    moved = BullMQQueueRegistry(redis_url="redis://queue.test").mark_stuck_as_dead_letter(
        timeout_seconds=30
    )
    assert moved[0].status == QueueJobStatus.DEAD_LETTER


def test_bullmq_registry_get_job_uses_direct_bridge_lookup(monkeypatch) -> None:
    response = json.dumps({"id": 1, "result": _record()}) + "\n"
    writes = []

    class Stream:
        def write(self, value):
            writes.append(json.loads(value))

        def flush(self):
            return None

        def readline(self):
            return response

    class Process:
        stdin = Stream()
        stdout = Stream()
        stderr = Stream()

        def poll(self):
            return None

    monkeypatch.setattr("app.services.queue.subprocess.Popen", lambda *args, **kwargs: Process())
    job = BullMQQueueRegistry(redis_url="redis://queue.test").get_job(
        "job-1", queue_name="render"
    )
    assert job is not None
    assert job.status == QueueJobStatus.QUEUED
    assert writes[0]["command"] == "job"
    assert writes[0]["payload"] == {"id": "job-1", "queue_name": "render"}


def test_specialized_queue_routing() -> None:
    project_id = uuid4()
    workspace_id = uuid4()
    remotion = RenderQueue().enqueue_render(
        render_job_id=uuid4(),
        project_id=project_id,
        workspace_id=workspace_id,
        render_engine="remotion_premium",
    )
    stems = ScaleQueue().enqueue_resource(
        resource_id=uuid4(),
        workspace_id=workspace_id,
        project_id=project_id,
        resource_kind="stem_separation",
    )
    provider = ScaleQueue().enqueue_resource(
        resource_id=uuid4(),
        workspace_id=workspace_id,
        project_id=project_id,
        resource_kind="generated_broll",
    )
    assert remotion.queue_name == "render_remotion"
    assert stems.queue_name == "audio_demucs"
    assert provider.queue_name == "provider_calls"


def test_expensive_queue_duplicate_active_job_is_deduped() -> None:
    registry = QueueRegistry()
    queue = RenderQueue(registry)
    render_job_id = uuid4()
    project_id = uuid4()
    workspace_id = uuid4()

    first = queue.enqueue_render(
        render_job_id=render_job_id,
        project_id=project_id,
        workspace_id=workspace_id,
        compute_tier="gpu",
    )
    second = queue.enqueue_render(
        render_job_id=render_job_id,
        project_id=project_id,
        workspace_id=workspace_id,
        compute_tier="gpu",
    )

    assert second == first
    assert len(registry.jobs) == 1


def test_queue_backpressure_blocks_overloaded_queue() -> None:
    class OverloadedRegistry:
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

        def enqueue(self, **_kwargs):
            raise AssertionError("overloaded queues should not enqueue jobs")

    with pytest.raises(ApiError) as exc_info:
        RenderQueue(OverloadedRegistry()).enqueue_render(
            render_job_id=uuid4(),
            project_id=uuid4(),
            workspace_id=uuid4(),
            compute_tier="gpu",
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "queue_backpressure"
