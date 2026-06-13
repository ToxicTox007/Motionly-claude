import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import UUID


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
    # Render engine hint stored so the worker can route without a manifest fetch
    render_engine: str = "ffmpeg_basic"


@dataclass(frozen=True)
class QueueSnapshot:
    queue_name: str
    queued: int
    running: int
    succeeded: int
    failed: int
    dead_letter: int
    canceled: int
    oldest_queued_seconds: int | None


class QueueRegistry:
    def __init__(self) -> None:
        self.jobs: dict[str, EnqueuedJob] = {}

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
        max_attempts: int = 3,
        render_engine: str = "ffmpeg_basic",
    ) -> EnqueuedJob:
        job = EnqueuedJob(
            id=job_id,
            kind=kind,
            queue_name=queue_name,
            priority=priority,
            max_attempts=max_attempts,
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=resource_id,
            render_engine=render_engine,
        )
        self.jobs[job.id] = job
        return job

    def retry(self, job_id: str) -> EnqueuedJob:
        job = self.jobs[job_id]
        attempts = job.attempts + 1
        status = (
            QueueJobStatus.DEAD_LETTER
            if attempts >= job.max_attempts
            else QueueJobStatus.QUEUED
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
            render_engine=job.render_engine,
        )
        self.jobs[job_id] = retried
        return retried

    def start(self, job_id: str) -> EnqueuedJob:
        job = self.jobs[job_id]
        started = self._copy_job(job, status=QueueJobStatus.RUNNING)
        self.jobs[job_id] = started
        return started

    def complete(self, job_id: str) -> EnqueuedJob:
        job = self.jobs[job_id]
        completed = self._copy_job(job, status=QueueJobStatus.SUCCEEDED)
        self.jobs[job_id] = completed
        return completed

    def fail(self, job_id: str) -> EnqueuedJob:
        job = self.jobs[job_id]
        attempts = job.attempts + 1
        status = (
            QueueJobStatus.DEAD_LETTER
            if attempts >= job.max_attempts
            else QueueJobStatus.QUEUED
        )
        failed = EnqueuedJob(
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
            render_engine=job.render_engine,
        )
        self.jobs[job_id] = failed
        return failed

    def cancel(self, job_id: str) -> EnqueuedJob:
        job = self.jobs[job_id]
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
            render_engine=job.render_engine,
        )
        self.jobs[job_id] = canceled
        return canceled

    def mark_stuck_as_dead_letter(self, *, timeout_seconds: int) -> list[EnqueuedJob]:
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
                render_engine=job.render_engine,
            )
            self.jobs[job.id] = dead
            moved.append(dead)
        return moved

    def snapshots(self) -> list[QueueSnapshot]:
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
            render_engine=job.render_engine,
        )


class BullMQQueueRegistry:
    def __init__(
        self,
        *,
        redis_url: str,
        bridge_path: str | None = None,
        node_command: str = "node",
        job_attempts: int = 3,
    ) -> None:
        self.redis_url = redis_url
        self.bridge_path = bridge_path or str(self._default_bridge_path())
        self.node_command = node_command
        self.job_attempts = job_attempts

    @staticmethod
    def _default_bridge_path() -> Path:
        current = Path(__file__).resolve()
        candidates = [Path("/app/apps/worker/src/queue-bridge.mjs")]
        candidates.extend(
            parent / "apps" / "worker" / "src" / "queue-bridge.mjs"
            for parent in current.parents
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
        max_attempts: int = 3,
        render_engine: str = "ffmpeg_basic",
    ) -> EnqueuedJob:
        payload = {
            "id": job_id,
            "kind": kind,
            "queue_name": queue_name,
            "priority": priority,
            "workspace_id": str(workspace_id) if workspace_id else None,
            "project_id": str(project_id) if project_id else None,
            "resource_id": str(resource_id) if resource_id else None,
            "max_attempts": max_attempts or self.job_attempts,
            "render_engine": render_engine,
        }
        return self._job_from_bridge(self._bridge("enqueue", payload))

    def retry(self, job_id: str) -> EnqueuedJob:
        return self._job_from_bridge(self._bridge("retry", {"id": job_id}))

    def start(self, job_id: str) -> EnqueuedJob:
        job = self.jobs[job_id]
        return self._copy_job(job, status=QueueJobStatus.RUNNING)

    def complete(self, job_id: str) -> EnqueuedJob:
        job = self.jobs[job_id]
        return self._copy_job(job, status=QueueJobStatus.SUCCEEDED)

    def fail(self, job_id: str) -> EnqueuedJob:
        job = self.jobs[job_id]
        status = (
            QueueJobStatus.DEAD_LETTER
            if job.attempts + 1 >= job.max_attempts
            else QueueJobStatus.FAILED
        )
        return self._copy_job(job, status=status)

    def cancel(self, job_id: str) -> EnqueuedJob:
        return self._job_from_bridge(self._bridge("cancel", {"id": job_id}))

    def mark_stuck_as_dead_letter(self, *, timeout_seconds: int) -> list[EnqueuedJob]:
        return []

    def snapshots(self) -> list[QueueSnapshot]:
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

    def _bridge(self, command: str, payload: dict[str, object]) -> object:
        env = {
            **os.environ,
            "REDIS_URL": self.redis_url,
            "JOB_ATTEMPTS": str(self.job_attempts),
        }
        result = subprocess.run(
            [self.node_command, self.bridge_path, command],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip() or "BullMQ bridge failed"
            )
        return json.loads(result.stdout or "null")

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
            render_engine=str(data.get("render_engine") or "ffmpeg_basic"),
        )

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
            render_engine=job.render_engine,
        )


class BaseQueue:
    queue_name = "default"
    kind = "job"

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
    ) -> EnqueuedJob:
        return self.registry.enqueue(
            job_id=job_id,
            kind=kind or self.kind,
            queue_name=self.queue_name,
            priority=priority,
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=resource_id,
            render_engine=render_engine,
        )

    def _job_id(self, *parts: object) -> str:
        return "--".join(str(part) for part in parts if part is not None)


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
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("captions", project_id, transcript_id, workspace_id),
            kind="caption_generation",
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=transcript_id,
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


class PlanQueue(BaseQueue):
    queue_name = "planning"
    kind = "edit_plan"

    def enqueue_plan(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        transcript_id: UUID,
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("plan", project_id, transcript_id, workspace_id),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=transcript_id,
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
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=":".join(
                str(part) for part in ("broll", project_id, edit_plan_id, workspace_id)
            ),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=edit_plan_id,
        )


class RenderQueue(BaseQueue):
    queue_name = "render"
    kind = "render"

    def enqueue_render(
        self,
        *,
        render_job_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
        priority: int = 0,
        render_engine: str = "ffmpeg_basic",
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=":".join(
                str(part)
                for part in (
                    "render",
                    project_id,
                    render_job_id,
                    workspace_id,
                    f"p{priority}" if priority else None,
                )
                if part is not None
            ),
            priority=priority,
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=render_job_id,
            render_engine=render_engine,
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
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("music", project_id, edit_plan_id, workspace_id),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=edit_plan_id,
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
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=self._job_id("effects", project_id, edit_plan_id or "no-plan", workspace_id),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=edit_plan_id,
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
    ) -> EnqueuedJob:
        return self._enqueue(
            job_id=":".join(
                str(part) for part in ("regenerate", project_id, control_settings_id, workspace_id)
            ),
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=control_settings_id,
        )
