"""Neutral speaker-diarization provider over any ``audio.diarization`` route.

Task 17 ports the vendor-specific diarization wrapper (the archived
degrade-instead-of-raise provider, deleted in Task 19) to a provider-neutral
module. :class:`GatewayDiarizationProvider` has the same audio-intelligence
contract (``diarize(audio_path=...)`` and the ``SpeakerDiarization`` output
shape) and the same *degrade-instead-of-raise* behaviour, but it is bound to no
vendor: it drives whatever ``audio.diarization`` route the gateway carries — a
registry ``async_job`` or ``generic_http`` template route, a local GPU route, or
(until Task 19) the still-present vendor route.

Two behaviours matter for parity:

* **Degrade, never raise.** A provider outage, budget refusal or malformed
  response returns a degraded result (``degraded: true``,
  ``heuristic_fallback: true``) instead of failing transcription, exactly as the
  archived wrapper did. Audio intelligence then keeps its transcript-based
  speaker estimate.
* **No route → heuristic.** When the gateway carries no ``audio.diarization``
  route, the provider returns the ``heuristic_single_speaker_v1`` degraded
  result (plan Section 3 / Task 17 no-route behaviour) without contacting any
  provider.

Worker-retry safety (plan invariant 6, Task 16B resume): the provider passes a
**stable idempotency key** derived from the upload identity (the audio file's
size and content hash) in the :class:`ModelCallContext`. An ``async_job`` route
reserves its operation under that key, so a worker retry after the commit step
resumes polling the recorded job instead of resubmitting and paying twice.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from app.services.media_probe import audio_duration_seconds
from app.services.model_gateway import (
    _ACTIVE_CALL_CONTEXT,
    DiarizationOutput,
    DiarizationRequest,
    ModelCallContext,
    ModelGateway,
    ModelGatewayError,
    ModelOperation,
    ModelProvidersExhausted,
)
from app.services.provider_resilience import (
    ProviderCallFailure,
    classify_provider_exception,
)

logger = logging.getLogger(__name__)

# The transcript heuristic that stands in when no route is configured
# (plan Section 3, ``apps/api/app/services/audio_intelligence.py``).
HEURISTIC_PROVIDER = "heuristic_single_speaker_v1"

# How many bytes of the audio file feed the idempotency hash. A fixed prefix
# plus the total size is enough to key one upload stably without reading a huge
# file into memory.
_IDEMPOTENCY_HASH_BYTES = 1024 * 1024


def _idempotency_key(audio_path: Path) -> str | None:
    """A stable key for one upload, derived from the file size and a content hash.

    The same source file always yields the same key, so a worker retry after the
    commit step reserves the *same* ``inference_operations`` row and resumes
    polling instead of resubmitting (plan Task 16B resume). Returns ``None`` when
    the file cannot be read, so the caller degrades rather than resubmitting under
    a fresh random key.
    """
    try:
        size = audio_path.stat().st_size
        digest = hashlib.sha256()
        digest.update(str(size).encode("ascii"))
        with audio_path.open("rb") as handle:
            digest.update(handle.read(_IDEMPOTENCY_HASH_BYTES))
    except OSError:
        return None
    return f"diarization-{digest.hexdigest()}"


class GatewayDiarizationProvider:
    """Audio-intelligence diarization over any gateway ``audio.diarization`` route."""

    provider_name = "gateway_diarization"
    remote = True

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        model: str,
        model_revision: str,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.model_revision = model_revision

    def check_availability(self) -> tuple[bool, str]:
        return (
            (True, "remote:gateway_diarization") if self._has_route() else (False, "route_missing")
        )

    def _has_route(self) -> bool:
        return any(
            ModelOperation.AUDIO_DIARIZATION in route.operations for route in self.gateway.routes
        )

    def diarize(self, *, audio_path: Path) -> dict[str, Any]:
        if not self._has_route():
            # No route: the transcript heuristic stands in (plan Section 3). The
            # heuristic speaker labels themselves are computed later by
            # audio_intelligence from the transcript; here we only mark the ML
            # result unavailable so that merge keeps the estimate.
            return self._degraded("model_route_unavailable")
        duration_s = audio_duration_seconds(audio_path)
        if duration_s is None:
            return self._degraded("duration_unknown")
        idempotency_key = _idempotency_key(audio_path)
        if idempotency_key is None:
            return self._degraded("duration_unknown")
        try:
            result = self.gateway.invoke(
                DiarizationRequest(audio_path=audio_path, duration_s=duration_s),
                context=self._call_context(idempotency_key),
            )
        except (ModelGatewayError, ProviderCallFailure) as exc:
            code = _failure_code(exc)
            logger.warning(
                "gateway_diarization_degraded",
                extra={"error_code": code, "model": self.model},
            )
            return self._degraded(code)
        if not isinstance(result.output, DiarizationOutput):
            raise TypeError("diarization route returned the wrong typed output")
        return {
            "provider": self.provider_name,
            "model": self.model,
            "speaker_count": result.output.speaker_count,
            "segments": [
                {
                    "speaker": segment.speaker,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "segment_index": index,
                }
                for index, segment in enumerate(result.output.segments)
            ],
            "confidence": None,
        }

    def _call_context(self, idempotency_key: str) -> ModelCallContext:
        # Attribute the call to the enclosing audio job's workspace/project (the
        # diarize call is nested inside the audio-intelligence gateway call), and
        # carry the stable idempotency key so an async route is resumable.
        active = _ACTIVE_CALL_CONTEXT.get()
        return ModelCallContext(
            workspace_id=active.workspace_id if active is not None else None,
            project_id=active.project_id if active is not None else None,
            idempotency_key=idempotency_key,
        )

    def _degraded(self, reason: str) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "speaker_count": 0,
            "segments": [],
            "confidence": None,
            "degraded": True,
            "status": f"unavailable:{reason}",
            # Keep the transcript-based estimate instead of an empty result.
            "heuristic_fallback": True,
        }


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, ModelProvidersExhausted) and exc.failures:
        return str(exc.failures[0].get("code") or exc.code)
    if isinstance(exc, ModelGatewayError):
        return exc.code
    return classify_provider_exception(exc).code
