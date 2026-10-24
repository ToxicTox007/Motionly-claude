from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.schemas.uploads import VideoMetadata
from app.schemas.visual import VideoManifest
from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderErrorInfo,
    classify_provider_exception,
)

logger = logging.getLogger(__name__)


class ModelCapability(StrEnum):
    PLANNING = "planning"
    TRANSCRIPTION = "transcription"
    VISION = "vision"
    AUDIO = "audio"
    EMBEDDINGS = "embeddings"
    GENERATION = "generation"


class ModelOperation(StrEnum):
    PLANNING_CHAT = "planning.chat"
    TRANSCRIPTION_SPEECH_TO_TEXT = "transcription.speech_to_text"
    VISION_PROMPT = "vision.prompt"
    VISION_VIDEO_MANIFEST = "vision.video_manifest"
    VISION_MATTE_SEGMENTATION = "vision.matte_segmentation"
    VISION_OBJECT_DETECTION = "vision.object_detection"
    AUDIO_PROMPT = "audio.prompt"
    AUDIO_INTELLIGENCE = "audio.intelligence"
    AUDIO_STEM_SEPARATION = "audio.stem_separation"
    AUDIO_DIARIZATION = "audio.diarization"
    EMBEDDINGS_TEXT = "embeddings.text"
    EMBEDDINGS_IMAGE = "embeddings.image"
    GENERATION_IMAGE = "generation.image"
    GENERATION_VIDEO_SUBMIT = "generation.video_submit"
    GENERATION_VIDEO_POLL = "generation.video_poll"


# Deployment-wide monthly spend caps are keyed by these feature names
# (MODEL_GATEWAY_FEATURE_BUDGETS_USD). A route opts in with ``budget_feature``;
# routes without one are limited only by the workspace monthly budget.
BUDGET_FEATURES = frozenset(
    {
        "planning",
        "transcription",
        "generation",
        "embeddings",
        "vision_objects",
        "vision_captions",
        "audio_diarization",
        "audio_emotion",
        "audio_stems",
    }
)

# Custom MODEL_GATEWAY_ROUTES entries default to these features when they do
# not name one. Vision and audio cover several features, so they must be explicit.
DEFAULT_BUDGET_FEATURE_BY_CAPABILITY = {
    "planning": "planning",
    "transcription": "transcription",
    "generation": "generation",
    "embeddings": "embeddings",
}


def parse_feature_budgets(value: str | None) -> dict[str, float]:
    """Parse MODEL_GATEWAY_FEATURE_BUDGETS_USD (a JSON object of feature -> USD)."""
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("MODEL_GATEWAY_FEATURE_BUDGETS_USD must be valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(
            "MODEL_GATEWAY_FEATURE_BUDGETS_USD must be a JSON object of feature to USD"
        )
    budgets: dict[str, float] = {}
    for feature, amount in payload.items():
        if feature not in BUDGET_FEATURES:
            raise ValueError(
                f"MODEL_GATEWAY_FEATURE_BUDGETS_USD has unknown feature {feature!r}; "
                f"expected one of {', '.join(sorted(BUDGET_FEATURES))}"
            )
        if isinstance(amount, bool) or not isinstance(amount, int | float):
            raise ValueError(
                f"MODEL_GATEWAY_FEATURE_BUDGETS_USD[{feature!r}] must be a number"
            )
        if not math.isfinite(float(amount)) or float(amount) <= 0:
            raise ValueError(
                f"MODEL_GATEWAY_FEATURE_BUDGETS_USD[{feature!r}] must be positive; "
                "omit a feature to leave it uncapped"
            )
        budgets[str(feature)] = float(amount)
    return budgets


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"unsupported chat role: {self.role}")
        if not self.content:
            raise ValueError("chat message content cannot be empty")


@dataclass(frozen=True)
class PlanningRequest:
    messages: tuple[ChatMessage, ...]
    response_format: Mapping[str, object] | None = None
    temperature: float = 0.2
    max_output_units: int = 4096

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("planning requests require at least one message")
        if self.max_output_units < 1:
            raise ValueError("max_output_units must be positive")


@dataclass(frozen=True)
class TranscriptionRequest:
    audio_path: Path
    language: str | None = None
    prompt: str | None = None
    response_format: str = "verbose_json"

    def __post_init__(self) -> None:
        if not str(self.audio_path):
            raise ValueError("transcription audio_path cannot be empty")
        if not self.response_format:
            raise ValueError("transcription response_format cannot be empty")


@dataclass(frozen=True)
class VisionRequest:
    prompt: str
    image_urls: tuple[str, ...]
    max_output_units: int = 2048
    # Optional structured-output contract (OpenAI-style response_format).
    response_format: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.prompt or not self.image_urls:
            raise ValueError("vision requests require a prompt and at least one image")


@dataclass(frozen=True)
class AudioRequest:
    prompt: str
    audio_path: Path
    audio_format: str = "wav"
    max_output_units: int = 2048
    # Optional structured-output contract (OpenAI-style response_format).
    response_format: Mapping[str, object] | None = None
    # Seconds of audio in the clip; meters per-second feature quotas (never sent).
    duration_s: float | None = None

    def __post_init__(self) -> None:
        if not self.prompt or not self.audio_format:
            raise ValueError("audio requests require a prompt and audio format")
        if self.max_output_units < 1:
            raise ValueError("max_output_units must be positive")
        if self.duration_s is not None and not (
            math.isfinite(self.duration_s) and self.duration_s >= 0
        ):
            raise ValueError("audio duration_s must be a finite, non-negative number")


@dataclass(frozen=True)
class EmbeddingsRequest:
    inputs: tuple[str, ...]
    dimensions: int | None = None

    def __post_init__(self) -> None:
        if not self.inputs or any(not item for item in self.inputs):
            raise ValueError("embedding inputs cannot be empty")


@dataclass(frozen=True)
class GenerationRequest:
    prompt: str
    image_urls: tuple[str, ...] = ()
    size: str = "1024x1024"
    count: int = 1
    response_format: str = "url"

    def __post_init__(self) -> None:
        if not self.prompt:
            raise ValueError("generation prompt cannot be empty")
        if not 1 <= self.count <= 4:
            raise ValueError("generation count must be between 1 and 4")


@dataclass(frozen=True)
class VisualManifestRequest:
    source_path: Path
    metadata: VideoMetadata
    sample_rate_fps: float
    scene_threshold: float

    def __post_init__(self) -> None:
        if self.sample_rate_fps <= 0:
            raise ValueError("visual sample_rate_fps must be positive")
        if not 0 <= self.scene_threshold <= 1:
            raise ValueError("visual scene_threshold must be between 0 and 1")


@dataclass(frozen=True)
class AudioIntelligenceRequest:
    audio_path: Path


@dataclass(frozen=True)
class ImageEmbeddingsRequest:
    images: tuple[bytes, ...]
    source_hints: tuple[str | None, ...] = ()

    def __post_init__(self) -> None:
        if not self.images or any(not image for image in self.images):
            raise ValueError("image embedding requests cannot be empty")
        if self.source_hints and len(self.source_hints) != len(self.images):
            raise ValueError("source_hints must align with images")


