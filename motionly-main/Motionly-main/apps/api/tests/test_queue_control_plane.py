import asyncio
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.plans import get_plan_job_status
from app.services.queue import BullMQQueueRegistry, EnqueuedJob

ROOT = Path(__file__).resolve().parents[3]
RUN_REDIS_CONTRACTS = os.getenv("RUN_REDIS_CONTRACTS") == "true"


def test_api_and_worker_bridges_have_one_exact_contract() -> None:
    api_bridge = ROOT / "apps/api/worker-bridge"
    worker_bridge = ROOT / "apps/worker/src"

    for filename in ("queue-bridge.mjs", "queue-common.mjs"):
        assert (api_bridge / filename).read_bytes() == (worker_bridge / filename).read_bytes()


def test_bullmq_registry_serializes_canonical_workflow_retry_fields(monkeypatch) -> None:
    captured = {}

    def bridge(command, payload):
        assert command == "enqueue"
        captured.update(payload)
        return {
            **payload,
            "attempts": 0,
            "status": "queued",
            "enqueued_at": "2026-08-08T12:00:00+00:00",
        }

    registry = BullMQQueueRegistry(redis_url="redis://queue.test")
    monkeypatch.setattr(registry, "_bridge", bridge)
    run_id = uuid4()
    step_id = uuid4()
    job = registry.enqueue(
        job_id="workflow-transport-job",
        kind="video_preprocess",
        queue_name="upload_preprocess",
        workflow_run_id=run_id,
        workflow_step_id=step_id,
        step_key="preprocess-source-v1",
        step_kind="preprocess_source",
        attempt_key="temporal-attempt:preprocess:0002",
        workflow_attempt_number=2,
        workflow_max_attempts=3,
        input_hash="a" * 64,
        max_attempts=1,
    )

    assert captured["max_attempts"] == 1
    assert captured["workflow_attempt_number"] == 2
    assert captured["workflow_max_attempts"] == 3
    assert job.max_attempts == 1
    assert job.workflow_attempt_number == 2
    assert job.workflow_max_attempts == 3
    assert job.attempt_key == "temporal-attempt:preprocess:0002"


def test_compose_workers_explicitly_use_worker_process_role() -> None:
    compose = (ROOT / "infra/docker-compose.gpu.yml").read_text(encoding="utf-8")
    worker_env = compose.split("x-media-environment:", 1)[1].split("x-media-volumes:", 1)[0]

    assert "PROCESS_ROLE: worker" in worker_env
    assert "WORKER_API_TOKEN_FILE: /run/secrets/worker_api_token" in worker_env

def test_bullmq_snapshots_reject_invalid_payload_without_scanning_jobs(monkeypatch) -> None:
    registry = BullMQQueueRegistry(redis_url="redis://queue.test")
    monkeypatch.setattr(registry, "_bridge", lambda command, payload: {"not": "snapshots"})

    with pytest.raises(RuntimeError, match="invalid snapshots payload"):
        registry.snapshots()


def test_plan_job_status_uses_direct_registry_lookup(monkeypatch) -> None:
    project_id = uuid4()
    workspace_id = uuid4()
    job = EnqueuedJob(
        id="plan-status-job",
        kind="edit_plan",
        queue_name="planning",
        project_id=project_id,
        workspace_id=workspace_id,
    )

    class DirectLookupRegistry:
        def __init__(self) -> None:
            self.lookups: list[str] = []

        @property
        def jobs(self):
            raise AssertionError("status polling must not load every queue job")

        def get_job(self, job_id: str, *, queue_name: str | None = None):
            self.lookups.append(f"{queue_name}:{job_id}")
            return job if queue_name == "planning" and job_id == job.id else None

    async def project_exists(*_args, **_kwargs):
        return object()

    monkeypatch.setattr("app.api.plans._get_project_for_workspace", project_exists)
    registry = DirectLookupRegistry()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(queue_registry=registry)))

    response = asyncio.run(
        get_plan_job_status(
            project_id=project_id,
            job_id=job.id,
            actor=Actor(user_id=uuid4(), workspace_id=workspace_id),
            repository=object(),
            request=request,
        )
    )

    assert response.status == "queued"
    assert registry.lookups == [f"planning:{job.id}"]


def test_plan_job_status_hides_foreign_workspace_job(monkeypatch) -> None:
    project_id = uuid4()
    workspace_id = uuid4()
    foreign_job = EnqueuedJob(
        id="opaque-foreign-job",
        kind="edit_plan",
        queue_name="planning",
        project_id=uuid4(),
        workspace_id=uuid4(),
    )

    class ForeignRegistry:
        def get_job(self, job_id: str, *, queue_name: str | None = None):
            assert job_id == foreign_job.id
            assert queue_name == "planning"
            return foreign_job

    async def project_exists(*_args, **_kwargs):
        return object()

    monkeypatch.setattr("app.api.plans._get_project_for_workspace", project_exists)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(queue_registry=ForeignRegistry()))
    )

    response = asyncio.run(
        get_plan_job_status(
            project_id=project_id,
            job_id=foreign_job.id,
            actor=Actor(user_id=uuid4(), workspace_id=workspace_id),
            repository=object(),
            request=request,
        )
    )

    assert response.status == "failed"
    assert response.failure_reason == "Job not found"


