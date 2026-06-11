import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
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


class UpstashQueueRegistry:
    index_key = "motionly:queue:jobs"

    def __init__(self, *, rest_url: str, token: str) -> None:
        self.rest_url = rest_url.rstrip("/")
        self.headers = {"authorization": f"Bearer {token}"}

    @property
    def jobs(self) -> dict[str, EnqueuedJob]:
        job_ids = self._command("SMEMBERS", self.index_key)
        if not isinstance(job_ids, list):
            return {}
        jobs: dict[str, EnqueuedJob] = {}
        for job_id in sorted(str(item) for item in job_ids):
            payload = self._command("GET", self._job_key(job_id))
            if not isinstance(payload, str):
                continue
            job = self._deserialize_job(payload)
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
        existing = self._command("GET", self._job_key(job_id))
        if isinstance(existing, str):
            return self._deserialize_job(existing)
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
        self._store_job(job)
        return job

    def retry(self, job_id: str) -> EnqueuedJob:
        job = self._get_job(job_id)
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
        self._store_job(retried)
        return retried

    def start(self, job_id: str) -> EnqueuedJob:
        job = self._get_job(job_id)
        started = self._copy_job(job, status=QueueJobStatus.RUNNING)
        self._store_job(started)
        return started

    def complete(self, job_id: str) -> EnqueuedJob:
        job = self._get_job(job_id)
        completed = self._copy_job(job, status=QueueJobStatus.SUCCEEDED)
        self._store_job(completed)
        return completed

    def fail(self, job_id: str) -> EnqueuedJob:
        job = self._get_job(job_id)
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
        )
        self._store_job(failed)
        return failed

    def cancel(self, job_id: str) -> EnqueuedJob:
        job = self._get_job(job_id)
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
        )
        self._store_job(canceled)
        return canceled

    def mark_stuck_as_dead_letter(self, *, timeout_seconds: int) -> list[EnqueuedJob]:
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
        moved = []
        for job in self.jobs.values():
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
            )
            self._store_job(dead)
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

    def _get_job(self, job_id: str) -> EnqueuedJob:
        payload = self._command("GET", self._job_key(job_id))
        if not isinstance(payload, str):
            raise KeyError(job_id)
        return self._deserialize_job(payload)

    def _store_job(self, job: EnqueuedJob) -> None:
        self._command("SADD", self.index_key, job.id)
        self._command("SET", self._job_key(job.id), self._serialize_job(job))

    def _command(self, *parts: str) -> object:
        import httpx

        response = httpx.post(
            f"{self.rest_url}/pipeline",
            headers=self.headers,
            json=[[part for part in parts]],
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            return None
        first = payload[0]
        if not isinstance(first, dict):
            return None
        return first.get("result")

    def _serialize_job(self, job: EnqueuedJob) -> str:
        payload = asdict(job)
        payload["status"] = job.status.value
        payload["enqueued_at"] = job.enqueued_at.isoformat()
        for key in ("workspace_id", "project_id", "resource_id"):
            payload[key] = str(payload[key]) if payload[key] else None
        return json.dumps(payload, separators=(",", ":"))

    def _deserialize_job(self, payload: str) -> EnqueuedJob:
        data = json.loads(payload)
        return EnqueuedJob(
            id=str(data["id"]),
            kind=str(data["kind"]),
            queue_name=str(data["queue_name"]),
            priority=int(data["priority"]),
            attempts=int(data["attempts"]),
            max_attempts=int(data["max_attempts"]),
            status=QueueJobStatus(str(data["status"])),
            enqueued_at=datetime.fromisoformat(str(data["enqueued_at"])),
            workspace_id=UUID(str(data["workspace_id"])) if data.get("workspace_id") else None,
            project_id=UUID(str(data["project_id"])) if data.get("project_id") else None,
            resource_id=UUID(str(data["resource_id"])) if data.get("resource_id") else None,
            # Graceful fallback for jobs enqueued before render_engine was added
            render_engine=str(data.get("render_engine") or "ffmpeg_basic"),
        )

    def _job_key(self, job_id: str) -> str:
        return f"motionly:queue:job:{job_id}"

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
        registry: QueueRegistry | UpstashQueueRegistry | None = None,
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
            job_id=f"preprocess:{video_id}",
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
            job_id=f"transcribe:{project_id}:{video_id}:{workspace_id}",
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
            job_id=f"captions:{project_id}:{transcript_id}:{workspace_id}",
            kind="caption_generation",
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=transcript_id,
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
            job_id=f"plan:{project_id}:{transcript_id}:{workspace_id}",
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
            job_id=f"broll:{project_id}:{edit_plan_id}:{workspace_id}",
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
        priority_suffix = f":p{priority}" if priority else ""
        return self._enqueue(
            job_id=f"render:{project_id}:{render_job_id}:{workspace_id}{priority_suffix}",
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
            job_id=f"music:{project_id}:{edit_plan_id}:{workspace_id}",
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
            job_id=f"effects:{project_id}:{edit_plan_id or 'no-plan'}:{workspace_id}",
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
            job_id=f"regenerate:{project_id}:{control_settings_id}:{workspace_id}",
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=control_settings_id,
        )
