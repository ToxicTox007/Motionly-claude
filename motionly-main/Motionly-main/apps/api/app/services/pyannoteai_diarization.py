"""Remote speaker diarization through the pyannoteAI API.

Flow (https://docs.pyannote.ai): reserve a temporary ``media://`` location and
upload the mono speech track to its pre-signed URL, submit a ``/v1/diarize`` job
for that location, then poll ``/v1/jobs/{id}`` until it finishes. pyannoteAI
deletes uploads within 48 hours and job output after 24 hours, and does not
train on customer audio.

``PyannoteAIDiarizationProvider`` is the audio-intelligence provider: it has the
same ``diarize(audio_path=...)`` contract and output shape as the local pyannote
provider, and returns the existing ``degraded`` result instead of raising, so a
provider outage never fails transcription.
"""

from __future__ import annotations

import logging
import time
import wave
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.services.media_probe import probe_duration_seconds
from app.services.model_gateway import (
    AdapterResult,
    DiarizationOutput,
    DiarizationRequest,
    DiarizationSegmentOutput,
    ModelGateway,
    ModelGatewayError,
    ModelOperation,
    ModelProvidersExhausted,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    diarization_billable_seconds,
    inherited_call_context,
)
from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderErrorInfo,
    classify_provider_exception,
    classify_provider_status,
)

logger = logging.getLogger(__name__)

PYANNOTEAI_BASE_URL = "https://api.pyannote.ai/v1"
PYANNOTEAI_ENDPOINT_HOST = "api.pyannote.ai"
# pyannoteAI bills successful jobs by audio duration with a 20-second minimum.
PYANNOTEAI_MINIMUM_BILLABLE_SECONDS = 20
# Hosted model families and their versions; the route revision pins one.
PYANNOTEAI_MODEL_REVISIONS = {
    "precision-3": "pyannoteai-precision-v3",
    "precision-2": "pyannoteai-precision-v2",
    "community-1": "pyannoteai-community-v1",
}
_TERMINAL_FAILURES = {"failed", "canceled"}
_RUNNING = {"pending", "created", "running"}


def pyannoteai_model_revision(model: str) -> str:
    """Immutable route revision for a hosted model, e.g. ``pyannoteai-precision-v3``."""
    if model not in PYANNOTEAI_MODEL_REVISIONS:
        raise ValueError(
            f"pyannoteAI model must be one of {', '.join(sorted(PYANNOTEAI_MODEL_REVISIONS))}"
        )
    return PYANNOTEAI_MODEL_REVISIONS[model]


def audio_duration_seconds(path: Path) -> float | None:
    """Duration of a WAV file from its header, falling back to ffprobe."""
    try:
        with wave.open(str(path), "rb") as audio:
            rate = audio.getframerate()
            frames = audio.getnframes()
        if rate > 0 and frames > 0:
            return frames / rate
        return None
    except (wave.Error, EOFError, OSError):
        return probe_duration_seconds(path)


