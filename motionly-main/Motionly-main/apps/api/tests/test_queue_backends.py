import json
from collections import deque
from uuid import uuid4

from app.services.queue import BullMQQueueRegistry, QueueJobStatus, RenderQueue, ScaleQueue


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
