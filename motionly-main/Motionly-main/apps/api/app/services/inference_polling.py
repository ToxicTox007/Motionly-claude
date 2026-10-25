"""Non-blocking async inference polling engine (plan Task 28, Section 2.5/2.10).

Tier 1's ``async_job`` adapter runs in **blocking** mode: it submits, then
busy-polls until the deadline inside one worker slot. Long video jobs cannot
afford to hold a slot for minutes, so Task 28 adds a **non-blocking** mode whose
polls are re-enqueued on a delay (plan Section 2.10 "non-blocking async mode ...
re-enqueues delayed polls so long video jobs do not hold a worker").

This module is the engine side of that mode. It owns the operation lifecycle and
leaves transport to the ``async_job`` adapter:

* :meth:`InferencePollingEngine.submit` reserves a persisted operation, runs the
  adapter's steps through the commit boundary, and returns the operation plus a
  :class:`PollHandle`. It never blocks on the provider.
* :meth:`InferencePollingEngine.poll_once` advances exactly one poll for a
  persisted operation and returns a :class:`PollResult`: ``pending`` with a
  ``retry_after_s`` (the caller re-enqueues a delayed poll) or a terminal
  :class:`~app.services.model_gateway.AdapterResult`.
* A worker that resumes after a restart calls :meth:`poll_once` with the stored
  operation id. Because the operation is already past ``reserved``, the engine
  never re-runs the submit steps and never contacts a second route (plan
  invariant 6).

Route resolution is injected. The registry/runtime wave (which owns
``inference_registry`` and ``inference_runtime``) supplies a
:class:`PollTargetResolver` that maps a persisted operation back to its adapter,
route, route config and the typed request to resume. That keeps this engine
decoupled from the registry this wave does not own.

The video caller hook (plan Task 28, documented for ``scale.py``/``main.py``,
which this wave does not edit): a submit path calls :meth:`submit`, stores the
returned ``operation.id`` on its domain record, and enqueues a first poll job on
the ``provider_calls`` queue with ``delay_seconds=handle.first_delay_s``. Each
poll job calls :meth:`poll_once`; on a ``pending`` result it re-enqueues itself
with ``delay_seconds=result.retry_after_s``; on a terminal result it writes the
output and stops. The worker queue's additive ``delay_seconds`` support
(``apps/worker/src/queue-bridge.mjs``) carries the delay to BullMQ.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.services.async_job_adapter import AsyncJobAdapter, AsyncJobRouteConfig
from app.services.inference_operations import (
    InferenceOperation,
    InferenceOperationRepository,
    OperationState,
    route_snapshot,
)
from app.services.model_gateway import (
    AdapterResult,
    DiarizationRequest,
    ModelRoute,
    StemSeparationRequest,
    VideoGenerationSubmitRequest,
    operation_for_request,
)

logger = logging.getLogger(__name__)

# The request types the non-blocking engine resumes. Video generation submits
# are the primary caller; diarization/stems can also run non-blocking.
NonBlockingRequest = DiarizationRequest | StemSeparationRequest | VideoGenerationSubmitRequest


@dataclass(frozen=True)
class PollTarget:
    """Everything needed to submit and poll one operation's route.

    The resolver returns this for an operation id. The ``adapter`` is the
    configured :class:`AsyncJobAdapter`; ``route`` and ``config`` are the exact
    route snapshot's live objects; ``request`` is the typed request to resume.
    """

    adapter: AsyncJobAdapter
    route: ModelRoute
    config: AsyncJobRouteConfig
    request: DiarizationRequest | StemSeparationRequest


# A resolver maps an operation to its poll target, or ``None`` when the route is
# gone. It is injected by the registry/runtime wave.
PollTargetResolver = Callable[[InferenceOperation], PollTarget | None]


@dataclass(frozen=True)
class PollHandle:
    """A durable handle the caller stores to drive the delayed poll loop."""

    operation_id: UUID
    route_id: str
    first_delay_s: float


@dataclass(frozen=True)
class PollResult:
    """The outcome of one :meth:`InferencePollingEngine.poll_once`."""

    state: OperationState
    pending: bool
    retry_after_s: float = 0.0
    result: AdapterResult | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.CANCELLED,
        }


class RouteUnavailableError(RuntimeError):
    """The persisted operation's route can no longer be resolved.

    A status poll is never re-routed (plan Section 2.5); if the original route is
    gone the operation stays non-terminal and the caller surfaces the condition.
    """


class InferencePollingEngine:
    """Engine for non-blocking (delayed-poll) async operations (Task 28)."""

    def __init__(
        self,
        *,
        operations_repository: InferenceOperationRepository,
        resolver: PollTargetResolver,
    ) -> None:
        self._operations = operations_repository
        self._resolve = resolver

    # -- submit -------------------------------------------------------------
    def submit(
        self,
        *,
        target: PollTarget,
        workspace_id: UUID,
        project_id: UUID | None,
        idempotency_key: str,
    ) -> tuple[InferenceOperation, PollHandle]:
        """Reserve an operation, run the steps to commit, and return a handle.

        Idempotent: the same ``(workspace_id, idempotency_key)`` returns the
        existing operation and never re-runs the steps. The returned handle
        carries the first poll delay the caller enqueues.
        """
        route = target.route
        config = target.config
        snapshot = route_snapshot(
            adapter_id=route.adapter,
            adapter_type="async_job",
            base_url=route.endpoint_host or "",
            model=route.model,
            model_revision=route.model_revision,
            mapping_version=route.capability_id or route.capability.value,
            secret_ref_name=None,
        )
        operation, created = self._operations.reserve(
            workspace_id=workspace_id,
            project_id=project_id,
            capability=operation_for_request(target.request).value,
            idempotency_key=idempotency_key,
            route_id=route.route_id,
            model=route.model,
            model_revision=route.model_revision,
            route_snapshot=snapshot,
        )
        if created and operation.state == OperationState.RESERVED:
            operation = target.adapter.submit_async(
                route=route,
                request=target.request,
                config=config,
                operation=operation,
            )
        else:
            # A resubmit of an existing operation never re-runs steps.
            operation = self._operations.get(operation.id)
        return operation, PollHandle(
            operation_id=operation.id,
            route_id=route.route_id,
            first_delay_s=_first_delay_s(config),
        )

    # -- poll ---------------------------------------------------------------
    def poll_once(self, operation_id: UUID) -> PollResult:
        """Advance one poll on a persisted operation and return its outcome.

        Uses the persisted operation as the source of truth: it resolves the
        original route snapshot, never resubmits, and never contacts a second
        route. A terminal operation returns immediately without a provider call.
        """
        operation = self._operations.get(operation_id)
        if operation.is_terminal:
            return PollResult(state=operation.state, pending=False)
        if operation.state == OperationState.RESERVED:
            # Not yet submitted: the caller must call submit() first. Treat as a
            # transient pending so a stray early poll simply retries.
            return PollResult(state=operation.state, pending=True, retry_after_s=0.0)

        target = self._resolve(operation)
        if target is None:
            raise RouteUnavailableError(
                f"operation {operation_id} route {operation.provider_route_id!r} is unavailable"
            )
        outcome = target.adapter.poll_once_async(
            route=target.route,
            request=target.request,
            config=target.config,
            operation=operation,
        )
        refreshed = self._operations.get(operation_id)
        if outcome.pending:
            return PollResult(
                state=refreshed.state,
                pending=True,
                retry_after_s=outcome.retry_after_s,
            )
        return PollResult(state=refreshed.state, pending=False, result=outcome.result)


def _first_delay_s(config: AsyncJobRouteConfig) -> float:
    """The first poll delay: the route's poll interval (the mapping may override)."""
    poll = config.mapping.get("poll")
    if isinstance(poll, dict):
        interval = poll.get("interval_s")
        if isinstance(interval, (int, float)) and not isinstance(interval, bool) and interval > 0:
            return float(interval)
    return config.poll_interval_s