@dataclass(frozen=True)
class VideoGenerationSubmitRequest:
    prompt: str
    prompt_image_url: str | None
    duration_s: int
    ratio: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.prompt or not self.ratio or not self.idempotency_key:
            raise ValueError("video generation submit fields cannot be empty")
        if self.duration_s < 1:
            raise ValueError("video generation duration_s must be positive")


@dataclass(frozen=True)
class VideoGenerationPollRequest:
    operation_id: str

    def __post_init__(self) -> None:
        if not self.operation_id:
            raise ValueError("video generation operation_id cannot be empty")


@dataclass(frozen=True)
class MatteSegmentationRequest:
    source_path: Path
    output_path: Path
    metadata: VideoMetadata
    max_dimension: int = 720

    def __post_init__(self) -> None:
        if self.max_dimension < 2:
            raise ValueError("matte max_dimension must be at least two pixels")


@dataclass(frozen=True)
class StemSeparationRequest:
    source_path: Path
    model: str
    # Source duration, set when the route is priced per second of audio.
    duration_s: float | None = None

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("stem separation model cannot be empty")
        if self.duration_s is not None and not (
            math.isfinite(self.duration_s) and self.duration_s > 0
        ):
            raise ValueError("stem separation duration_s must be positive")


@dataclass(frozen=True)
class DiarizationRequest:
    """Speaker diarization of a mono speech track."""

    audio_path: Path
    duration_s: float
    # Providers that bill a per-job minimum (pyannoteAI: 20 s) reserve at least this.
    minimum_billable_s: int = 0

    def __post_init__(self) -> None:
        if not str(self.audio_path):
            raise ValueError("diarization audio_path cannot be empty")
        if not (math.isfinite(self.duration_s) and self.duration_s > 0):
            raise ValueError("diarization duration_s must be positive")
        if self.minimum_billable_s < 0:
            raise ValueError("diarization minimum_billable_s cannot be negative")


@dataclass(frozen=True)
class ObjectDetectionRequest:
    """Object localisation for a batch of encoded images (priced per image)."""

    images: tuple[bytes, ...]
    max_results: int = 24

    def __post_init__(self) -> None:
        if not self.images or any(not image for image in self.images):
            raise ValueError("object detection requests cannot be empty")
        if self.max_results < 1:
            raise ValueError("object detection max_results must be positive")


type ModelRequest = (
    PlanningRequest
    | TranscriptionRequest
    | VisionRequest
    | AudioRequest
    | EmbeddingsRequest
    | GenerationRequest
    | VisualManifestRequest
    | AudioIntelligenceRequest
    | ImageEmbeddingsRequest
    | VideoGenerationSubmitRequest
    | VideoGenerationPollRequest
    | MatteSegmentationRequest
    | StemSeparationRequest
    | DiarizationRequest
    | ObjectDetectionRequest
)


@dataclass(frozen=True)
class PlanningOutput:
    content: str
    raw_response: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("planning output content cannot be empty")


@dataclass(frozen=True)
class TranscriptWordOutput:
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.text or self.start_ms < 0 or self.end_ms < self.start_ms:
            raise ValueError("transcript word text and timestamps are invalid")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("transcript word confidence must be between 0 and 1")


@dataclass(frozen=True)
class TranscriptionOutput:
    text: str
    language: str | None
    confidence: float | None
    words: tuple[TranscriptWordOutput, ...]
    raw_response: Mapping[str, object]


@dataclass(frozen=True)
class VisionOutput:
    content: str
    raw_response: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("vision output content cannot be empty")


@dataclass(frozen=True)
class AudioOutput:
    content: str
    raw_response: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("audio output content cannot be empty")


@dataclass(frozen=True)
class EmbeddingsOutput:
    vectors: tuple[tuple[float, ...], ...]
    raw_response: Mapping[str, object]

    def __post_init__(self) -> None:
        dimensions = {len(vector) for vector in self.vectors}
        if (
            not self.vectors
            or dimensions == {0}
            or len(dimensions) != 1
            or any(not math.isfinite(value) for vector in self.vectors for value in vector)
        ):
            raise ValueError("embedding output vectors must be finite and dimensionally aligned")


@dataclass(frozen=True)
class GeneratedArtifact:
    url: str | None = None
    base64_data: str | None = None
    revised_prompt: str | None = None

    def __post_init__(self) -> None:
        if not self.url and not self.base64_data:
            raise ValueError("generated artifacts require a URL or base64 payload")


@dataclass(frozen=True)
class GenerationOutput:
    artifacts: tuple[GeneratedArtifact, ...]
    operation_id: str | None
    raw_response: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.artifacts:
            raise ValueError("generation output requires at least one artifact")


@dataclass(frozen=True)
class VisualManifestOutput:
    manifest: VideoManifest


@dataclass(frozen=True)
class AudioIntelligenceOutput:
    insights: Mapping[str, object]


@dataclass(frozen=True)
class GenerationOperationOutput:
    operation_id: str
    raw_response: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.operation_id:
            raise ValueError("generation operation_id cannot be empty")


@dataclass(frozen=True)
class MatteSegmentationOutput:
    fps: float
    width: int
    height: int
    frame_count: int

    def __post_init__(self) -> None:
        if self.fps <= 0 or min(self.width, self.height, self.frame_count) <= 0:
            raise ValueError("matte segmentation output dimensions are invalid")


@dataclass(frozen=True)
class StemSeparationOutput:
    stems: Mapping[str, Path]

    def __post_init__(self) -> None:
        if not self.stems or any(not name.strip() for name in self.stems):
            raise ValueError("stem separation output cannot be empty")


@dataclass(frozen=True)
class DiarizationSegmentOutput:
    speaker: str
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if not self.speaker.strip():
            raise ValueError("diarization speaker cannot be empty")
        if self.start_ms < 0 or self.end_ms < self.start_ms:
            raise ValueError("diarization segment bounds are invalid")


@dataclass(frozen=True)
class DiarizationOutput:
    segments: tuple[DiarizationSegmentOutput, ...]
    raw_response: Mapping[str, object]

    @property
    def speaker_count(self) -> int:
        return len({segment.speaker for segment in self.segments})


@dataclass(frozen=True)
class DetectedObjectOutput:
    """One localised object; the box is normalised to [0, 1] image coordinates."""

    label: str
    confidence: float
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("detected object label cannot be empty")
        values = (self.confidence, self.x, self.y, self.width, self.height)
        if any(not (math.isfinite(value) and 0 <= value <= 1) for value in values):
            raise ValueError("detected object scores and boxes must be within [0, 1]")


@dataclass(frozen=True)
class ObjectDetectionOutput:
    # One tuple of detections per request image, in request order.
    detections: tuple[tuple[DetectedObjectOutput, ...], ...]
    raw_response: Mapping[str, object] = field(default_factory=dict)


type ModelOutput = (
    PlanningOutput
    | TranscriptionOutput
    | VisionOutput
    | AudioOutput
    | EmbeddingsOutput
    | GenerationOutput
    | VisualManifestOutput
    | AudioIntelligenceOutput
    | GenerationOperationOutput
    | MatteSegmentationOutput
    | StemSeparationOutput
    | DiarizationOutput
    | ObjectDetectionOutput
)


