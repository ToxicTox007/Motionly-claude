import json
from datetime import UTC, datetime

from app.services.queue import BullMQQueueRegistry, QueueJobStatus


class _Input:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, value: str) -> None:
        self.lines.append(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _Output:
    def __init__(self, results: list[object]) -> None:
        self.results = iter(results)
        self.request_id = 0

    def readline(self) -> str:
        self.request_id += 1
        return json.dumps({"id": self.request_id, "result": next(self.results)}) + "\n"


class _Process:
    def __init__(self, results: list[object]) -> None:
        self.stdin = _Input()
        self.stdout = _Output(results)
        self.stderr = _Output([])

    def poll(self):
        return None

    def wait(self, *, timeout: int):
        return 0


def test_bullmq_registry_reuses_one_persistent_bridge(monkeypatch) -> None:
    process = _Process([[], []])
    launches = []

    def popen(*args, **kwargs):
        launches.append((args, kwargs))
        return process

    monkeypatch.setattr("app.services.queue.subprocess.Popen", popen)
    registry = BullMQQueueRegistry(redis_url="redis://queue.test")
    assert registry.jobs == {}
    assert registry.jobs == {}
    assert len(launches) == 1
    assert launches[0][0][0][-1] == "serve"
    requests = [json.loads(line) for line in process.stdin.lines]
    assert [request["command"] for request in requests] == ["jobs", "jobs"]


def test_bullmq_stuck_jobs_transition_to_dead_letter(monkeypatch) -> None:
    now = datetime.now(UTC).isoformat()
    record = {
        "id": "stuck-render",
        "kind": "render",
        "queue_name": "render",
        "priority": 0,
        "attempts": 3,
        "max_attempts": 3,
        "status": "dead_letter",
        "enqueued_at": now,
        "workspace_id": None,
        "project_id": None,
        "resource_id": None,
        "render_engine": "ffmpeg_basic",
    }
    process = _Process([[record]])
    monkeypatch.setattr("app.services.queue.subprocess.Popen", lambda *args, **kwargs: process)
    transitioned = BullMQQueueRegistry(redis_url="redis://queue.test").mark_stuck_as_dead_letter(
        timeout_seconds=60
    )
    assert transitioned[0].status == QueueJobStatus.DEAD_LETTER
    request = json.loads(process.stdin.lines[0])
    assert request["command"] == "mark-stuck-dead-letter"
    assert request["payload"] == {"timeout_seconds": 60}


def test_bullmq_snapshots_use_count_bridge_command(monkeypatch) -> None:
    process = _Process(
        [
            [
                {
                    "queue_name": "render_gpu",
                    "queued": 750,
                    "running": 3,
                    "succeeded": 1000,
                    "failed": 0,
                    "dead_letter": 2,
                    "canceled": 0,
                    "oldest_queued_seconds": 180,
                }
            ]
        ]
    )
    monkeypatch.setattr("app.services.queue.subprocess.Popen", lambda *args, **kwargs: process)

    snapshots = BullMQQueueRegistry(redis_url="redis://queue.test").snapshots()

    assert snapshots[0].queue_name == "render_gpu"
    assert snapshots[0].queued == 750
    assert snapshots[0].dead_letter == 2
    request = json.loads(process.stdin.lines[0])
    assert request["command"] == "snapshots"
