"""Fail-closed registry-fingerprint check for the Python inference workers.

Plan Task 6 worker rule: the API and the visual and matte workers must build the
same route catalog from one registry file, and a worker must fail closed on a
mismatch for a capability the job requires.

Design (the most direct form the architecture allows):

* The visual and matte workers are not long-lived heartbeat senders; they are
  stdin/stdout executors spawned per job by ``apps/worker/src/worker.mjs`` (see
  ``app.workers.executor_protocol``). There is no Python heartbeat loop in the
  worker entrypoint to stamp a fingerprint onto, so the check rides on the job
  payload instead.
* When the API enqueues a visual or matte job it may stamp a
  ``registry_fingerprint`` into the payload — the catalog fingerprint restricted
  to the capabilities that job requires
  (:meth:`InferenceRuntime.capability_fingerprint`). The worker builds its own
  :class:`~app.services.inference_runtime.InferenceRuntime` from the same
  registry file and compares the two.
* On a mismatch for a required capability the job fails closed with the error
  code ``inference_registry_mismatch`` instead of running, so a worker that
  booted on a stale registry can never silently serve a job the API planned
  against a different catalog.
* A payload with no ``registry_fingerprint`` is accepted (older enqueuers, and
  dev/test): the fingerprint check is additive and never blocks a job the API
  did not stamp. The API-vs-worker identity is still provable through the shared
  ``registry_version`` and ``catalog_fingerprint`` in tests.

Where a Python process does send a :class:`WorkerHeartbeat` (the operations
layer), ``registry_version`` and ``catalog_fingerprint`` can be added to its
metadata dict; the worker entrypoints here do not send one, so they rely on the
payload check above.
"""

from __future__ import annotations

from app.api.errors import ApiError
from app.services.inference_capabilities import InferenceCapability
from app.services.inference_runtime import InferenceRuntime

# Capabilities the visual-analysis job may invoke through the gateway: the
# manifest itself plus the remote keyframe enrichers.
VISUAL_CAPABILITY_IDS: frozenset[str] = frozenset(
    {
        InferenceCapability.VISION_CAPTION.value,
        InferenceCapability.VISION_OBJECT_DETECTION.value,
        InferenceCapability.EMBEDDINGS_IMAGE.value,
    }
)

# Capabilities the matte job requires.
MATTE_CAPABILITY_IDS: frozenset[str] = frozenset({InferenceCapability.MATTE_SEGMENTATION.value})

REGISTRY_MISMATCH_CODE = "inference_registry_mismatch"


def registry_fingerprint_for(
    runtime: InferenceRuntime | None, capability_ids: frozenset[str]
) -> str | None:
    """Compute the stamp a producer writes into a job payload (plan Task 6).

    The enqueuer (``app.services.matte_jobs``/``visual_jobs``) calls this with
    the SAME per-job capability-set constant the worker checks
    (:data:`MATTE_CAPABILITY_IDS` / :data:`VISUAL_CAPABILITY_IDS`), so producer
    and consumer cannot drift: both read the one constant defined here.

    Returns ``None`` when no runtime is available (a non-API enqueue path): the
    payload is left unstamped and :func:`enforce_registry_fingerprint` accepts it
    (the check is additive). Returns the capability-restricted catalog
    fingerprint otherwise.
    """
    if runtime is None:
        return None
    return runtime.capability_fingerprint(capability_ids)


class InferenceRegistryMismatch(ApiError):
    """The worker's inference catalog differs from the one the API enqueued against."""

    def __init__(self, *, required: str, expected: str, actual: str) -> None:
        super().__init__(
            409,
            REGISTRY_MISMATCH_CODE,
            (
                "The worker's inference registry does not match the one this job "
                "was planned against; refusing to run to avoid serving a stale "
                "catalog."
            ),
            details={"required": required, "expected": expected, "actual": actual},
        )
        self.required = required
        self.expected = expected
        self.actual = actual


def enforce_registry_fingerprint(
    payload: dict[str, object],
    *,
    runtime: InferenceRuntime,
    capability_ids: frozenset[str],
) -> None:
    """Fail closed when the payload's required-capability fingerprint drifts.

    A payload without a ``registry_fingerprint`` is accepted (the check is
    additive). A present fingerprint that differs from the worker's own
    capability-restricted fingerprint raises :class:`InferenceRegistryMismatch`.
    """
    expected = payload.get("registry_fingerprint")
    if not expected:
        return
    actual = runtime.capability_fingerprint(capability_ids)
    if str(expected) != actual:
        raise InferenceRegistryMismatch(
            required=",".join(sorted(capability_ids)),
            expected=str(expected),
            actual=actual,
        )