@dataclass(frozen=True)
class ModelUsage:
    input_units: int = 0
    output_units: int = 0

    def __post_init__(self) -> None:
        if self.input_units < 0 or self.output_units < 0:
            raise ValueError("model usage units cannot be negative")

    @property
    def total_units(self) -> int:
        return self.input_units + self.output_units


@dataclass(frozen=True)
class AdapterResult:
    output: ModelOutput
    usage: ModelUsage = field(default_factory=ModelUsage)
    usage_reported: bool = True
    provider_request_id: str | None = None


@dataclass(frozen=True)
class ModelRoute:
    route_id: str
    capability: ModelCapability
    adapter: str
    provider: str
    model: str
    model_revision: str
    operations: tuple[ModelOperation, ...] = ()
    priority: int = 100
    input_cost_per_million_usd: float = 0.0
    output_cost_per_million_usd: float = 0.0
    fixed_cost_usd: float = 0.0
    explicitly_free: bool = False
    max_output_units: int = 4096
    enabled: bool = True
    # HTTP adapter routes bind this from the adapter's actual configured base URL.
    # Native routes leave it unset and never cross the live-effects boundary.
    endpoint_host: str | None = None
    requires_live_effects_gate: bool = False
    # Optional deployment-wide monthly cap (see BUDGET_FEATURES).
    budget_feature: str | None = None

    def __post_init__(self) -> None:
        required = {
            "route_id": self.route_id,
            "adapter": self.adapter,
            "provider": self.provider,
            "model": self.model,
            "model_revision": self.model_revision,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"model route fields cannot be empty: {', '.join(missing)}")
        if not is_immutable_model_revision(self.model_revision, model=self.model):
            raise ValueError(
                "model_revision must be a pinned date, semantic version, or content hash"
            )
        if any(
            operation.value.split(".", 1)[0] != self.capability.value
            for operation in self.operations
        ):
            raise ValueError("model route operation does not match its capability")
        if min(
            self.input_cost_per_million_usd,
            self.output_cost_per_million_usd,
            self.fixed_cost_usd,
        ) < 0:
            raise ValueError("model route costs cannot be negative")
        if self.explicitly_free and any(
            cost > 0
            for cost in (
                self.input_cost_per_million_usd,
                self.output_cost_per_million_usd,
                self.fixed_cost_usd,
            )
        ):
            raise ValueError("explicitly free model routes cannot declare positive costs")
        if self.max_output_units < 1:
            raise ValueError("model route max_output_units must be positive")
        if self.budget_feature is not None and self.budget_feature not in BUDGET_FEATURES:
            raise ValueError(
                f"model route budget_feature {self.budget_feature!r} is unknown; "
                f"expected one of {', '.join(sorted(BUDGET_FEATURES))}"
            )

    @property
    def circuit_key(self) -> str:
        return f"{self.route_id}:{self.model_revision}"


@dataclass(frozen=True)
class ModelCallContext:
    workspace_id: object | None = None
    project_id: object | None = None
    request_id: str = field(default_factory=lambda: str(uuid4()))
    idempotency_key: str | None = None
    budget_usd: float | None = None
    required_route_id: str | None = None
    required_model_revision: str | None = None
    operation: ModelOperation | None = None


# The context of the gateway call whose adapter is currently running. Composite
# local adapters (audio intelligence, visual manifest) call providers that may
# make nested remote gateway calls; those inherit the outer workspace/project so
# budgets, quotas and telemetry stay attributed to the right tenant.
_ACTIVE_CALL_CONTEXT: ContextVar[ModelCallContext | None] = ContextVar(
    "motionly_active_model_call_context", default=None
)


def inherited_call_context(
    *,
    workspace_id: object | None = None,
    project_id: object | None = None,
) -> ModelCallContext:
    """A fresh call context for a nested gateway call.

    Explicit ids win; otherwise the ids of the enclosing gateway call are used.
    """
    active = _ACTIVE_CALL_CONTEXT.get()
    return ModelCallContext(
        workspace_id=(
            workspace_id
            if workspace_id is not None
            else (active.workspace_id if active is not None else None)
        ),
        project_id=(
            project_id
            if project_id is not None
            else (active.project_id if active is not None else None)
        ),
    )


@dataclass(frozen=True)
class ModelInvocationMetadata:
    request_id: str
    capability: ModelCapability
    operation: ModelOperation
    route_id: str
    provider: str
    model: str
    model_revision: str
    provider_request_id: str | None
    fallback_count: int
    latency_ms: int
    usage: ModelUsage
    estimated_cost_usd: float
    actual_cost_usd: float

    def as_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "capability": self.capability.value,
            "operation": self.operation.value,
            "route_id": self.route_id,
            "provider": self.provider,
            "model": self.model,
            "model_revision": self.model_revision,
            "provider_request_id": self.provider_request_id,
            "fallback_count": self.fallback_count,
            "latency_ms": self.latency_ms,
            "usage": {
                "input_units": self.usage.input_units,
                "output_units": self.usage.output_units,
                "total_units": self.usage.total_units,
            },
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "actual_cost_usd": round(self.actual_cost_usd, 8),
        }


@dataclass(frozen=True)
class GatewayResult:
    output: ModelOutput
    metadata: ModelInvocationMetadata


class ModelGatewayError(RuntimeError):
    code = "model_gateway_error"


class NoModelRoute(ModelGatewayError):
    code = "model_route_unavailable"

    def __init__(self, capability: ModelCapability) -> None:
        super().__init__(f"No model route is configured for {capability.value}.")
        self.capability = capability


class ModelBudgetExceeded(ModelGatewayError):
    code = "model_budget_exceeded"


class ModelProvidersExhausted(ModelGatewayError):
    code = "model_providers_exhausted"

    def __init__(self, capability: ModelCapability, failures: Sequence[Mapping[str, str]]) -> None:
        super().__init__(f"All configured model routes failed for {capability.value}.")
        self.capability = capability
        self.failures = tuple(dict(item) for item in failures)


class FeatureQuotaError(ModelGatewayError):
    """A per-plan feature quota refused the call before any provider was contacted."""

    code = "quota_error"

    def __init__(self, message: str, *, feature: str | None = None) -> None:
        super().__init__(message)
        self.feature = feature


class FeatureQuotaExceeded(FeatureQuotaError):
    code = "quota_exceeded"


class FeatureQuotaCheckFailed(FeatureQuotaError):
    """The quota could not be evaluated; paid calls are refused rather than unmetered."""

    code = "quota_check_failed"


QUOTA_FAILURE_CODES = frozenset({FeatureQuotaExceeded.code, FeatureQuotaCheckFailed.code})


class FeatureQuotaGuard(Protocol):
    """Per-plan usage quotas for metered remote features (see app.services.billing)."""

    def applies(self, *, route: ModelRoute, context: ModelCallContext) -> bool: ...

    def check(self, *, route: ModelRoute, context: ModelCallContext, quantity: float) -> None: ...

    def record(
        self,
        *,
        route: ModelRoute,
        context: ModelCallContext,
        quantity: float,
        actual_cost_usd: float,
    ) -> None: ...


class ModelAdapter(Protocol):
    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult: ...


class ModelLiveEffectsGate(Protocol):
    """A local-audit gate that must decide before a remote adapter is invoked."""

    def authorize_model(
        self,
        *,
        provider: str,
        endpoint_host: str | None,
        estimated_cost_usd: float,
        explicitly_free: bool,
    ) -> None: ...


