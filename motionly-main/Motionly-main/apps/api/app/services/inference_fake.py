"""Deterministic, provider-neutral fake inference adapter for CI and dev.

The fake implements the ``ModelAdapter`` protocol from ``model_gateway`` and
produces a schema-valid typed output for every request type in the current
``ModelRequest`` union. It never performs any I/O, so it is safe under the
no-network test guard.

Scripted outcomes let a test force the exact failure a real HTTP adapter would
raise. Every outcome is mapped to the same ``ProviderCallFailure`` /
``ProviderErrorInfo`` classification that ``app.services.model_adapters`` and
``app.services.provider_resilience`` produce today, so gateway behaviour
(fallback, circuit opening, billing) matches production without a live call.

Production construction is refused: ``app.core.config`` ``APP_ENV=production``
can never instantiate this adapter, mirroring the Section 2.6 rule that a fake
adapter is a startup error in production.

The next agent (Task 3) adds new typed request classes by adding one entry to
``_DISPATCH`` keyed by the request type; an unknown type raises ``TypeError``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

import httpx

from app.services.model_gateway import (
    AdapterResult,
    AudioIntelligenceOutput,
    AudioIntelligenceRequest,
    AudioOutput,
    AudioRequest,
    DetectedObjectOutput,
    DiarizationOutput,
    DiarizationRequest,
    DiarizationSegmentOutput,
    EmbeddingsOutput,
    EmbeddingsRequest,
    GeneratedArtifact,
    GenerationOperationOutput,
    GenerationOutput,
    GenerationRequest,
    ImageEmbeddingsRequest,
    MatteSegmentationOutput,
    MatteSegmentationRequest,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    PlanningOutput,
    PlanningRequest,
    SoundClassificationOutput,
    SoundClassificationRequest,
    SoundLabelOutput,
    StemSeparationOutput,
    StemSeparationRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    TranscriptWordOutput,
    VideoGenerationPollRequest,
    VideoGenerationSubmitRequest,
    VisionOutput,
    VisionRequest,
    VisualManifestOutput,
    VisualManifestRequest,
)
from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderErrorInfo,
    classify_provider_exception,
    classify_provider_status,
)


class ScriptedOutcome(StrEnum):
    """A scripted result for one fake adapter call."""

    SUCCESS = "success"
    SCHEMA_INVALID = "schema_invalid"
    HTTP_400 = "http_400"
    HTTP_401 = "http_401"
    HTTP_403 = "http_403"
    HTTP_402 = "http_402"
    HTTP_429 = "http_429"
    HTTP_5XX = "http_5xx"
    TIMEOUT_BEFORE_SEND = "timeout_before_send"
    TIMEOUT_AFTER_SEND = "timeout_after_send"


# Outcomes that put a request on the wire (the provider may have been charged).
# ``timeout_after_send`` is the uncertain case the Task 16A ``sent`` flag will
# distinguish; until then the fake records it so tests can assert on it.
_SENT_OUTCOMES = frozenset(
    {
        ScriptedOutcome.SUCCESS,
        ScriptedOutcome.SCHEMA_INVALID,
        ScriptedOutcome.HTTP_400,
        ScriptedOutcome.HTTP_401,
        ScriptedOutcome.HTTP_403,
        ScriptedOutcome.HTTP_402,
        ScriptedOutcome.HTTP_429,
        ScriptedOutcome.HTTP_5XX,
        ScriptedOutcome.TIMEOUT_AFTER_SEND,
    }
)

_HTTP_STATUS_BY_OUTCOME = {
    ScriptedOutcome.HTTP_400: 400,
    ScriptedOutcome.HTTP_401: 401,
    ScriptedOutcome.HTTP_403: 403,
    ScriptedOutcome.HTTP_402: 402,
    ScriptedOutcome.HTTP_429: 429,
    ScriptedOutcome.HTTP_5XX: 503,
}


@dataclass(frozen=True)
class AdapterCallRecord:
    """One recorded fake adapter invocation, for test assertions."""

    route_id: str
    request_type: str
    outcome: ScriptedOutcome
    sent: bool


class ProductionAdapterRejected(RuntimeError):
    """Raised when the fake adapter is constructed in a production app env."""


def _malformed_failure(provider: str) -> ProviderCallFailure:
    """The exact classification a real adapter raises for a malformed response."""
    return ProviderCallFailure(
        provider=provider,
        error_info=ProviderErrorInfo(
            "provider_malformed_response",
            False,
            fallback_eligible=True,
            circuit_worthy=True,
        ),
    )


def _status_failure(provider: str, status_code: int) -> ProviderCallFailure:
    """The exact classification a real HTTP adapter raises for a status code."""
    return ProviderCallFailure(
        provider=provider,
        error_info=classify_provider_status(status_code),
    )


def _timeout_failure(provider: str, *, sent: bool) -> ProviderCallFailure:
    """The exact classification a real adapter raises for an httpx timeout.

    A connect timeout happens before anything leaves the client (``sent=False``),
    so the Task 16A gateway rule may still fall back; a read timeout happens
    after the request has been written (``sent=True``), so a route-affine async
    submit must not fall back. Both classify to ``provider_timeout``.
    """
    request = httpx.Request("POST", f"https://{provider}.invalid/fake")
    exc: httpx.TimeoutException = (
        httpx.ReadTimeout("fake timeout after send", request=request)
        if sent
        else httpx.ConnectTimeout("fake timeout before send", request=request)
    )
    return ProviderCallFailure(
        provider=provider,
        error_info=classify_provider_exception(exc),
    )


@dataclass
class _VideoJob:
    """One in-memory async job, progressed by scripted poll states."""

    states: list[str]
    index: int = 0
    artifact_url: str = "https://fake.invalid/artifacts/generated.mp4"
    operation_id: str = ""


# --- deterministic success builders, keyed by request type ------------------


def _planning_success(
    request: PlanningRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    content = '{"ok": true}' if request.response_format is not None else "fake plan"
    payload: Mapping[str, object] = {
        "choices": [{"message": {"content": content}}],
        "model": route.model,
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }
    return (
        AdapterResult(
            output=PlanningOutput(content=content, raw_response=payload),
            usage=ModelUsage(input_units=12, output_units=8),
            provider_request_id="fake-request",
        ),
        None,
    )


def _transcription_success(
    request: TranscriptionRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    # Word timestamps in milliseconds, matching the OpenAI-compatible adapter.
    words = (
        TranscriptWordOutput(text="hello", start_ms=0, end_ms=480, confidence=0.98),
        TranscriptWordOutput(text="world", start_ms=500, end_ms=1000, confidence=0.95),
    )
    payload: Mapping[str, object] = {
        "text": "hello world",
        "language": request.language or "en",
        "model": route.model,
        "words": [
            {"word": "hello", "start": 0.0, "end": 0.48, "confidence": 0.98},
            {"word": "world", "start": 0.5, "end": 1.0, "confidence": 0.95},
        ],
    }
    return (
        AdapterResult(
            output=TranscriptionOutput(
                text="hello world",
                language=request.language or "en",
                confidence=0.96,
                words=words,
                raw_response=payload,
            ),
            usage=ModelUsage(input_units=20, output_units=0),
            provider_request_id="fake-request",
        ),
        None,
    )


def _vision_success(
    request: VisionRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del request
    payload: Mapping[str, object] = {
        "choices": [{"message": {"content": "a fake caption"}}],
        "model": route.model,
    }
    return (
        AdapterResult(
            output=VisionOutput(content="a fake caption", raw_response=payload),
            usage=ModelUsage(input_units=4100, output_units=6),
        ),
        None,
    )


def _audio_success(
    request: AudioRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del request
    payload: Mapping[str, object] = {
        "choices": [{"message": {"content": '{"emotion": "neutral"}'}}],
        "model": route.model,
    }
    return (
        AdapterResult(
            output=AudioOutput(content='{"emotion": "neutral"}', raw_response=payload),
            usage=ModelUsage(input_units=2048, output_units=6),
        ),
        None,
    )


def _embeddings_success(
    request: EmbeddingsRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del route
    dimensions = request.dimensions or 8
    vectors = tuple(
        tuple(float((index + offset) % 7) / 7.0 for offset in range(dimensions))
        for index, _ in enumerate(request.inputs)
    )
    payload: Mapping[str, object] = {"data": [{"index": i} for i in range(len(vectors))]}
    return (
        AdapterResult(
            output=EmbeddingsOutput(vectors=vectors, raw_response=payload),
            usage=ModelUsage(input_units=len(request.inputs), output_units=0),
        ),
        None,
    )


def _image_embeddings_success(
    request: ImageEmbeddingsRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del route
    dimensions = 8
    vectors = tuple(
        tuple(float((index + offset) % 5) / 5.0 for offset in range(dimensions))
        for index, _ in enumerate(request.images)
    )
    payload: Mapping[str, object] = {"data": [{"index": i} for i in range(len(vectors))]}
    return (
        AdapterResult(
            output=EmbeddingsOutput(vectors=vectors, raw_response=payload),
            usage=ModelUsage(input_units=len(request.images), output_units=0),
            usage_reported=False,
        ),
        None,
    )


def _generation_success(
    request: GenerationRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del route
    artifacts = tuple(
        GeneratedArtifact(url=f"https://fake.invalid/image-{index}.png")
        for index in range(request.count)
    )
    payload: Mapping[str, object] = {
        "id": "fake-generation",
        "data": [{"url": artifact.url} for artifact in artifacts],
    }
    return (
        AdapterResult(
            output=GenerationOutput(
                artifacts=artifacts,
                operation_id="fake-generation",
                raw_response=payload,
            ),
            usage=ModelUsage(input_units=len(request.prompt), output_units=request.count),
        ),
        None,
    )


def _visual_manifest_success(
    request: VisualManifestRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    from app.schemas.visual import VideoManifest

    manifest = VideoManifest(
        duration_ms=1000,
        sample_rate_fps=request.sample_rate_fps,
        frame_count=2,
        provider=route.provider,
        model_versions={route.model: route.model_revision},
    )
    return (
        AdapterResult(
            output=VisualManifestOutput(manifest=manifest),
            usage=ModelUsage(input_units=16, output_units=0),
        ),
        None,
    )


def _audio_intelligence_success(
    request: AudioIntelligenceRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del request, route
    return (
        AdapterResult(
            output=AudioIntelligenceOutput(
                insights={"speakers": 1, "emotion": "neutral"}
            ),
            usage=ModelUsage(input_units=32, output_units=0),
        ),
        None,
    )


def _matte_segmentation_success(
    request: MatteSegmentationRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del route
    width = min(request.max_dimension, 720)
    return (
        AdapterResult(
            output=MatteSegmentationOutput(
                fps=25.0, width=width, height=width, frame_count=25
            ),
            usage=ModelUsage(input_units=16, output_units=0),
        ),
        None,
    )


def _stem_separation_success(
    request: StemSeparationRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del route
    base = request.source_path.parent
    stems: Mapping[str, Path] = {
        "vocals": base / "vocals.wav",
        "no_vocals": base / "no_vocals.wav",
    }
    return (
        AdapterResult(
            output=StemSeparationOutput(stems=stems),
            usage=ModelUsage(input_units=1, output_units=0),
        ),
        None,
    )


def _diarization_success(
    request: DiarizationRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del route
    end_ms = max(1, round(request.duration_s * 1000))
    segments = (
        DiarizationSegmentOutput(speaker="speaker_1", start_ms=0, end_ms=end_ms // 2),
        DiarizationSegmentOutput(
            speaker="speaker_2", start_ms=end_ms // 2, end_ms=end_ms
        ),
    )
    payload: Mapping[str, object] = {"speaker_count": 2}
    return (
        AdapterResult(
            output=DiarizationOutput(segments=segments, raw_response=payload),
            usage=ModelUsage(input_units=1, output_units=0),
        ),
        None,
    )


def _object_detection_success(
    request: ObjectDetectionRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del route
    detections = tuple(
        (
            DetectedObjectOutput(
                label="object",
                confidence=0.9,
                x=0.1,
                y=0.1,
                width=0.3,
                height=0.3,
            ),
        )
        for _ in request.images
    )
    payload: Mapping[str, object] = {"image_count": len(request.images)}
    return (
        AdapterResult(
            output=ObjectDetectionOutput(detections=detections, raw_response=payload),
            usage=ModelUsage(input_units=len(request.images), output_units=0),
        ),
        None,
    )


def _sound_classification_success(
    request: SoundClassificationRequest, route: ModelRoute
) -> tuple[AdapterResult, None]:
    del route
    labels = tuple(
        SoundLabelOutput(label=f"sound_{index}", score=0.9 - index * 0.1)
        for index in range(max(1, min(request.max_results, 2)))
    )
    payload: Mapping[str, object] = {"label_count": len(labels)}
    return (
        AdapterResult(
            output=SoundClassificationOutput(labels=labels, raw_response=payload),
            usage=ModelUsage(input_units=1, output_units=0),
        ),
        None,
    )


# Dispatch table keyed by request type. Task 3 extends this by adding entries.
_SuccessBuilder = Callable[[ModelRequest, ModelRoute], "tuple[AdapterResult, None]"]
_DISPATCH: dict[type, _SuccessBuilder] = {
    PlanningRequest: _planning_success,  # type: ignore[dict-item]
    TranscriptionRequest: _transcription_success,  # type: ignore[dict-item]
    VisionRequest: _vision_success,  # type: ignore[dict-item]
    AudioRequest: _audio_success,  # type: ignore[dict-item]
    EmbeddingsRequest: _embeddings_success,  # type: ignore[dict-item]
    GenerationRequest: _generation_success,  # type: ignore[dict-item]
    VisualManifestRequest: _visual_manifest_success,  # type: ignore[dict-item]
    AudioIntelligenceRequest: _audio_intelligence_success,  # type: ignore[dict-item]
    ImageEmbeddingsRequest: _image_embeddings_success,  # type: ignore[dict-item]
    MatteSegmentationRequest: _matte_segmentation_success,  # type: ignore[dict-item]
    StemSeparationRequest: _stem_separation_success,  # type: ignore[dict-item]
    DiarizationRequest: _diarization_success,  # type: ignore[dict-item]
    ObjectDetectionRequest: _object_detection_success,  # type: ignore[dict-item]
    SoundClassificationRequest: _sound_classification_success,  # type: ignore[dict-item]
}


@dataclass
class _Scripted:
    """A queue of scripted outcomes for one route id, plus a default."""

    default: ScriptedOutcome = ScriptedOutcome.SUCCESS
    queue: list[ScriptedOutcome] = field(default_factory=list)

    def next(self) -> ScriptedOutcome:
        if self.queue:
            return self.queue.pop(0)
        return self.default


class DeterministicInferenceAdapter:
    """A deterministic ``ModelAdapter`` with scripted per-call outcomes.

    Outcomes are selected by route id. A route with no script uses
    ``default_outcome``. ``script(route_id, outcomes)`` queues an ordered list of
    outcomes consumed one per call, so fallback and retry paths can be exercised.
    """

    def __init__(
        self,
        *,
        default_outcome: ScriptedOutcome = ScriptedOutcome.SUCCESS,
        app_env: str | None = None,
    ) -> None:
        self._guard_production(app_env)
        self._default_outcome = default_outcome
        self._scripts: dict[str, _Scripted] = {}
        self._jobs: dict[str, _VideoJob] = {}
        self._job_scripts: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self.calls: list[AdapterCallRecord] = []

    @staticmethod
    def _guard_production(app_env: str | None) -> None:
        env = app_env
        if env is None:
            from app.core.config import get_settings

            env = get_settings().app_env
        if str(env).strip().lower() == "production":
            raise ProductionAdapterRejected(
                "The fake inference adapter is rejected in production."
            )

    def script(self, route_id: str, outcomes: list[ScriptedOutcome]) -> None:
        """Queue an ordered list of outcomes for ``route_id`` (one per call)."""
        with self._lock:
            entry = self._scripts.setdefault(route_id, _Scripted(self._default_outcome))
            entry.queue.extend(outcomes)

    def set_default(self, route_id: str, outcome: ScriptedOutcome) -> None:
        """Set the steady-state outcome returned after any queued outcomes."""
        with self._lock:
            entry = self._scripts.setdefault(route_id, _Scripted(self._default_outcome))
            entry.default = outcome

    def script_video_poll(self, operation_id: str, states: list[str]) -> None:
        """Script the state sequence a poll for ``operation_id`` walks through."""
        with self._lock:
            self._job_scripts[operation_id] = list(states)
            job = self._jobs.get(operation_id)
            if job is not None:
                job.states = list(states)
                job.index = 0

    def _next_outcome(self, route_id: str) -> ScriptedOutcome:
        with self._lock:
            entry = self._scripts.get(route_id)
            if entry is None:
                return self._default_outcome
            return entry.next()

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        outcome = self._next_outcome(route.route_id)
        request_type = type(request).__name__
        sent = outcome in _SENT_OUTCOMES
        self.calls.append(
            AdapterCallRecord(
                route_id=route.route_id,
                request_type=request_type,
                outcome=outcome,
                sent=sent,
            )
        )

        if outcome in (ScriptedOutcome.TIMEOUT_BEFORE_SEND, ScriptedOutcome.TIMEOUT_AFTER_SEND):
            raise _timeout_failure(route.provider, sent=sent)
        if outcome == ScriptedOutcome.SCHEMA_INVALID:
            raise _malformed_failure(route.provider)
        status = _HTTP_STATUS_BY_OUTCOME.get(outcome)
        if status is not None:
            raise _status_failure(route.provider, status)

        # SUCCESS: async requests use the in-memory job store.
        if isinstance(request, VideoGenerationSubmitRequest):
            return self._submit_video(route, request)
        if isinstance(request, VideoGenerationPollRequest):
            return self._poll_video(route, request)

        builder = _DISPATCH.get(type(request))
        if builder is None:
            raise TypeError(f"fake adapter has no builder for {request_type}")
        result, _ = builder(request, route)
        return result

    # --- async in-memory job store -----------------------------------------

    def _submit_video(
        self, route: ModelRoute, request: VideoGenerationSubmitRequest
    ) -> AdapterResult:
        del route
        with self._lock:
            operation_id = f"fake-op-{uuid4().hex}"
            states = self._job_scripts.get(request.idempotency_key) or [
                "running",
                "succeeded",
            ]
            self._jobs[operation_id] = _VideoJob(
                states=list(states), operation_id=operation_id
            )
        payload: Mapping[str, object] = {"id": operation_id}
        return AdapterResult(
            output=GenerationOperationOutput(
                operation_id=operation_id, raw_response=payload
            ),
            usage=ModelUsage(input_units=len(request.prompt), output_units=0),
        )

    def _poll_video(
        self, route: ModelRoute, request: VideoGenerationPollRequest
    ) -> AdapterResult:
        with self._lock:
            job = self._jobs.get(request.operation_id)
            if job is None:
                raise _malformed_failure(route.provider)
            state = job.states[min(job.index, len(job.states) - 1)]
            if job.index < len(job.states) - 1:
                job.index += 1
            artifact_url = job.artifact_url
        if state == "succeeded":
            output = GenerationOutput(
                artifacts=(GeneratedArtifact(url=artifact_url),),
                operation_id=request.operation_id,
                raw_response={"state": state},
            )
        elif state == "failed":
            raise ProviderCallFailure(
                provider=route.provider,
                error_info=ProviderErrorInfo("provider_unavailable", True),
            )
        else:
            # Still running: no artifact yet. Represented as an operation handle.
            return AdapterResult(
                output=GenerationOperationOutput(
                    operation_id=request.operation_id,
                    raw_response={"state": state},
                ),
                usage=ModelUsage(),
            )
        return AdapterResult(output=output, usage=ModelUsage())


def supported_request_types() -> frozenset[type]:
    """Request types the fake can currently build a success output for."""
    return frozenset(_DISPATCH) | {
        VideoGenerationSubmitRequest,
        VideoGenerationPollRequest,
    }