@pytest.mark.skipif(
    not RUN_REDIS_CONTRACTS,
    reason="set RUN_REDIS_CONTRACTS=true and REDIS_URL to run the real Redis bridge contract",
)
def test_api_shipped_bridge_round_trips_job_jobs_and_snapshots_with_redis() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/15")
    bridge_path = ROOT / "apps/worker/src/queue-bridge.mjs"
    registry = BullMQQueueRegistry(redis_url=redis_url, bridge_path=str(bridge_path))
    job_id = f"queue-control-contract-{uuid4()}"

    try:
        enqueued = registry.enqueue(
            job_id=job_id,
            kind="queue_control_contract",
            queue_name="maintenance",
        )
        assert enqueued.id == job_id

        direct = registry.get_job(job_id, queue_name="maintenance")
        assert direct is not None
        assert direct.id == job_id
        assert direct.queue_name == "maintenance"

        assert registry.jobs[job_id].kind == "queue_control_contract"
        maintenance = next(
            snapshot for snapshot in registry.snapshots() if snapshot.queue_name == "maintenance"
        )
        assert maintenance.queued >= 1
        assert maintenance.oldest_queued_seconds is not None
    finally:
        try:
            if registry.get_job(job_id, queue_name="maintenance") is not None:
                registry.cancel(job_id)
        finally:
            registry.close()


def _run_worker_node(script: str, *, redis_url: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "REDIS_URL": redis_url}
    return subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT / "apps/worker",
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(
    not RUN_REDIS_CONTRACTS,
    reason="set RUN_REDIS_CONTRACTS=true and REDIS_URL to run the real Redis bridge contract",
)
def test_paused_queue_counts_as_backlog_not_cancellation() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/15")
    bridge_path = ROOT / "apps/worker/src/queue-bridge.mjs"
    registry = BullMQQueueRegistry(redis_url=redis_url, bridge_path=str(bridge_path))
    job_id = f"paused-queue-contract-{uuid4()}"
    pause_script = """
import { Queue } from 'bullmq';
import IORedis from 'ioredis';
const connection = new IORedis(process.env.REDIS_URL, { maxRetriesPerRequest: null });
const queue = new Queue('maintenance', { connection });
await queue.pause();
await queue.close();
await connection.quit();
"""
    resume_script = pause_script.replace("await queue.pause();", "await queue.resume();")

    try:
        registry.enqueue(
            job_id=job_id,
            kind="paused_queue_contract",
            queue_name="maintenance",
        )
        _run_worker_node(pause_script, redis_url=redis_url)
        snapshot = next(item for item in registry.snapshots() if item.queue_name == "maintenance")
        assert snapshot.queued >= 1
        assert snapshot.canceled == 0
    finally:
        _run_worker_node(resume_script, redis_url=redis_url)
        try:
            if registry.get_job(job_id, queue_name="maintenance") is not None:
                registry.cancel(job_id)
        finally:
            registry.close()


@pytest.mark.skipif(
    not RUN_REDIS_CONTRACTS,
    reason="set RUN_REDIS_CONTRACTS=true and REDIS_URL to run the real Redis bridge contract",
)
def test_recover_stuck_does_not_relabel_a_live_locked_job() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/15")
    bridge_path = ROOT / "apps/worker/src/queue-bridge.mjs"
    registry = BullMQQueueRegistry(redis_url=redis_url, bridge_path=str(bridge_path))
    job_id = f"live-lock-contract-{uuid4()}"
    worker_script = """
import { Worker } from 'bullmq';
import IORedis from 'ioredis';
const connection = new IORedis(process.env.REDIS_URL, { maxRetriesPerRequest: null });
const worker = new Worker('maintenance', async () => new Promise(() => {}), {
  connection,
  lockDuration: 10000,
  stalledInterval: 1000,
});
worker.on('active', () => console.log('active'));
await worker.waitUntilReady();
console.log('ready');
await new Promise(() => {});
"""
    env = {**os.environ, "REDIS_URL": redis_url}
    process = subprocess.Popen(
        ["node", "--input-type=module", "--eval", worker_script],
        cwd=ROOT / "apps/worker",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        registry.enqueue(
            job_id=job_id,
            kind="live_lock_contract",
            queue_name="maintenance",
        )
        assert process.stdout.readline().strip() == "active"
        time.sleep(1.1)

        transitioned = registry.recover_stuck_jobs(timeout_seconds=1)
        current = registry.get_job(job_id, queue_name="maintenance")

        assert transitioned == []
        assert current is not None
        assert current.status.value == "running"
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        registry.close()
        _run_worker_node(
            """
import { Queue } from 'bullmq';
import IORedis from 'ioredis';
const connection = new IORedis(process.env.REDIS_URL, { maxRetriesPerRequest: null });
const queue = new Queue('maintenance', { connection });
await queue.obliterate({ force: true });
await queue.close();
await connection.quit();
""",
            redis_url=redis_url,
        )


def test_bullmq_transitions_use_direct_job_lookup(monkeypatch) -> None:
    record = {
        "id": "transition-job",
        "kind": "render",
        "queue_name": "render",
        "priority": 0,
        "attempts": 0,
        "max_attempts": 3,
        "status": "queued",
        "enqueued_at": "2026-07-14T00:00:00+00:00",
        "workspace_id": None,
        "project_id": None,
        "resource_id": None,
        "render_engine": "ffmpeg_basic",
    }
    commands: list[tuple[str, dict[str, object]]] = []

    def bridge(command: str, payload: dict[str, object]):
        commands.append((command, payload))
        return record

    registry = BullMQQueueRegistry(redis_url="redis://queue.test")
    monkeypatch.setattr(registry, "_bridge", bridge)

    registry.start(record["id"])
    registry.complete(record["id"])
    registry.fail(record["id"])

    assert commands == [
        ("job", {"id": "transition-job", "queue_name": None}),
        ("job", {"id": "transition-job", "queue_name": None}),
        ("job", {"id": "transition-job", "queue_name": None}),
    ]