@dataclass(frozen=True)
class BudgetReservation:
    token: str
    reserved_usd: float
    limit_usd: float


class BudgetLedger(Protocol):
    def reserve(self, *, scope: str, amount_usd: float, limit_usd: float) -> BudgetReservation: ...

    def commit(self, reservation: BudgetReservation, *, actual_usd: float) -> None: ...

    def release(self, reservation: BudgetReservation) -> None: ...


class InMemoryBudgetLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[str, float] = {}
        self._reservations: dict[str, tuple[str, float, float]] = {}

    def reserve(self, *, scope: str, amount_usd: float, limit_usd: float) -> BudgetReservation:
        amount = max(0.0, amount_usd)
        with self._lock:
            current = self._usage.get(scope, 0.0)
            if limit_usd > 0 and current + amount > limit_usd + 1e-9:
                raise ModelBudgetExceeded("The workspace model budget is exhausted.")
            token = uuid4().hex
            self._usage[scope] = current + amount
            self._reservations[token] = (scope, amount, limit_usd)
        return BudgetReservation(
            token=token,
            reserved_usd=amount,
            limit_usd=limit_usd,
        )

    def commit(self, reservation: BudgetReservation, *, actual_usd: float) -> None:
        with self._lock:
            item = self._reservations.pop(reservation.token, None)
            if item is None:
                return
            scope, reserved, _limit = item
            actual = max(0.0, actual_usd)
            self._usage[scope] = max(
                0.0,
                self._usage.get(scope, 0.0) - reserved + actual,
            )
            if actual > reserved + 1e-9:
                raise ModelBudgetExceeded(
                    "Actual model cost exceeded the preflight reservation."
                )

    def release(self, reservation: BudgetReservation) -> None:
        with self._lock:
            item = self._reservations.pop(reservation.token, None)
            if item is None:
                return
            scope, reserved, _limit = item
            self._usage[scope] = max(0.0, self._usage.get(scope, 0.0) - reserved)

    def usage(self, scope: str) -> float:
        with self._lock:
            return self._usage.get(scope, 0.0)


class RedisBudgetLedger:
    """Atomic, cross-process dollar reservations backed by Redis."""

    def __init__(
        self,
        redis_url: str,
        *,
        prefix: str = "motionly:model-budget",
        reservation_ttl_seconds: int = 3600,
    ) -> None:
        from redis import Redis

        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._prefix = prefix
        self._reservation_ttl_seconds = max(1, reservation_ttl_seconds)

    def reserve(self, *, scope: str, amount_usd: float, limit_usd: float) -> BudgetReservation:
        token = uuid4().hex
        usage_key = f"{self._prefix}:usage:{scope}"
        reservation_key = f"{self._prefix}:reservation:{token}"
        leases_key = f"{self._prefix}:leases:{scope}"
        script = """
        local current = tonumber(redis.call('GET', KEYS[1]) or '0')
        local expired = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', ARGV[4])
        for _, expired_token in ipairs(expired) do
          local expired_key = ARGV[5] .. ':reservation:' .. expired_token
          local value = redis.call('GET', expired_key)
          if value then
            local separator = string.find(value, '|', 1, true)
            if separator then
              current = math.max(0, current - (tonumber(string.sub(value, separator + 1)) or 0))
            end
            redis.call('DEL', expired_key)
          end
          redis.call('ZREM', KEYS[3], expired_token)
        end
        local amount = tonumber(ARGV[1])
        local limit = tonumber(ARGV[2])
        if limit > 0 and current + amount > limit then
          redis.call('SET', KEYS[1], current, 'EX', 3456000)
          return 0
        end
        redis.call('SET', KEYS[1], current + amount, 'EX', 3456000)
        redis.call('SET', KEYS[2], ARGV[3] .. '|' .. ARGV[1], 'EX', 3456000)
        redis.call('ZADD', KEYS[3], tonumber(ARGV[4]) + tonumber(ARGV[6]), ARGV[7])
        redis.call('EXPIRE', KEYS[3], 3456000)
        return 1
        """
        now = int(time.time())
        accepted = self._redis.eval(
            script,
            3,
            usage_key,
            reservation_key,
            leases_key,
            str(max(0.0, amount_usd)),
            str(max(0.0, limit_usd)),
            scope,
            str(now),
            self._prefix,
            str(self._reservation_ttl_seconds),
            token,
        )
        if int(accepted) != 1:
            raise ModelBudgetExceeded("The workspace model budget is exhausted.")
        return BudgetReservation(
            token=token,
            reserved_usd=max(0.0, amount_usd),
            limit_usd=max(0.0, limit_usd),
        )

    def commit(self, reservation: BudgetReservation, *, actual_usd: float) -> None:
        self._settle(reservation, actual_usd=max(0.0, actual_usd))

    def release(self, reservation: BudgetReservation) -> None:
        self._settle(reservation, actual_usd=0.0)

    def usage(self, scope: str) -> float:
        value = self._redis.get(f"{self._prefix}:usage:{scope}")
        return float(value or 0.0)

    def _settle(self, reservation: BudgetReservation, *, actual_usd: float) -> None:
        reservation_key = f"{self._prefix}:reservation:{reservation.token}"
        script = """
        local value = redis.call('GET', KEYS[1])
        if not value then return 0 end
        local separator = string.find(value, '|', 1, true)
        if not separator then redis.call('DEL', KEYS[1]); return 0 end
        local scope = string.sub(value, 1, separator - 1)
        local reserved = tonumber(string.sub(value, separator + 1)) or 0
        local usage_key = ARGV[1] .. ':usage:' .. scope
        local leases_key = ARGV[1] .. ':leases:' .. scope
        local current = tonumber(redis.call('GET', usage_key) or '0')
        local actual = tonumber(ARGV[2])
        redis.call('ZREM', leases_key, ARGV[3])
        local settled = math.max(0, current - reserved + actual)
        redis.call('SET', usage_key, settled, 'EX', 3456000)
        redis.call('DEL', KEYS[1])
        if actual > reserved then
          return -1
        end
        return 1
        """
        settled = self._redis.eval(
            script,
            1,
            reservation_key,
            self._prefix,
            str(actual_usd),
            reservation.token,
        )
        if int(settled) == -1:
            raise ModelBudgetExceeded(
                "Actual model cost exceeded the preflight reservation."
            )


class CircuitStore(Protocol):
    def check(self, route_key: str) -> None: ...

    def record_success(self, route_key: str) -> None: ...

    def record_failure(self, route_key: str, *, retryable: bool) -> None: ...


class SharedCircuitOpen(ModelGatewayError):
    code = "provider_circuit_open"


