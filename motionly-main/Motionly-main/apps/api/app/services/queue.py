import hashlib
import json
import logging
import os
import select
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from app.services.queue_slo import raise_if_queue_backpressured
from app.services.request_counters import increment_queue_calls
from app.services.tracing import current_trace_id

logger = logging.getLogger(__name__)


class QueueJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class EnqueuedJob:
    id: str
    kind: str
    queue_name: str = "default"
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    status: QueueJobStatus = QueueJobStatus.QUEUED
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    workspace_id: UUID | None = None
    project_id: UUID | None = None
    resource_id: UUID | None = None
    workflow_run_id: UUID | None = None
    workflow_step_id: UUID | None = None
    step_key: str | None = None
    step_kind: str | None = None
    attempt_key: str | None = None
    workflow_attempt_number: int | None = None
    workflow_max_attempts: int | None = None
    input_hash: str | None = None
    next_run_state: str | None = None
    # Render engine hint stored so the worker can route without a manifest fetch
    render_engine: str = "ffmpeg_basic"
    trace_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class QueueSnapshot:
    queue_name: str
    queued: int
    running: int
    succeeded: int
    failed: int
    dead_letter: int
    canceled: int
    oldest_queued_seconds: int | None = None
    failed_scan_truncated: bool = False


class QueueRegistry:
    def __init__(self) -> None:
        self.jobs: dict[str, EnqueuedJob] = {}

    def get_job(self, job_id: str, *, queue_name: str | None = None) -> EnqueuedJob | None:
        increment_queue_calls()
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if queue_name is not None and job.queue_name != queue_name:
            return None
        return job

    def enqueue(
        self,
        *,
        job_id: str,
        kind: str,
        queue_name: str,
        priority: int = 0,
        workspace_id: UUID | None = None,
        project_id: UUID | None = None,
        resource_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        workflow_step_id: UUID | None = None,
        step_key: str | None = None,
        step_kind: str | None = None,
        attempt_key: str | None = None,
        workflow_attempt_number: int | None = None,
        workflow_max_attempts: int | None = None,
        input_hash: str | None = None,
        next_run_state: str | None = None,
        max_attempts: int = 3,
        render_engine: str = "ffmpeg_basic",
    ) -> EnqueuedJob:
        increment_queue_calls()
        job = EnqueuedJob(
            id=job_id,
            kind=kind,
            queue_name=queue_name,
            priority=priority,
            max_attempts=max_attempts,
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=resource_id,
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            step_key=step_key,
            step_kind=step_kind,
            attempt_key=attempt_key,
            workflow_attempt_number=workflow_attempt_number,
            workflow_max_attempts=workflow_max_attempts,
            input_hash=input_hash,
            next_run_state=next_run_state,
            render_engine=render_engine,
            trace_id=current_trace_id.get(),
        )
        self.jobs[job.id] = job
        return job

    def retry(self, job_id: str) -> EnqueuedJob:
        increment_queue_calls()
        job = self.jobs[job_id]
        if job.status in {
            QueueJobStatus.CANCELED,
            QueueJobStatus.SUCCEEDED,
            QueueJobStatus.RUNNING,
        }:
            raise ValueError(f"Queue job cannot be retried in state {job.status}")
        if job.status == QueueJobStatus.QUEUED:
            return job
        attempts = job.attempts + 1
        status = (
            QueueJobStatus.DEAD_LETTER if attempts >= job.max_attempts else QueueJobStatus.QUEUED
        )
        retried = EnqueuedJob(
            id=job.id,
            kind=job.kind,
            queue_name=job.queue_name,
            priority=job.priority,
            attempts=attempts,
            max_attempts=job.max_attempts,
            status=status,
            enqueued_at=datetime.now(UTC),
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            resource_id=job.resource_id,
            workflow_run_id=job.workflow_run_id,
            workflow_step_id=job.workflow_step_id,
            step_key=job.step_key,
            step_kind=job.step_kind,
            attempt_key=job.attempt_key,
            workflow_attempt_number=job.workflow_attempt_number,
            workflow_max_attempts=job.workflow_max_attempts,
            input_hash=job.input_hash,
            next_run_state=job.next_run_state,
            render_engine=job.render_engine,
            trace_id=job.trace_id,
            error_code=None,
            error_message=None,
        )
        self.jobs[job_id] = retried
        return retried

    def start(self, job_id: str) -> EnqueuedJob:
        increment_queue_calls()
        job = self.jobs[job_id]
        started = self._copy_job(job, status=QueueJobStatus.RUNNING)
        self.jobs[job_id] = started
        return started

    def complete(self, job_id: str) -> EnqueuedJob:
        increment_queue_calls()
        job = self.jobs[job_id]
        completed = self._copy_job(job, status=QueueJobStatus.SUCCEEDED)
        self.jobs[job_id] = completed
        return completed

    def fail(
        self,
        job_id: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        backoff_seconds: int | None = None,
    ) -> EnqueuedJob:
        increment_queue_calls()
        job = self.jobs[job_id]
        attempts = job.attempts + 1
        status = (
            QueueJobStatus.DEAD_LETTER if attempts >= job.max_attempts else QueueJobStatus.QUEUED
        )
        delay = timedelta(seconds=max(0, backoff_seconds or 0))
        failed = EnqueuedJob(
            id=job.id,
            kind=job.kind,
            queue_name=job.queue_name,
            priority=job.priority,
            attempts=attempts,
            max_attempts=job.max_attempts,
            status=status,
            enqueued_at=datetime.now(UTC) + delay,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            resource_id=job.resource_id,
            workflow_run_id=job.workflow_run_id,
            workflow_step_id=job.workflow_step_id,
            step_key=job.step_key,
            step_kind=job.step_kind,
            attempt_key=job.attempt_key,
            workflow_attempt_number=job.workflow_attempt_number,
            workflow_max_attempts=job.workflow_max_attempts,
            input_hash=job.input_hash,
            next_run_state=job.next_run_state,
            render_engine=job.render_engine,
            trace_id=job.trace_id,
            error_code=error_code,
            error_message=error_message,
        )
        self.jobs[job_id] = failed
        return failed

    def cancel(self, job_id: str) -> EnqueuedJob:
        increment_queue_calls()
        job = self.jobs[job_id]
        if job.status in {
            QueueJobStatus.CANCELED,
            QueueJobStatus.SUCCEEDED,
            QueueJobStatus.FAILED,
            QueueJobStatus.DEAD_LETTER,
        }:
            raise ValueError(f"Queue job cannot be canceled in state {job.status}")
        canceled = EnqueuedJob(
            id=job.id,
            kind=job.kind,
            queue_name=job.queue_name,
            priority=job.priority,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            status=QueueJobStatus.CANCELED,
            enqueued_at=job.enqueued_at,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            resource_id=job.resource_id,
            workflow_run_id=job.workflow_run_id,
            workflow_step_id=job.workflow_step_id,
            step_key=job.step_key,
            step_kind=job.step_kind,
            attempt_key=job.attempt_key,
            workflow_attempt_number=job.workflow_attempt_number,
            workflow_max_attempts=job.workflow_max_attempts,
            input_hash=job.input_hash,
            next_run_state=job.next_run_state,
            render_engine=job.render_engine,
            trace_id=job.trace_id,
            error_code=job.error_code,
            error_message=job.error_message,
        )
        self.jobs[job_id] = canceled
        return canceled

    def mark_stuck_as_dead_letter(self, *, timeout_seconds: int) -> list[EnqueuedJob]:
        increment_queue_calls()
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
        moved = []
        for job in list(self.jobs.values()):
            if job.status not in {QueueJobStatus.QUEUED, QueueJobStatus.RUNNING}:
                continue
            if job.enqueued_at > cutoff:
                continue
            dead = EnqueuedJob(
                id=job.id,
                kind=job.kind,
                queue_name=job.queue_name,
                priority=job.priority,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                status=QueueJobStatus.DEAD_LETTER,
                enqueued_at=job.enqueued_at,
                workspace_id=job.workspace_id,
                project_id=job.project_id,
                resource_id=job.resource_id,
                workflow_run_id=job.workflow_run_id,
                workflow_step_id=job.workflow_step_id,
                step_key=job.step_key,
                step_kind=job.step_kind,
                attempt_key=job.attempt_key,
                workflow_attempt_number=job.workflow_attempt_number,
                workflow_max_attempts=job.workflow_max_attempts,
                input_hash=job.input_hash,
                next_run_state=job.next_run_state,
                render_engine=job.render_engine,
                trace_id=job.trace_id,
                error_code=job.error_code,
                error_message=job.error_message,
            )
            self.jobs[job.id] = dead
            moved.append(dead)
        return moved

    def recover_stuck_jobs(
        self,
        *,
        timeout_seconds: int,
        backoff_seconds: int = 30,
    ) -> list[EnqueuedJob]:
        increment_queue_calls()
        cutoff = datetime.now(UTC) - timedelta(seconds=max(1, timeout_seconds))
        recovered = []
        for job in list(self.jobs.values()):
            if job.status != QueueJobStatus.RUNNING or job.enqueued_at > cutoff:
                continue
            recovered.append(
                self.fail(
                    job.id,
                    error_code="stuck_timeout",
                    error_message="Worker job exceeded the stuck timeout and was requeued.",
                    backoff_seconds=backoff_seconds,
                )
            )
            logger.warning(
                "stuck_job_failed"
                if recovered[-1].status == QueueJobStatus.DEAD_LETTER
                else "stuck_job_recovered",
                extra={"job_id": job.id, "kind": job.kind, "queue_name": job.queue_name},
            )
        return recovered

    def snapshots(self) -> list[QueueSnapshot]:
        increment_queue_calls()
        queue_names = sorted({job.queue_name for job in self.jobs.values()})
        now = datetime.now(UTC)
        snapshots = []
        for queue_name in queue_names:
            jobs = [job for job in self.jobs.values() if job.queue_name == queue_name]
            queued_jobs = [job for job in jobs if job.status == QueueJobStatus.QUEUED]
            oldest = min((job.enqueued_at for job in queued_jobs), default=None)
            snapshots.append(
                QueueSnapshot(
                    queue_name=queue_name,
                    queued=len(queued_jobs),
                    running=len([job for job in jobs if job.status == QueueJobStatus.RUNNING]),
                    succeeded=len([job for job in jobs if job.status == QueueJobStatus.SUCCEEDED]),
                    failed=len([job for job in jobs if job.status == QueueJobStatus.FAILED]),
                    dead_letter=len(
                        [job for job in jobs if job.status == QueueJobStatus.DEAD_LETTER]
                    ),
                    canceled=len([job for job in jobs if job.status == QueueJobStatus.CANCELED]),
                    oldest_queued_seconds=round((now - oldest).total_seconds()) if oldest else None,
                )
            )
        return snapshots

    def _copy_job(self, job: EnqueuedJob, *, status: QueueJobStatus) -> EnqueuedJob:
        return EnqueuedJob(
            id=job.id,
            kind=job.kind,
            queue_name=job.queue_name,
            priority=job.priority,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            status=status,
            enqueued_at=job.enqueued_at,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            resource_id=job.resource_id,
            workflow_run_id=job.workflow_run_id,
            workflow_step_id=job.workflow_step_id,
            step_key=job.step_key,
            step_kind=job.step_kind,
            attempt_key=job.attempt_key,
            workflow_attempt_number=job.workflow_attempt_number,
            workflow_max_attempts=job.workflow_max_attempts,
            input_hash=job.input_hash,
            next_run_state=job.next_run_state,
            render_engine=job.render_engine,
            trace_id=job.trace_id,
            error_code=job.error_code,
            error_message=job.error_message,
        )


