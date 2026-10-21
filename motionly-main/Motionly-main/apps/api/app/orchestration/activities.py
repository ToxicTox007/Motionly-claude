from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.api.errors import ApiError
from app.orchestration.contracts import (
    EnqueueStepRequest,
    EnqueueStepResult,
    assert_identifier_only_history,
    job_id_for,
)
from app.services.queue import BullMQQueueRegistry, EnqueuedJob, QueueRegistry


class QueueRegistryProtocol(Protocol):
    def get_job(self, job_id: str, *, queue_name: str | None = None) -> EnqueuedJob | None: ...

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
    ) -> EnqueuedJob: ...


class WorkflowRepositoryProtocol(Protocol):
    def prepare_step(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        workflow_run_id: UUID,
        step_key: str,
        step_kind: str,
        input_hash: str,
        max_attempts: int,
        bullmq_job_id: str,
        resource_id: UUID | None = None,
    ) -> object: ...


class BullMQActivities:
    def __init__(
        self,
        registry: QueueRegistry | BullMQQueueRegistry | QueueRegistryProtocol,
        workflow_repository: WorkflowRepositoryProtocol | None = None,
    ) -> None:
        self.registry = registry
        self.workflow_repository = workflow_repository

    @activity.defn(name="enqueue_bullmq_step")
    async def enqueue_bullmq_step(self, request: EnqueueStepRequest) -> EnqueueStepResult:
        assert_identifier_only_history(request)
        try:
            return await asyncio.to_thread(self._enqueue, request)
        except ApplicationError:
            raise
        except ApiError as error:
            # Surface the real database/transport cause instead of masking it
            # as "temporarily unavailable". A transport conflict will never
            # resolve by retrying, so it is raised non-retryable with its real
            # error code as the ApplicationError type.
            #
            # workflow_not_found means the run (and its project) was deleted out
            # from under an in-flight workflow. Retrying would produce a
            # WORKFLOW_NOT_FOUND storm, so it is terminal and quiet: the
            # dispatcher is cancelling this Temporal workflow via the durable
            # cancellation request written by the project-delete trigger. Log it
            # at info, never error.
            non_retryable_codes = {
                "workflow_step_transport_conflict",
                "workflow_not_found",
            }
            if error.code == "workflow_not_found":
                activity.logger.info(
                    "workflow_enqueue_skipped_deleted_run",
                    extra={
                        "workflow_run_id": request.workflow_run_id,
                        "step_key": request.step_key,
                    },
                )
            raise ApplicationError(
                error.message,
                type=error.code,
                non_retryable=error.code in non_retryable_codes,
            ) from None
        except Exception:
            raise ApplicationError(
                "BullMQ identifier enqueue is temporarily unavailable.",
                type="bullmq_enqueue_unavailable",
            ) from None

    def _enqueue(self, request: EnqueueStepRequest) -> EnqueueStepResult:
        job_id = job_id_for(request)
        if self.workflow_repository is not None:
            self.workflow_repository.prepare_step(
                workspace_id=UUID(request.workspace_id),
                project_id=UUID(request.project_id),
                workflow_run_id=UUID(request.workflow_run_id),
                step_key=request.step_key,
                step_kind=request.step_kind,
                input_hash=request.input_hash,
                max_attempts=request.max_attempts,
                bullmq_job_id=job_id,
                resource_id=UUID(request.resource_id) if request.resource_id else None,
            )
        existing = self.registry.get_job(job_id, queue_name=request.queue_name)
        if existing is not None:
            self._assert_same_job(existing, request)
            return self._result(existing, request)
        job = self.registry.enqueue(
            job_id=job_id,
            kind=request.job_kind,
            queue_name=request.queue_name,
            workspace_id=UUID(request.workspace_id),
            project_id=UUID(request.project_id),
            resource_id=UUID(request.resource_id) if request.resource_id else None,
            workflow_run_id=UUID(request.workflow_run_id),
            workflow_step_id=(UUID(request.workflow_step_id) if request.workflow_step_id else None),
            step_key=request.step_key,
            step_kind=request.step_kind,
            attempt_key=request.attempt_key,
            workflow_attempt_number=request.attempt_number,
            workflow_max_attempts=request.max_attempts,
            input_hash=request.input_hash,
            next_run_state=request.next_run_state,
            max_attempts=1,
            render_engine=(
                "remotion_premium" if request.queue_name == "render_remotion" else "ffmpeg_basic"
            ),
        )
        self._assert_same_job(job, request)
        return self._result(job, request)

    @staticmethod
    def _assert_same_job(job: EnqueuedJob, request: EnqueueStepRequest) -> None:
        expected = (
            request.queue_name,
            request.job_kind,
            request.step_kind,
            request.workflow_run_id,
            request.workflow_step_id,
            request.step_key,
            request.attempt_key,
            request.attempt_number,
            request.max_attempts,
            1,
            request.input_hash,
            request.next_run_state,
        )
        actual = (
            job.queue_name,
            job.kind,
            job.step_kind,
            str(job.workflow_run_id) if job.workflow_run_id else None,
            str(job.workflow_step_id) if job.workflow_step_id else None,
            job.step_key,
            job.attempt_key,
            job.workflow_attempt_number,
            job.workflow_max_attempts,
            job.max_attempts,
            job.input_hash,
            job.next_run_state,
        )
        if actual != expected:
            raise ApplicationError(
                "Deterministic BullMQ job identity conflicts with an existing job.",
                type="bullmq_job_identity_conflict",
                non_retryable=True,
            )

    @staticmethod
    def _result(job: EnqueuedJob, request: EnqueueStepRequest) -> EnqueueStepResult:
        result = EnqueueStepResult(
            job_id=job.id,
            queue_name=job.queue_name,
            workflow_run_id=request.workflow_run_id,
            step_key=request.step_key,
            attempt_key=request.attempt_key,
            input_hash=request.input_hash,
        )
        assert_identifier_only_history(result)
        return result


def deterministic_job_id(request: EnqueueStepRequest) -> str:
    return job_id_for(request)
