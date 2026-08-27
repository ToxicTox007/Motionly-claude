from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.api.errors import ApiError
from app.core.config import Settings
from app.core.logging import redact_string
from app.orchestration.client import (
    TemporalConnectionConfig,
    TemporalOrchestrationClient,
    connect_temporal_client,
)
from app.orchestration.contracts import (
    ApprovalGateSignal,
    StepOutcomeSignal,
    TemporalStartResult,
    WorkflowStartInput,
    assert_identifier_only_history,
    signal_from_outbox,
    workflow_start_for_run,
)
from app.repositories.workflows import (
    OutboxRecord,
    SupabaseWorkflowRepository,
    WorkflowRunRecord,
)

logger = logging.getLogger(__name__)


class WorkflowRepositoryProtocol(Protocol):
    async def claim_outbox_async(
        self, *, lease_owner: str, limit: int, lease_seconds: int
    ) -> list[OutboxRecord]: ...

    async def get_workflow_async(
        self, *, workflow_run_id: object, workspace_id: object, project_id: object
    ) -> WorkflowRunRecord: ...

    async def ack_outbox_async(self, **values: object) -> OutboxRecord: ...

    async def fail_outbox_async(self, **values: object) -> OutboxRecord: ...


class TemporalClientProtocol(Protocol):
    async def start_workflow(
        self, *, workflow_id: str, input: WorkflowStartInput
    ) -> TemporalStartResult: ...

    async def signal_workflow(
        self, *, workflow_id: str, signal: StepOutcomeSignal | ApprovalGateSignal
    ) -> str: ...


@dataclass(frozen=True)
class DispatchBatchResult:
    claimed: int = 0
    dispatched: int = 0
    failed: int = 0
    stale: int = 0


class OutboxDispatcher:
    def __init__(
        self,
        *,
        repository: WorkflowRepositoryProtocol,
        temporal: TemporalClientProtocol,
        lease_owner: str,
        batch_size: int = 25,
        lease_seconds: int = 60,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not lease_owner or len(lease_owner) > 160:
            raise ValueError("dispatcher lease_owner is invalid")
        if not 1 <= batch_size <= 100 or not 5 <= lease_seconds <= 900:
            raise ValueError("dispatcher claim bounds are invalid")
        if not 0.1 <= base_backoff_seconds <= max_backoff_seconds <= 3600:
            raise ValueError("dispatcher backoff bounds are invalid")
        self.repository = repository
        self.temporal = temporal
        self.lease_owner = lease_owner
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.base_backoff_seconds = float(base_backoff_seconds)
        self.max_backoff_seconds = float(max_backoff_seconds)
        self.clock = clock or (lambda: datetime.now(UTC))

    async def dispatch_once(self) -> DispatchBatchResult:
        messages = await self.repository.claim_outbox_async(
            lease_owner=self.lease_owner,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        dispatched = failed = stale = 0
        for message in messages:
            result = await self.dispatch_message(message)
            dispatched += result == "dispatched"
            failed += result == "failed"
            stale += result == "stale"
        return DispatchBatchResult(
            claimed=len(messages),
            dispatched=dispatched,
            failed=failed,
            stale=stale,
        )

    async def dispatch_message(self, message: OutboxRecord) -> str:
        if message.lease_token is None:
            raise ValueError("dispatcher requires a fenced outbox lease")
        try:
            if message.destination != "temporal":
                raise ValueError("dispatcher received a non-Temporal outbox message")
            assert_identifier_only_history(message.payload)
            run = await self.repository.get_workflow_async(
                workflow_run_id=message.workflow_run_id,
                workspace_id=message.workspace_id,
                project_id=message.project_id,
            )
            self._assert_message_run(message, run)
            if message.event_type == "workflow.start":
                start = workflow_start_for_run(run)
                delivery = await self.temporal.start_workflow(
                    workflow_id=run.temporal_workflow_id,
                    input=start,
                )
                await self.repository.ack_outbox_async(
                    outbox_id=message.id,
                    lease_token=message.lease_token,
                    transport_message_id=delivery.run_id,
                    temporal_run_id=delivery.run_id,
                )
            else:
                signal = signal_from_outbox(
                    event_type=message.event_type,
                    event_id=message.deduplication_key,
                    payload=message.payload,
                )
                transport_id = await self.temporal.signal_workflow(
                    workflow_id=run.temporal_workflow_id,
                    signal=signal,
                )
                await self.repository.ack_outbox_async(
                    outbox_id=message.id,
                    lease_token=message.lease_token,
                    transport_message_id=transport_id,
                    temporal_run_id=None,
                )
            return "dispatched"
        except ApiError as exc:
            if exc.code == "outbox_stale_claim":
                logger.info("temporal_dispatch_stale_claim", extra={"outbox_id": str(message.id)})
                return "stale"
            return await self._record_failure(message, exc)
        except Exception as exc:
            return await self._record_failure(message, exc)

    async def _record_failure(self, message: OutboxRecord, exc: Exception) -> str:
        assert message.lease_token is not None
        retry_at = self.clock() + timedelta(seconds=self.backoff_seconds(message.attempt_count))
        error_code = _error_code(exc)
        error_message = redact_string(str(exc) or "Temporal dispatch failed.")[:500]
        try:
            await self.repository.fail_outbox_async(
                outbox_id=message.id,
                lease_token=message.lease_token,
                error_code=error_code,
                error_message=error_message,
                retry_at=retry_at,
            )
        except ApiError as fence_error:
            if fence_error.code == "outbox_stale_claim":
                logger.info("temporal_dispatch_failure_stale", extra={"outbox_id": str(message.id)})
                return "stale"
            raise
        logger.warning(
            "temporal_dispatch_failed",
            extra={
                "outbox_id": str(message.id),
                "event_type": message.event_type,
                "error_code": error_code,
            },
        )
        return "failed"

    def backoff_seconds(self, attempt_count: int) -> float:
        exponent = max(0, min(attempt_count - 1, 20))
        return min(self.max_backoff_seconds, self.base_backoff_seconds * (2**exponent))

    @staticmethod
    def _assert_message_run(message: OutboxRecord, run: WorkflowRunRecord) -> None:
        payload_run_id = message.payload.get("workflow_run_id")
        if payload_run_id != str(run.id) or message.workflow_run_id != run.id:
            raise ValueError("outbox workflow identity does not match the canonical run")
        if message.workspace_id != run.workspace_id or message.project_id != run.project_id:
            raise ValueError("outbox tenant identity does not match the canonical run")


async def run_dispatcher(
    settings: Settings,
    *,
    once: bool = False,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> DispatchBatchResult | None:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    config = TemporalConnectionConfig.from_settings(settings)
    client = await connect_temporal_client(config)
    temporal = TemporalOrchestrationClient(client, task_queue=config.task_queue)
    repository = SupabaseWorkflowRepository(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
    )
    dispatcher = OutboxDispatcher(
        repository=repository,
        temporal=temporal,
        lease_owner=settings.temporal_dispatcher_id or f"dispatcher-{os.getpid()}",
        batch_size=settings.workflow_dispatcher_batch_size,
        lease_seconds=settings.workflow_dispatcher_lease_seconds,
        base_backoff_seconds=settings.workflow_dispatcher_backoff_initial_seconds,
        max_backoff_seconds=settings.workflow_dispatcher_backoff_max_seconds,
    )
    while True:
        result = await dispatcher.dispatch_once()
        if once:
            return result
        if result.claimed == 0:
            await sleeper(settings.workflow_dispatcher_poll_interval_seconds)


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        return exc.code[:120]
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).lower()
    return f"temporal_{name}"[:120]