class BullMQQueueRegistry:
    def __init__(
        self,
        *,
        redis_url: str,
        bridge_path: str | None = None,
        node_command: str = "node",
        job_attempts: int = 3,
        bridge_timeout_seconds: float = 10.0,
    ) -> None:
        self.redis_url = redis_url
        self.bridge_path = bridge_path or str(self._default_bridge_path())
        self.node_command = node_command
        self.job_attempts = job_attempts
        self.bridge_timeout_seconds = max(0.1, float(bridge_timeout_seconds))
        self._bridge_process: subprocess.Popen[str] | None = None
        self._bridge_lock = threading.Lock()
        self._bridge_request_id = 0

    @staticmethod
    def _default_bridge_path() -> Path:
        current = Path(__file__).resolve()
        candidates = [
            Path("/app/apps/api/worker-bridge/queue-bridge.mjs"),
            Path("/app/apps/worker/src/queue-bridge.mjs"),
        ]
        candidates.extend(
            parent / "apps" / "api" / "worker-bridge" / "queue-bridge.mjs"
            for parent in current.parents
        )
        candidates.extend(
            parent / "apps" / "worker" / "src" / "queue-bridge.mjs" for parent in current.parents
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[-1]

    @property
    def jobs(self) -> dict[str, EnqueuedJob]:
        records = self._bridge("jobs", {})
        if not isinstance(records, list):
            return {}
        jobs: dict[str, EnqueuedJob] = {}
        for record in records:
            if isinstance(record, dict):
                job = self._job_from_bridge(record)
                jobs[job.id] = job
        return jobs

    def get_job(self, job_id: str, *, queue_name: str | None = None) -> EnqueuedJob | None:
        increment_queue_calls()
        record = self._bridge("job", {"id": job_id, "queue_name": queue_name})
        if record is None:
            return None
        return self._job_from_bridge(record)

    def enqueue(
        self,
        *,
        job_id: str,
        kind: str,
        queue_name: str,
        priority: int = 0,
        workspace_id: UUID | None = None,
        project_id: UUID | None = None,
        resource_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        workflow_step_id: UUID | None = None,
        step_key: str | None = None,
        step_kind: str | None = None,
        attempt_key: str | None = None,
        workflow_attempt_number: int | None = None,
        workflow_max_attempts: int | None = None,
        input_hash: str | None = None,
        next_run_state: str | None = None,
        max_attempts: int = 3,
        render_engine: str = "ffmpeg_basic",
    ) -> EnqueuedJob:
        increment_queue_calls()
        payload = {
            "id": job_id,
            "kind": kind,
            "queue_name": queue_name,
            "priority": priority,
            "workspace_id": str(workspace_id) if workspace_id else None,
            "project_id": str(project_id) if project_id else None,
            "resource_id": str(resource_id) if resource_id else None,
            "workflow_run_id": str(workflow_run_id) if workflow_run_id else None,
            "workflow_step_id": str(workflow_step_id) if workflow_step_id else None,
            "step_key": step_key,
            "step_kind": step_kind,
            "attempt_key": attempt_key,
            "workflow_attempt_number": workflow_attempt_number,
            "workflow_max_attempts": workflow_max_attempts,
            "input_hash": input_hash,
            "next_run_state": next_run_state,
            "max_attempts": max_attempts or self.job_attempts,
            "render_engine": render_engine,
            "trace_id": current_trace_id.get(),
        }
        return self._job_from_bridge(self._bridge("enqueue", payload))

    def retry(self, job_id: str) -> EnqueuedJob:
        increment_queue_calls()
        return self._job_from_bridge(self._bridge("retry", {"id": job_id}))

    def cancel(self, job_id: str) -> EnqueuedJob:
        increment_queue_calls()
        return self._job_from_bridge(self._bridge("cancel", {"id": job_id}))

    def mark_stuck_as_dead_letter(self, *, timeout_seconds: int) -> list[EnqueuedJob]:
        increment_queue_calls()
        records = self._bridge(
            "mark-stuck-dead-letter",
            {"timeout_seconds": max(1, timeout_seconds)},
        )
        if not isinstance(records, list):
            return []
        return [self._job_from_bridge(record) for record in records]

    def recover_stuck_jobs(
        self,
        *,
        timeout_seconds: int,
        backoff_seconds: int = 30,
    ) -> list[EnqueuedJob]:
        increment_queue_calls()
        records = self._bridge(
            "recover-stuck",
            {
                "timeout_seconds": max(1, timeout_seconds),
                "backoff_seconds": max(1, backoff_seconds),
            },
        )
        if not isinstance(records, list):
            return []
        jobs = [self._job_from_bridge(record) for record in records]
        for job in jobs:
            logger.warning(
                "stuck_job_failed"
                if job.status == QueueJobStatus.DEAD_LETTER
                else "stuck_job_recovered",
                extra={"job_id": job.id, "kind": job.kind, "queue_name": job.queue_name},
            )
        return jobs

    def snapshots(self) -> list[QueueSnapshot]:
        increment_queue_calls()
        records = self._bridge("snapshots", {})
        if not isinstance(records, list):
            raise RuntimeError("BullMQ bridge returned an invalid snapshots payload")
        snapshots = []
        for record in records:
            if not isinstance(record, dict):
                raise RuntimeError("BullMQ bridge returned an invalid snapshot record")
            snapshots.append(
                QueueSnapshot(
                    queue_name=str(record.get("queue_name") or ""),
                    queued=int(record.get("queued") or 0),
                    running=int(record.get("running") or 0),
                    succeeded=int(record.get("succeeded") or 0),
                    failed=int(record.get("failed") or 0),
                    dead_letter=int(record.get("dead_letter") or 0),
                    canceled=int(record.get("canceled") or 0),
                    failed_scan_truncated=bool(record.get("failed_scan_truncated", False)),
                    oldest_queued_seconds=(
                        int(record["oldest_queued_seconds"])
                        if record.get("oldest_queued_seconds") is not None
                        else None
                    ),
                )
            )
        return snapshots

    def _bridge(self, command: str, payload: dict[str, object]) -> object:
        env = {
            **os.environ,
            "REDIS_URL": self.redis_url,
            "JOB_ATTEMPTS": str(self.job_attempts),
        }
        with self._bridge_lock:
            process = self._ensure_bridge_process(env)
            self._bridge_request_id += 1
            request_id = self._bridge_request_id
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(
                json.dumps({"id": request_id, "command": command, "payload": payload}) + "\n"
            )
            process.stdin.flush()
            try:
                output_fd = process.stdout.fileno()
            except (AttributeError, OSError, TypeError):
                line = process.stdout.readline()
            else:
                ready, _, _ = select.select(
                    [output_fd],
                    [],
                    [],
                    self.bridge_timeout_seconds,
                )
                if not ready:
                    self.close()
                    raise TimeoutError(f"BullMQ bridge timed out handling {command!r}")
                line = process.stdout.readline()
            if not line:
                stderr = process.stderr.read() if process.stderr else ""
                self.close()
                raise RuntimeError(stderr.strip() or "Persistent BullMQ bridge stopped")
            response = json.loads(line)
            if response.get("id") != request_id:
                raise RuntimeError("Persistent BullMQ bridge response was out of sequence")
            if response.get("error"):
                raise RuntimeError(str(response["error"]))
            return response.get("result")

    def _ensure_bridge_process(self, env: dict[str, str]) -> subprocess.Popen[str]:
        if self._bridge_process is None or self._bridge_process.poll() is not None:
            self._bridge_process = subprocess.Popen(
                [self.node_command, self.bridge_path, "serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
        return self._bridge_process

    def close(self) -> None:
        process, self._bridge_process = self._bridge_process, None
        if process is None or process.poll() is not None:
            return
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()

    def _job_from_bridge(self, data: object) -> EnqueuedJob:
        if not isinstance(data, dict):
            raise RuntimeError("BullMQ bridge returned an invalid job payload")
        return EnqueuedJob(
            id=str(data["id"]),
            kind=str(data["kind"]),
            queue_name=str(data["queue_name"]),
            priority=int(data.get("priority") or 0),
            attempts=int(data.get("attempts") or 0),
            max_attempts=int(data.get("max_attempts") or self.job_attempts),
            status=QueueJobStatus(str(data.get("status") or QueueJobStatus.QUEUED.value)),
            enqueued_at=datetime.fromisoformat(str(data["enqueued_at"])),
            workspace_id=UUID(str(data["workspace_id"])) if data.get("workspace_id") else None,
            project_id=UUID(str(data["project_id"])) if data.get("project_id") else None,
            resource_id=UUID(str(data["resource_id"])) if data.get("resource_id") else None,
            workflow_run_id=(
                UUID(str(data["workflow_run_id"])) if data.get("workflow_run_id") else None
            ),
            workflow_step_id=(
                UUID(str(data["workflow_step_id"])) if data.get("workflow_step_id") else None
            ),
            step_key=str(data["step_key"]) if data.get("step_key") else None,
            step_kind=str(data["step_kind"]) if data.get("step_kind") else None,
            attempt_key=str(data["attempt_key"]) if data.get("attempt_key") else None,
            workflow_attempt_number=(
                int(data["workflow_attempt_number"])
                if data.get("workflow_attempt_number") is not None
                else None
            ),
            workflow_max_attempts=(
                int(data["workflow_max_attempts"])
                if data.get("workflow_max_attempts") is not None
                else None
            ),
            input_hash=str(data["input_hash"]) if data.get("input_hash") else None,
            next_run_state=(str(data["next_run_state"]) if data.get("next_run_state") else None),
            render_engine=str(data.get("render_engine") or "ffmpeg_basic"),
            trace_id=str(data["trace_id"]) if data.get("trace_id") else None,
            error_code=str(data["error_code"]) if data.get("error_code") else None,
            error_message=str(data["error_message"]) if data.get("error_message") else None,
        )


class BaseQueue:
    queue_name = "default"
    kind = "job"
    active_duplicate_statuses = {
        QueueJobStatus.QUEUED,
        QueueJobStatus.RUNNING,
    }

    def __init__(
        self,
        registry: QueueRegistry | BullMQQueueRegistry | None = None,
    ) -> None:
        self.registry = registry or QueueRegistry()

    def _enqueue(
        self,
        *,
        job_id: str,
        kind: str | None = None,
        priority: int = 0,
        workspace_id: UUID | None = None,
        project_id: UUID | None = None,
        resource_id: UUID | None = None,
        render_engine: str = "ffmpeg_basic",
        queue_name: str | None = None,
    ) -> EnqueuedJob:
        target_queue_name = queue_name or self.queue_name
        duplicate = self._active_duplicate(job_id=job_id, queue_name=target_queue_name)
        if duplicate is not None:
            logger.info(
                "duplicate_queue_job_deduped",
                extra={
                    "job_id": duplicate.id,
                    "kind": kind or self.kind,
                    "queue_name": target_queue_name,
                    "status": duplicate.status.value,
                },
            )
            return duplicate
        raise_if_queue_backpressured(self.registry, target_queue_name)
        return self.registry.enqueue(
            job_id=job_id,
            kind=kind or self.kind,
            queue_name=target_queue_name,
            priority=priority,
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=resource_id,
            render_engine=render_engine,
        )

    def _job_id(self, *parts: object) -> str:
        return "--".join(str(part) for part in parts if part is not None)

    def _active_duplicate(self, *, job_id: str, queue_name: str) -> EnqueuedJob | None:
        getter = getattr(self.registry, "get_job", None)
        if getter is None:
            return None
        try:
            job = getter(job_id, queue_name=queue_name)
        except Exception:
            logger.exception(
                "queue_duplicate_lookup_failed",
                extra={"job_id": job_id, "queue_name": queue_name},
            )
            return None
        if job is None or job.status not in self.active_duplicate_statuses:
            return None
        return job

    def retry(self, job_id: str) -> EnqueuedJob:
        return self.registry.retry(job_id)

    def cancel(self, job_id: str) -> EnqueuedJob:
        return self.registry.cancel(job_id)


class PreprocessQueue(BaseQueue):
    queue_name = "upload_preprocess"
    kind = "video_preprocess"

    def enqueue_video_preprocess(
        self,
        *,
        video_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("preprocess", video_id),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=video_id,
        )


class TranscriptionQueue(BaseQueue):
    queue_name = "transcription"

    def enqueue_transcription(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        video_id: UUID,
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("transcribe", project_id, video_id, workspace_id),
            kind="transcription",
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=video_id,
        )

    def enqueue_caption_generation(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        transcript_id: UUID,
        job_payload: str = "{}",
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("captions", project_id, transcript_id, workspace_id),
            kind="caption_generation",
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=transcript_id,
            render_engine=job_payload,
        )


class VisualQueue(BaseQueue):
    queue_name = "visual_analysis"
    kind = "visual_analysis"

    def enqueue_visual_analysis(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        video_id: UUID,
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("visual", project_id, video_id, workspace_id),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=video_id,
        )


class MatteQueue(BaseQueue):
    queue_name = "matte_extraction"
    kind = "matte_extraction"

    def enqueue_matte(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        video_id: UUID,
        matte_id: UUID,
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("matte", project_id, video_id, workspace_id, matte_id),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=video_id,
            render_engine=str(matte_id),
        )


class PlanQueue(BaseQueue):
    queue_name = "planning"
    kind = "edit_plan"

    def enqueue_plan(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        transcript_id: UUID,
        proposal_id: UUID | None = None,
        request_id: str | None = None,
        context_digest: str | None = None,
        render_engine: str = "ffmpeg_basic",
    ) -> EnqueuedJob:
        resource_id = proposal_id or transcript_id
        identity = (
            "plan",
            project_id,
            resource_id,
            (request_id or "legacy")[:16],
            (context_digest or "legacy")[:16],
            workspace_id,
        )
        return self._enqueue(
            job_id=self._job_id(*identity),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=resource_id,
            render_engine=render_engine,
        )


class BrollQueue(BaseQueue):
    queue_name = "stock_search"
    kind = "broll_generation"

    def enqueue_broll(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        edit_plan_id: UUID,
        job_payload: str = "{}",
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=":".join(
                str(part) for part in ("broll", project_id, edit_plan_id, workspace_id)
            ),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=edit_plan_id,
            render_engine=job_payload,
        )


class StockAssetEnrichmentQueue(BaseQueue):
    queue_name = "stock_asset_enrichment"
    kind = "stock_asset_enrichment"

    def enqueue_asset_enrichment(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        asset_id: UUID,
        refresh_key: str = "initial",
        job_payload: str = "{}",
    ) -> EnqueuedJob:
        normalized_refresh_key = str(refresh_key).strip()
        if not normalized_refresh_key:
            raise ValueError("stock asset enrichment refresh_key is required")
        refresh_digest = hashlib.sha256(normalized_refresh_key.encode("utf-8")).hexdigest()[:24]
        return self._enqueue(
            job_id=":".join(
                str(part)
                for part in (
                    "stock-asset-enrichment",
                    project_id,
                    asset_id,
                    workspace_id,
                    refresh_digest,
                )
            ),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=asset_id,
            render_engine=job_payload,
        )


class RenderQueue(BaseQueue):
    queue_name = "render"
    kind = "render"

    @staticmethod
    def job_id_for_render(
        *,
        render_job_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
        priority: int = 0,
    ) -> str:
        return ":".join(
            str(part)
            for part in (
                "render",
                project_id,
                render_job_id,
                workspace_id,
                f"p{priority}" if priority else None,
            )
            if part is not None
        )

    def enqueue_render(
        self,
        *,
        render_job_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
        priority: int = 0,
        render_engine: str = "ffmpeg_basic",
        compute_tier: str = "cpu",
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self.job_id_for_render(
                render_job_id=render_job_id,
                project_id=project_id,
                workspace_id=workspace_id,
                priority=priority,
            ),
            priority=priority,
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=render_job_id,
            render_engine=render_engine,
            queue_name=(
                "render_remotion"
                if render_engine == "remotion_premium"
                else "render_gpu"
                if compute_tier == "gpu"
                else self.queue_name
            ),
        )


class PublishQueue(BaseQueue):
    queue_name = "publish"
    kind = "publish"

    def enqueue_publish(
        self,
        *,
        publish_job_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("publish", publish_job_id),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=publish_job_id,
        )


class ScaleQueue(BaseQueue):
    queue_name = "intelligence"
    kind = "phase5"

    def enqueue_resource(
        self,
        *,
        resource_id: UUID,
        workspace_id: UUID,
        project_id: UUID | None,
        resource_kind: str | None = None,
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("phase5", resource_id),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=resource_id,
            kind="batch_plan" if resource_kind == "batch" else self.kind,
            queue_name=(
                "audio_demucs"
                if resource_kind == "stem_separation"
                else "provider_calls"
                if resource_kind in {"generated_broll", "model_training", "style_profile"}
                else "planning"
                if resource_kind == "batch"
                else self.queue_name
            ),
        )


class MusicQueue(BaseQueue):
    queue_name = "audio"
    kind = "music_selection"

    def enqueue_music_selection(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        edit_plan_id: UUID,
        job_payload: str = "{}",
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("music", project_id, edit_plan_id, workspace_id),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=edit_plan_id,
            render_engine=job_payload,
        )


class EffectsQueue(BaseQueue):
    queue_name = "render"
    kind = "effects_config"

    def enqueue_effects_config(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        edit_plan_id: UUID | None,
        job_payload: str = "{}",
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("effects", project_id, edit_plan_id or "no-plan", workspace_id),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=edit_plan_id,
            render_engine=job_payload,
        )


class RegenerationQueue(BaseQueue):
    queue_name = "planning"
    kind = "layer_regeneration"

    def enqueue_regeneration(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        control_settings_id: UUID,
        job_payload: str = "{}",
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=":".join(
                str(part) for part in ("regenerate", project_id, control_settings_id, workspace_id)
            ),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=control_settings_id,
            render_engine=job_payload,
        )