class PyannoteAIDiarizationAdapter:
    """Model-gateway adapter for ``audio.diarization`` on pyannoteAI."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = PYANNOTEAI_BASE_URL,
        poll_interval_seconds: float = 5.0,
        max_poll_interval_seconds: float = 30.0,
        timeout_seconds: float = 900.0,
        request_timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("pyannoteAI API key is required")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("pyannoteAI base_url must be an absolute HTTPS URL")
        if poll_interval_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("pyannoteAI poll interval and timeout must be positive")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval_seconds
        self._max_poll_interval = max(poll_interval_seconds, max_poll_interval_seconds)
        self._timeout = timeout_seconds
        self._request_timeout = request_timeout_seconds
        self._transport = transport
        self._sleep = sleep
        self._clock = clock

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        if not isinstance(request, DiarizationRequest):
            raise TypeError("pyannoteAI adapter requires DiarizationRequest")
        # A random key: no user, project, or file name is shared with the provider.
        media_url = f"media://motionly-{uuid4().hex}"
        with httpx.Client(
            timeout=httpx.Timeout(self._request_timeout, connect=10.0),
            transport=self._transport,
            trust_env=False,
        ) as client:
            upload_url = self._reserve_upload(client, media_url)
            self._upload(client, upload_url, request.audio_path)
            job_id = self._submit(client, media_url, route.model)
            output = self._wait_for_output(client, job_id)
        segments = _parse_segments(output)
        raw_response: dict[str, object] = {"jobId": job_id, "model": route.model}
        if isinstance(output.get("warning"), str):
            raw_response["warning"] = output["warning"]
        return AdapterResult(
            output=DiarizationOutput(segments=segments, raw_response=raw_response),
            usage=ModelUsage(input_units=diarization_billable_seconds(request)),
            provider_request_id=job_id,
        )

    def _reserve_upload(self, client: httpx.Client, media_url: str) -> str:
        payload = self._request(client, "POST", "media/input", json={"url": media_url})
        upload_url = payload.get("url")
        if not isinstance(upload_url, str) or urlsplit(upload_url).scheme != "https":
            raise _malformed()
        return upload_url

    def _upload(self, client: httpx.Client, upload_url: str, audio_path: Path) -> None:
        try:
            with audio_path.open("rb") as audio_file:
                # Pre-signed storage URL: it carries its own authorization, so the
                # API key is never sent to the storage host.
                response = client.put(
                    upload_url,
                    content=audio_file,
                    headers={"content-type": "application/octet-stream"},
                )
        except OSError as exc:
            raise ProviderCallFailure(
                provider="pyannoteai",
                error_info=ProviderErrorInfo(
                    "provider_request_rejected",
                    False,
                    fallback_eligible=False,
                    circuit_worthy=False,
                    billable=False,
                ),
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderCallFailure(
                provider="pyannoteai",
                error_info=classify_provider_exception(exc),
            ) from None
        if response.status_code >= 400:
            raise ProviderCallFailure(
                provider="pyannoteai",
                error_info=classify_provider_status(response.status_code),
            )

    def _submit(self, client: httpx.Client, media_url: str, model: str) -> str:
        payload = self._request(
            client,
            "POST",
            "diarize",
            json={"url": media_url, "model": model},
        )
        job_id = payload.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            raise _malformed()
        return job_id

    def _wait_for_output(self, client: httpx.Client, job_id: str) -> Mapping[str, object]:
        deadline = self._clock() + self._timeout
        interval = self._poll_interval
        while True:
            try:
                payload = self._request(client, "GET", f"jobs/{job_id}")
            except ProviderCallFailure as exc:
                # The job keeps running remotely; transient polling errors (for
                # example rate limiting) only delay the result.
                if not exc.error_info.retryable:
                    raise
            else:
                status = payload.get("status")
                if status == "succeeded":
                    output = payload.get("output")
                    if not isinstance(output, Mapping):
                        raise _malformed()
                    return output
                if status in _TERMINAL_FAILURES:
                    raise ProviderCallFailure(
                        provider="pyannoteai",
                        error_info=ProviderErrorInfo(
                            "provider_job_failed",
                            False,
                            fallback_eligible=False,
                            # Only successful jobs are billed.
                            billable=False,
                        ),
                        message=f"pyannoteAI diarization ended with status {status}.",
                    )
                if status not in _RUNNING:
                    raise _malformed()
            if self._clock() + interval > deadline:
                raise ProviderCallFailure(
                    provider="pyannoteai",
                    error_info=ProviderErrorInfo("provider_timeout", True),
                    message="pyannoteAI did not finish diarization in time.",
                )
            self._sleep(interval)
            interval = min(self._max_poll_interval, interval * 1.5)

    def _request(
        self,
        client: httpx.Client,
        method: str,
        operation: str,
        *,
        json: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            response = client.request(
                method,
                f"{self._base_url}/{operation}",
                json=dict(json) if json is not None else None,
                headers={
                    "accept": "application/json",
                    "authorization": f"Bearer {self._api_key}",
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderCallFailure(
                provider="pyannoteai",
                error_info=classify_provider_exception(exc),
            ) from None
        if response.status_code >= 400:
            raise ProviderCallFailure(
                provider="pyannoteai",
                error_info=classify_provider_status(response.status_code),
            )
        try:
            payload = response.json()
        except ValueError:
            raise _malformed() from None
        if not isinstance(payload, dict):
            raise _malformed()
        return payload


def _parse_segments(output: Mapping[str, object]) -> tuple[DiarizationSegmentOutput, ...]:
    raw_segments = output.get("diarization")
    if not isinstance(raw_segments, list):
        raise _malformed()
    segments: list[DiarizationSegmentOutput] = []
    for item in raw_segments:
        if not isinstance(item, Mapping):
            raise _malformed()
        try:
            segment = DiarizationSegmentOutput(
                speaker=str(item["speaker"]),
                start_ms=round(float(item["start"]) * 1000),
                end_ms=round(float(item["end"]) * 1000),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise _malformed() from exc
        segments.append(segment)
    return tuple(sorted(segments, key=lambda segment: (segment.start_ms, segment.end_ms)))


def _malformed() -> ProviderCallFailure:
    return ProviderCallFailure(
        provider="pyannoteai",
        error_info=ProviderErrorInfo(
            "provider_malformed_response",
            False,
            fallback_eligible=True,
            circuit_worthy=True,
        ),
    )


class PyannoteAIDiarizationProvider:
    """Audio-intelligence diarization backed by the gateway's pyannoteAI route."""

    provider_name = "pyannoteai"
    remote = True

    def __init__(self, *, gateway: ModelGateway, model: str, model_revision: str) -> None:
        self.gateway = gateway
        self.model = model
        self.model_revision = model_revision

    def check_availability(self) -> tuple[bool, str]:
        configured = any(
            ModelOperation.AUDIO_DIARIZATION in route.operations
            for route in self.gateway.routes
        )
        return (True, "remote:pyannoteai") if configured else (False, "route_missing")

    def diarize(self, *, audio_path: Path) -> dict[str, Any]:
        duration_s = audio_duration_seconds(audio_path)
        if duration_s is None:
            return self._degraded("duration_unknown")
        try:
            result = self.gateway.invoke(
                DiarizationRequest(
                    audio_path=audio_path,
                    duration_s=duration_s,
                    minimum_billable_s=PYANNOTEAI_MINIMUM_BILLABLE_SECONDS,
                ),
                # Attribute the call to the transcription job's workspace/project.
                context=inherited_call_context(),
            )
        except (ModelGatewayError, ProviderCallFailure) as exc:
            code = _failure_code(exc)
            logger.warning(
                "pyannoteai_diarization_degraded",
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