class InMemoryCircuitStore:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
        clock=time.monotonic,
    ) -> None:
        self._failure_threshold = max(1, failure_threshold)
        self._cooldown_seconds = max(1, cooldown_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def check(self, route_key: str) -> None:
        with self._lock:
            open_until = self._open_until.get(route_key)
            if open_until is None:
                return
            if self._clock() >= open_until:
                self._open_until.pop(route_key, None)
                self._failures[route_key] = 0
                return
        raise SharedCircuitOpen("The shared provider circuit is open.")

    def record_success(self, route_key: str) -> None:
        with self._lock:
            self._failures.pop(route_key, None)
            self._open_until.pop(route_key, None)

    def record_failure(self, route_key: str, *, retryable: bool) -> None:
        if not retryable:
            return
        with self._lock:
            count = self._failures.get(route_key, 0) + 1
            self._failures[route_key] = count
            if count >= self._failure_threshold:
                self._open_until[route_key] = self._clock() + self._cooldown_seconds


class RedisCircuitStore:
    """Cross-process circuit state; route revisions receive independent circuits."""

    def __init__(
        self,
        redis_url: str,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
        prefix: str = "motionly:model-circuit",
    ) -> None:
        from redis import Redis

        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._failure_threshold = max(1, failure_threshold)
        self._cooldown_seconds = max(1, cooldown_seconds)
        self._prefix = prefix

    def check(self, route_key: str) -> None:
        if self._redis.exists(f"{self._prefix}:open:{route_key}"):
            raise SharedCircuitOpen("The shared provider circuit is open.")

    def record_success(self, route_key: str) -> None:
        self._redis.delete(
            f"{self._prefix}:failures:{route_key}",
            f"{self._prefix}:open:{route_key}",
        )

    def record_failure(self, route_key: str, *, retryable: bool) -> None:
        if not retryable:
            return
        failure_key = f"{self._prefix}:failures:{route_key}"
        open_key = f"{self._prefix}:open:{route_key}"
        script = """
        local count = redis.call('INCR', KEYS[1])
        redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
        if count >= tonumber(ARGV[1]) then
          redis.call('SET', KEYS[2], '1', 'EX', tonumber(ARGV[2]))
        end
        return count
        """
        self._redis.eval(
            script,
            2,
            failure_key,
            open_key,
            str(self._failure_threshold),
            str(self._cooldown_seconds),
        )


class GatewayTelemetry(Protocol):
    def record_attempt(
        self,
        *,
        context: ModelCallContext,
        route: ModelRoute,
        success: bool,
        latency_ms: int,
        error_code: str | None,
        fallback_count: int,
        estimated_cost_usd: float,
        actual_cost_usd: float,
        usage: ModelUsage,
    ) -> None: ...


class NullGatewayTelemetry:
    def record_attempt(self, **_values: object) -> None:
        return


class OperationsGatewayTelemetry:
    def __init__(self, *, metrics_registry: object | None, operations_repository: object | None):
        self._metrics = metrics_registry
        self._operations = operations_repository

    def record_attempt(
        self,
        *,
        context: ModelCallContext,
        route: ModelRoute,
        success: bool,
        latency_ms: int,
        error_code: str | None,
        fallback_count: int,
        estimated_cost_usd: float,
        actual_cost_usd: float,
        usage: ModelUsage,
    ) -> None:
        status = "success" if success else "failure"
        labels = {
            "capability": route.capability.value,
            "provider": route.provider,
            "model_revision": route.model_revision,
            "status": status,
        }
        metric_calls = []
        if self._metrics is not None:
            metric_calls = [
                lambda: self._metrics.observe("model_gateway_latency_ms", latency_ms, **labels),
                lambda: self._metrics.observe(
                    "model_gateway_cost_usd",
                    actual_cost_usd,
                    capability=route.capability.value,
                    provider=route.provider,
                    model_revision=route.model_revision,
                ),
            ]
            if fallback_count:
                metric_calls.append(
                    lambda: self._metrics.observe(
                        "model_gateway_fallback_count",
                        fallback_count,
                        capability=route.capability.value,
                        provider=route.provider,
                    )
                )
        for metric_call in metric_calls:
            try:
                metric_call()
            except Exception as exc:
                _log_telemetry_failure(route, "metrics", exc)
        if self._operations is not None:
            try:
                self._operations.record_provider_slo(
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    provider=route.provider,
                    operation=operation_for_capability_route(route, context),
                    success=success,
                    latency_ms=latency_ms,
                    error_code=error_code,
                    model=route.model,
                    model_revision=route.model_revision,
                    route_id=route.route_id,
                    request_id=context.request_id,
                    fallback_count=fallback_count,
                    estimated_cost_usd=round(estimated_cost_usd, 8),
                    actual_cost_usd=round(actual_cost_usd, 8),
                    usage={
                        "input_units": usage.input_units,
                        "output_units": usage.output_units,
                    },
                )
            except Exception as exc:
                _log_telemetry_failure(route, "operations", exc)


def _log_telemetry_failure(route: ModelRoute, sink: str, exc: Exception) -> None:
    logger.warning(
        "model_gateway_telemetry_failed",
        extra={
            "route_id": route.route_id,
            "sink": sink,
            "error_type": type(exc).__name__,
        },
    )


class ModelGateway:
    def __init__(
        self,
        *,
        adapters: Mapping[str, ModelAdapter],
        routes: Sequence[ModelRoute],
        budget_ledger: BudgetLedger | None = None,
        circuit_store: CircuitStore | None = None,
        telemetry: GatewayTelemetry | None = None,
        live_effects_gate: ModelLiveEffectsGate | None = None,
        monthly_budget_usd: float = 100.0,
        request_budget_usd: float = 5.0,
        feature_budgets_usd: Mapping[str, float] | None = None,
        quota_guard: FeatureQuotaGuard | None = None,
    ) -> None:
        self._adapters = dict(adapters)
        self._registration_lock = threading.Lock()
        self._quota_guard = quota_guard
        self._routes = tuple(
            sorted((route for route in routes if route.enabled), key=lambda route: route.priority)
        )
        self._budget_ledger = budget_ledger or InMemoryBudgetLedger()
        self._circuit_store = circuit_store or InMemoryCircuitStore()
        self._telemetry = telemetry or NullGatewayTelemetry()
        self._live_effects_gate = live_effects_gate
        self._monthly_budget_usd = max(0.0, monthly_budget_usd)
        self._request_budget_usd = max(0.0, request_budget_usd)
        self._feature_budgets_usd = dict(feature_budgets_usd or {})
        unknown_features = sorted(set(self._feature_budgets_usd) - BUDGET_FEATURES)
        if unknown_features:
            raise ValueError(
                "feature budgets reference unknown features: " + ", ".join(unknown_features)
            )
        if any(amount <= 0 for amount in self._feature_budgets_usd.values()):
            raise ValueError(
                "feature budgets must be positive; omit a feature to leave it uncapped"
            )
        route_ids = [route.route_id for route in self._routes]
        duplicate_route_ids = sorted(
            route_id for route_id in set(route_ids) if route_ids.count(route_id) > 1
        )
        if duplicate_route_ids:
            raise ValueError(
                "model routes contain duplicate route_id values: "
                + ", ".join(duplicate_route_ids)
            )
        unknown_adapters = sorted(
            {route.adapter for route in self._routes} - self._adapters.keys()
        )
        if unknown_adapters:
            names = ", ".join(unknown_adapters)
            raise ValueError(f"model routes reference unknown adapters: {names}")

    @property
    def routes(self) -> tuple[ModelRoute, ...]:
        return self._routes

    @property
    def feature_budgets_usd(self) -> dict[str, float]:
        return dict(self._feature_budgets_usd)

    @property
    def quota_guard(self) -> FeatureQuotaGuard | None:
        return self._quota_guard

    def set_quota_guard(self, guard: FeatureQuotaGuard | None) -> None:
        """Attach per-plan feature quotas once the billing repositories exist."""
        self._quota_guard = guard

    def check_feature_quota(
        self,
        *,
        route_id: str,
        context: ModelCallContext | None,
        quantity: float,
    ) -> None:
        """Refuse up front when a job of several calls would not fit its quota.

        Each call is still checked and recorded on its own; this only avoids paying
        for the first calls of a job whose remaining calls would be refused.
        """
        route = next((item for item in self._routes if item.route_id == route_id), None)
        context = context or ModelCallContext()
        if route is None or not self._quota_applies(route, context):
            return
        self._enforce_quota(route=route, context=context, quantity=quantity)

    def _quota_applies(self, route: ModelRoute, context: ModelCallContext) -> bool:
        guard = self._quota_guard
        return guard is not None and bool(guard.applies(route=route, context=context))

    def _enforce_quota(
        self, *, route: ModelRoute, context: ModelCallContext, quantity: float
    ) -> None:
        guard = self._quota_guard
        if guard is None:
            return
        try:
            guard.check(route=route, context=context, quantity=quantity)
        except FeatureQuotaError:
            raise
        except Exception as exc:
            logger.warning(
                "model_feature_quota_check_failed",
                extra={
                    "route_id": route.route_id,
                    "feature": route.budget_feature,
                    "error_type": type(exc).__name__,
                },
            )
            raise FeatureQuotaCheckFailed(
                "The feature quota could not be checked.", feature=route.budget_feature
            ) from exc

    def _record_quota_usage(
        self,
        *,
        route: ModelRoute,
        context: ModelCallContext,
        quantity: float,
        actual_cost_usd: float,
    ) -> None:
        guard = self._quota_guard
        if guard is None:
            return
        try:
            guard.record(
                route=route,
                context=context,
                quantity=quantity,
                actual_cost_usd=actual_cost_usd,
            )
        except Exception as exc:
            # The provider call already succeeded and was paid for; losing the
            # usage row under-counts the quota but must not fail the job.
            logger.warning(
                "model_feature_quota_record_failed",
                extra={
                    "route_id": route.route_id,
                    "feature": route.budget_feature,
                    "error_type": type(exc).__name__,
                },
            )

    def has_adapter(self, name: str) -> bool:
        with self._registration_lock:
            return name in self._adapters

    def register_adapter(self, name: str, adapter: ModelAdapter) -> None:
        if not name.strip():
            raise ValueError("adapter name cannot be empty")
        with self._registration_lock:
            if name in self._adapters:
                raise ValueError(f"model adapter is already registered: {name}")
            self._adapters[name] = adapter

    def register_route(self, route: ModelRoute) -> None:
        with self._registration_lock:
            if route.adapter not in self._adapters:
                raise ValueError(f"model route references unknown adapter: {route.adapter}")
            if any(item.route_id == route.route_id for item in self._routes):
                raise ValueError(f"model route_id is already registered: {route.route_id}")
            self._routes = tuple(sorted((*self._routes, route), key=lambda item: item.priority))

    def invoke(
        self,
        request: ModelRequest,
        *,
        context: ModelCallContext | None = None,
    ) -> GatewayResult:
        context = context or ModelCallContext()
        capability = capability_for_request(request)
        operation = operation_for_request(request)
        context = ModelCallContext(
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            request_id=context.request_id,
            idempotency_key=context.idempotency_key,
            budget_usd=context.budget_usd,
            required_route_id=context.required_route_id,
            required_model_revision=context.required_model_revision,
            operation=operation,
        )
        routes = [
            route
            for route in self._routes
            if route.capability == capability
            and (not route.operations or operation in route.operations)
            and (context.required_route_id is None or route.route_id == context.required_route_id)
            and (
                context.required_model_revision is None
                or route.model_revision == context.required_model_revision
            )
        ]
        if not routes:
            raise NoModelRoute(capability)

        failures: list[dict[str, str]] = []
        request_spend = 0.0
        for fallback_count, route in enumerate(routes):
            estimated_usage = ModelUsage(
                input_units=estimate_input_units(request),
                output_units=min(request_max_output_units(request), route.max_output_units),
            )
            estimated_cost = (
                0.0
                if isinstance(request, VideoGenerationPollRequest)
                else route_cost_usd(route, estimated_usage)
            )
            request_limit = (
                self._request_budget_usd if context.budget_usd is None else context.budget_usd
            )
            if request_limit > 0 and request_spend + estimated_cost > request_limit + 1e-9:
                failures.append({"route_id": route.route_id, "code": ModelBudgetExceeded.code})
                self._record_attempt(
                    context=context,
                    route=route,
                    success=False,
                    latency_ms=0,
                    error_code=ModelBudgetExceeded.code,
                    fallback_count=fallback_count,
                    estimated_cost_usd=estimated_cost,
                    actual_cost_usd=0.0,
                    usage=ModelUsage(),
                )
                continue

            if self._live_effects_gate is not None and route.requires_live_effects_gate:
                # This decision happens before both shared-budget reservation and
                # adapter invocation. Native routes do not require a remote gate.
                self._live_effects_gate.authorize_model(
                    provider=route.provider,
                    endpoint_host=route.endpoint_host,
                    estimated_cost_usd=estimated_cost,
                    explicitly_free=route.explicitly_free,
                )

            try:
                self._circuit_store.check(route.circuit_key)
            except SharedCircuitOpen:
                failures.append({"route_id": route.route_id, "code": SharedCircuitOpen.code})
                self._record_attempt(
                    context=context,
                    route=route,
                    success=False,
                    latency_ms=0,
                    error_code=SharedCircuitOpen.code,
                    fallback_count=fallback_count,
                    estimated_cost_usd=estimated_cost,
                    actual_cost_usd=0.0,
                    usage=ModelUsage(),
                )
                continue

            quota_units: float | None = None
            if self._quota_applies(route, context):
                quota_units = feature_quota_units(request)
                try:
                    self._enforce_quota(route=route, context=context, quantity=quota_units)
                except FeatureQuotaError as exc:
                    failures.append({"route_id": route.route_id, "code": exc.code})
                    self._record_attempt(
                        context=context,
                        route=route,
                        success=False,
                        latency_ms=0,
                        error_code=exc.code,
                        fallback_count=fallback_count,
                        estimated_cost_usd=estimated_cost,
                        actual_cost_usd=0.0,
                        usage=ModelUsage(),
                    )
                    continue

            reservations: list[BudgetReservation] = []
            budget_error_code = ModelBudgetExceeded.code
            try:
                reservations.append(
                    self._budget_ledger.reserve(
                        scope=budget_scope(context.workspace_id),
                        amount_usd=estimated_cost,
                        limit_usd=self._monthly_budget_usd,
                    )
                )
                feature_limit = (
                    self._feature_budgets_usd.get(route.budget_feature)
                    if route.budget_feature is not None
                    else None
                )
                # Zero-cost attempts cannot consume a dollar cap.
                if feature_limit is not None and estimated_cost > 0:
                    budget_error_code = "model_feature_budget_exceeded"
                    reservations.append(
                        self._budget_ledger.reserve(
                            scope=feature_budget_scope(str(route.budget_feature)),
                            amount_usd=estimated_cost,
                            limit_usd=feature_limit,
                        )
                    )
            except ModelBudgetExceeded:
                for reservation in reservations:
                    self._budget_ledger.release(reservation)
                failures.append({"route_id": route.route_id, "code": ModelBudgetExceeded.code})
                self._record_attempt(
                    context=context,
                    route=route,
                    success=False,
                    latency_ms=0,
                    error_code=budget_error_code,
                    fallback_count=fallback_count,
                    estimated_cost_usd=estimated_cost,
                    actual_cost_usd=0.0,
                    usage=ModelUsage(),
                )
                continue

            started = time.perf_counter()
            active_token = _ACTIVE_CALL_CONTEXT.set(context)
            try:
                adapter_result = self._adapters[route.adapter].invoke(route=route, request=request)
            except Exception as exc:
                latency_ms = round((time.perf_counter() - started) * 1000)
                error_info = classify_provider_exception(exc)
                if isinstance(exc, SharedCircuitOpen):
                    error_info = ProviderErrorInfo(SharedCircuitOpen.code, True)
                self._circuit_store.record_failure(
                    route.circuit_key,
                    retryable=error_info.should_open_circuit,
                )
                # Remote attempts may be billable; local preflight rejections are not.
                failed_cost = estimated_cost if error_info.billable else 0.0
                self._settle(reservations, actual_usd=failed_cost)
                request_spend += failed_cost
                failures.append({"route_id": route.route_id, "code": error_info.code})
                self._record_attempt(
                    context=context,
                    route=route,
                    success=False,
                    latency_ms=latency_ms,
                    error_code=error_info.code,
                    fallback_count=fallback_count,
                    estimated_cost_usd=estimated_cost,
                    actual_cost_usd=failed_cost,
                    usage=ModelUsage(),
                )
                if not error_info.can_fallback or isinstance(
                    request, VideoGenerationSubmitRequest
                ):
                    break
                continue
            finally:
                _ACTIVE_CALL_CONTEXT.reset(active_token)

            latency_ms = round((time.perf_counter() - started) * 1000)
            actual_cost = (
                0.0
                if isinstance(request, VideoGenerationPollRequest)
                else route_cost_usd(route, adapter_result.usage)
                if adapter_result.usage_reported
                else estimated_cost
            )
            try:
                self._settle(reservations, actual_usd=actual_cost)
            except ModelBudgetExceeded:
                self._record_attempt(
                    context=context,
                    route=route,
                    success=False,
                    latency_ms=latency_ms,
                    error_code="model_actual_cost_exceeded_reservation",
                    fallback_count=fallback_count,
                    estimated_cost_usd=estimated_cost,
                    actual_cost_usd=actual_cost,
                    usage=adapter_result.usage,
                )
                raise
            request_spend += actual_cost
            self._circuit_store.record_success(route.circuit_key)
            self._record_attempt(
                context=context,
                route=route,
                success=True,
                latency_ms=latency_ms,
                error_code=None,
                fallback_count=fallback_count,
                estimated_cost_usd=estimated_cost,
                actual_cost_usd=actual_cost,
                usage=adapter_result.usage,
            )
            if quota_units is not None:
                self._record_quota_usage(
                    route=route,
                    context=context,
                    quantity=quota_units,
                    actual_cost_usd=actual_cost,
                )
            metadata = ModelInvocationMetadata(
                request_id=context.request_id,
                capability=capability,
                operation=operation,
                route_id=route.route_id,
                provider=route.provider,
                model=route.model,
                model_revision=route.model_revision,
                provider_request_id=adapter_result.provider_request_id,
                fallback_count=fallback_count,
                latency_ms=latency_ms,
                usage=adapter_result.usage,
                estimated_cost_usd=estimated_cost,
                actual_cost_usd=actual_cost,
            )
            return GatewayResult(output=adapter_result.output, metadata=metadata)

        if failures and all(item["code"] == ModelBudgetExceeded.code for item in failures):
            raise ModelBudgetExceeded("No configured model route fits the dollar budget.")
        if failures and all(item["code"] in QUOTA_FAILURE_CODES for item in failures):
            feature = next(
                (route.budget_feature for route in routes if route.budget_feature), None
            )
            if all(item["code"] == FeatureQuotaExceeded.code for item in failures):
                raise FeatureQuotaExceeded(
                    "The workspace plan's quota for this feature is used up.", feature=feature
                )
            raise FeatureQuotaCheckFailed(
                "The feature quota could not be checked.", feature=feature
            )
        raise ModelProvidersExhausted(capability, failures)

    def _settle(self, reservations: Sequence[BudgetReservation], *, actual_usd: float) -> None:
        """Commit every reservation, then surface the first overrun (if any)."""
        overrun: ModelBudgetExceeded | None = None
        for reservation in reservations:
            try:
                self._budget_ledger.commit(reservation, actual_usd=actual_usd)
            except ModelBudgetExceeded as exc:
                overrun = overrun or exc
        if overrun is not None:
            raise overrun

    def _record_attempt(self, **values: object) -> None:
        self._telemetry.record_attempt(**values)  # type: ignore[arg-type]


def capability_for_request(request: ModelRequest) -> ModelCapability:
    mapping = {
        PlanningRequest: ModelCapability.PLANNING,
        TranscriptionRequest: ModelCapability.TRANSCRIPTION,
        VisionRequest: ModelCapability.VISION,
        AudioRequest: ModelCapability.AUDIO,
        EmbeddingsRequest: ModelCapability.EMBEDDINGS,
        GenerationRequest: ModelCapability.GENERATION,
        VisualManifestRequest: ModelCapability.VISION,
        AudioIntelligenceRequest: ModelCapability.AUDIO,
        ImageEmbeddingsRequest: ModelCapability.EMBEDDINGS,
        VideoGenerationSubmitRequest: ModelCapability.GENERATION,
        VideoGenerationPollRequest: ModelCapability.GENERATION,
        MatteSegmentationRequest: ModelCapability.VISION,
        StemSeparationRequest: ModelCapability.AUDIO,
        DiarizationRequest: ModelCapability.AUDIO,
        ObjectDetectionRequest: ModelCapability.VISION,
    }
    for request_type, capability in mapping.items():
        if isinstance(request, request_type):
            return capability
    raise TypeError(f"unsupported model request type: {type(request).__name__}")


def operation_for_request(request: ModelRequest) -> ModelOperation:
    mapping = {
        PlanningRequest: ModelOperation.PLANNING_CHAT,
        TranscriptionRequest: ModelOperation.TRANSCRIPTION_SPEECH_TO_TEXT,
        VisionRequest: ModelOperation.VISION_PROMPT,
        AudioRequest: ModelOperation.AUDIO_PROMPT,
        EmbeddingsRequest: ModelOperation.EMBEDDINGS_TEXT,
        GenerationRequest: ModelOperation.GENERATION_IMAGE,
        VisualManifestRequest: ModelOperation.VISION_VIDEO_MANIFEST,
        AudioIntelligenceRequest: ModelOperation.AUDIO_INTELLIGENCE,
        ImageEmbeddingsRequest: ModelOperation.EMBEDDINGS_IMAGE,
        VideoGenerationSubmitRequest: ModelOperation.GENERATION_VIDEO_SUBMIT,
        VideoGenerationPollRequest: ModelOperation.GENERATION_VIDEO_POLL,
        MatteSegmentationRequest: ModelOperation.VISION_MATTE_SEGMENTATION,
        StemSeparationRequest: ModelOperation.AUDIO_STEM_SEPARATION,
        DiarizationRequest: ModelOperation.AUDIO_DIARIZATION,
        ObjectDetectionRequest: ModelOperation.VISION_OBJECT_DETECTION,
    }
    for request_type, operation in mapping.items():
        if isinstance(request, request_type):
            return operation
    raise TypeError(f"unsupported model request type: {type(request).__name__}")


def operation_for_capability_route(route: ModelRoute, context: ModelCallContext) -> str:
    if context.operation is not None:
        return context.operation.value
    return route.capability.value


def feature_quota_units(request: ModelRequest) -> float:
    """Units a per-plan feature quota counts for one request.

    Images for vision, inputs for embeddings, and seconds of audio for audio
    features, so plan limits read the same way whatever the vendor bills by.
    """
    if isinstance(request, ObjectDetectionRequest | ImageEmbeddingsRequest):
        return float(len(request.images))
    if isinstance(request, VisionRequest):
        return float(len(request.image_urls))
    if isinstance(request, EmbeddingsRequest):
        return float(len(request.inputs))
    if isinstance(request, DiarizationRequest):
        return float(request.duration_s)
    if isinstance(request, StemSeparationRequest | AudioRequest):
        return float(request.duration_s or 0.0)
    return float(estimate_input_units(request))


def estimate_input_units(request: ModelRequest) -> int:
    if isinstance(request, PlanningRequest):
        return max(
            1,
            sum(len(message.content.encode("utf-8")) for message in request.messages),
        )
    if isinstance(request, VisionRequest):
        return max(1, len(request.prompt.encode("utf-8")) + len(request.image_urls) * 4096)
    if isinstance(request, AudioRequest):
        return max(1, request.audio_path.stat().st_size // 32)
    if isinstance(request, TranscriptionRequest):
        return max(1, request.audio_path.stat().st_size // 32)
    if isinstance(request, EmbeddingsRequest):
        return max(1, sum(len(item.encode("utf-8")) for item in request.inputs))
    if isinstance(request, GenerationRequest):
        return max(
            1,
            len(request.prompt.encode("utf-8")) + len(request.image_urls) * 4096,
        )
    if isinstance(request, VisualManifestRequest):
        return max(1, request.source_path.stat().st_size // 1024)
    if isinstance(request, AudioIntelligenceRequest):
        return max(1, request.audio_path.stat().st_size // 32)
    if isinstance(request, ImageEmbeddingsRequest):
        return max(1, sum(len(image) for image in request.images) // 1024)
    if isinstance(request, VideoGenerationSubmitRequest):
        return max(1, len(request.prompt.encode("utf-8")))
    if isinstance(request, VideoGenerationPollRequest):
        return 0
    if isinstance(request, MatteSegmentationRequest):
        return max(1, request.source_path.stat().st_size // 1024)
    if isinstance(request, StemSeparationRequest):
        if request.duration_s is not None:
            return stem_duration_units(request.duration_s)
        return max(1, request.source_path.stat().st_size // 1024)
    if isinstance(request, DiarizationRequest):
        return diarization_billable_seconds(request)
    if isinstance(request, ObjectDetectionRequest):
        # Billed per image: one input unit per image.
        return len(request.images)
    return 1


def diarization_billable_seconds(request: DiarizationRequest) -> int:
    """Whole seconds of audio (rounded up), never below the provider's job minimum."""
    return max(1, request.minimum_billable_s, math.ceil(request.duration_s))


def stem_duration_units(duration_s: float) -> int:
    """Whole seconds of audio, rounded up (the per-second pricing unit)."""
    return max(1, math.ceil(duration_s))


def request_max_output_units(request: ModelRequest) -> int:
    if isinstance(request, PlanningRequest | VisionRequest | AudioRequest):
        return request.max_output_units
    if isinstance(request, EmbeddingsRequest):
        return request.dimensions or 1536
    if isinstance(request, GenerationRequest):
        return request.count
    if isinstance(request, ImageEmbeddingsRequest):
        return len(request.images)
    if isinstance(request, VideoGenerationSubmitRequest):
        return request.duration_s
    return 0


def route_cost_usd(route: ModelRoute, usage: ModelUsage) -> float:
    return max(
        0.0,
        route.fixed_cost_usd
        + usage.input_units * route.input_cost_per_million_usd / 1_000_000
        + usage.output_units * route.output_cost_per_million_usd / 1_000_000,
    )


def budget_scope(workspace_id: object | None, *, now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    owner = str(workspace_id) if workspace_id is not None else "unattributed"
    return f"{owner}:{current.year:04d}-{current.month:02d}"


def feature_budget_scope(feature: str, *, now: datetime | None = None) -> str:
    """Deployment-wide monthly scope for one budget feature (all workspaces)."""
    current = now or datetime.now(UTC)
    return f"feature:{feature}:{current.year:04d}-{current.month:02d}"


def provider_failure(code: str, *, retryable: bool = False) -> ProviderCallFailure:
    return ProviderCallFailure(
        provider="model_gateway",
        error_info=ProviderErrorInfo(code=code, retryable=retryable),
    )


def route_catalog_fingerprint(
    gateway: ModelGateway,
    *,
    capability: ModelCapability,
    operation: ModelOperation,
) -> str:
    identity = "|".join(
        f"{route.route_id}:{route.model}:{route.model_revision}:{route.priority}"
        for route in gateway.routes
        if route.capability == capability
        and (not route.operations or operation in route.operations)
    )
    return f"gateway:{hashlib.sha256(identity.encode()).hexdigest()}"


def validate_gateway_operations(
    gateway: ModelGateway | None,
    *,
    required: Sequence[ModelOperation],
    runtime: str,
) -> None:
    """Fail closed unless runtime-registered routes can execute every operation."""
    if gateway is None:
        raise RuntimeError(f"Model gateway is required for {runtime}")
    available = {
        operation
        for route in gateway.routes
        for operation in route.operations
    }
    missing = sorted(set(required) - available, key=lambda item: item.value)
    if missing:
        names = ", ".join(item.value for item in missing)
        raise RuntimeError(f"Model gateway for {runtime} is missing operations: {names}")


def is_immutable_model_revision(value: str, *, model: str | None = None) -> bool:
    normalized = value.strip().lower()
    if not normalized or normalized == (model or "").strip().lower():
        return False
    if any(marker in normalized for marker in ("latest", "provider-managed", "floating")):
        return False
    return bool(
        re.fullmatch(r"sha256:[0-9a-f]{64}", normalized)
        or re.fullmatch(r"[a-z0-9_.-]+==\d+(?:\.\d+)*", normalized)
        or re.search(r"\d{4}-\d{2}-\d{2}", normalized)
        or re.search(r"(?:^|[-@/:])v\d+(?:[.-]\d+)*$", normalized)
        or re.search(r"(?:^|[-@/:])[0-9a-f]{8,64}$", normalized)
    )
