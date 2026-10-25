"""Admin route-test action (plan Task 22, Section 2.7).

``run_route_test`` drives one configured route with a tiny, typed **synthetic**
sample for its capability and reports schema validity, latency, usage, estimated
and actual cost, the gateway error code and the route's circuit state. It never
sends real user data.

Safety rules (plan Task 22):

* A **per-test dollar cost ceiling** is passed as the gateway call budget, so a
  misconfigured price cannot run away.
* A **separate operator budget scope** is used (a synthetic admin workspace id),
  so a test never consumes a real workspace's monthly budget.
* A **paid async submit** (an ``async_submit`` route that is not
  ``explicitly_free``) is refused unless the caller passes ``allow_paid=true``:
  an async submit may create a real remote job, so it must be confirmed.
* Tests of **hard-capped** routes count against the cap exactly like a real
  call, because they go through the same gateway path.
* A **malformed provider response** is reported as ``schema_invalid`` (the
  adapters raise ``provider_malformed_response`` for it), never as a crash.

The call is **route-affine**: the gateway is bound to the single route under
test via ``required_route_id``/``required_model_revision``, so a test never
falls back to another route and the result describes exactly that route.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.inference_capabilities import InferenceCapability
from app.services.model_gateway import (
    AudioRequest,
    DiarizationRequest,
    EmbeddingsRequest,
    GenerationRequest,
    ImageEmbeddingsRequest,
    ModelBudgetExceeded,
    ModelCallContext,
    ModelGateway,
    ModelGatewayError,
    ModelProvidersExhausted,
    ModelRoute,
    ObjectDetectionRequest,
    PlanningRequest,
    SoundClassificationRequest,
    StemSeparationRequest,
    TranscriptionRequest,
    VideoGenerationSubmitRequest,
    VisionRequest,
)
from app.services.model_gateway import (
    ChatMessage as _ChatMessage,
)

# A dedicated, synthetic operator workspace id for route tests. It is never a
# real tenant, so a test's reservation/quotas use an isolated budget scope.
OPERATOR_TEST_WORKSPACE = "admin-route-test"

# The gateway error code every adapter raises for a malformed/unparseable
# response. It maps to ``schema_invalid`` in the report (plan Task 22).
_SCHEMA_INVALID_CODE = "provider_malformed_response"

# A tiny, deterministic 1x1 PNG for image-input capabilities (no user data).
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
    b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@dataclass(frozen=True)
class RouteTestOutcome:
    """The result of one synthetic route test (plan Task 22)."""

    route_id: str
    capability: str
    status: str  # "ok" | "schema_invalid" | "error"
    schema_valid: bool
    latency_ms: int | None
    usage: dict[str, int]
    estimated_cost_usd: float
    actual_cost_usd: float
    cost_unknown: bool
    error_code: str | None
    circuit_state: str
    paid_confirmation_required: bool = False
    operation_id: str | None = None


class RouteTestError(RuntimeError):
    """A route test could not be run (unknown route or unsupported capability)."""


def _synthetic_request(capability: InferenceCapability, *, audio_path: Path):
    """Build the tiny typed synthetic sample for ``capability`` (no user data)."""
    if capability == InferenceCapability.PLANNING_CHAT:
        return PlanningRequest(
            messages=(_ChatMessage(role="user", content="ping"),),
            max_output_units=16,
        )
    if capability == InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT:
        return TranscriptionRequest(audio_path=audio_path, language="en")
    if capability == InferenceCapability.VISION_CAPTION:
        return VisionRequest(
            prompt="Describe this image.",
            image_urls=("https://cdn.example.com/route-test.png",),
            max_output_units=16,
        )
    if capability == InferenceCapability.VISION_OBJECT_DETECTION:
        return ObjectDetectionRequest(images=(_TINY_PNG,), max_results=1)
    if capability == InferenceCapability.EMBEDDINGS_TEXT:
        return EmbeddingsRequest(inputs=("route test",))
    if capability == InferenceCapability.EMBEDDINGS_IMAGE:
        return ImageEmbeddingsRequest(images=(_TINY_PNG,))
    if capability == InferenceCapability.AUDIO_DIARIZATION:
        return DiarizationRequest(audio_path=audio_path, duration_s=1.0)
    if capability == InferenceCapability.AUDIO_EMOTION:
        return AudioRequest(
            prompt="Classify the emotion.",
            audio_path=audio_path,
            max_output_units=16,
            duration_s=1.0,
        )
    if capability == InferenceCapability.AUDIO_STEM_SEPARATION:
        return StemSeparationRequest(
            source_path=audio_path, model="route-test", duration_s=1.0
        )
    if capability == InferenceCapability.AUDIO_SOUND_CLASSIFICATION:
        return SoundClassificationRequest(
            audio_path=audio_path, max_results=1, duration_s=1.0
        )
    if capability == InferenceCapability.IMAGE_GENERATION:
        return GenerationRequest(prompt="a tiny test swatch", count=1)
    if capability == InferenceCapability.VIDEO_GENERATION_SUBMIT:
        return VideoGenerationSubmitRequest(
            prompt="a tiny test clip",
            prompt_image_url=None,
            duration_s=1,
            ratio="16:9",
            idempotency_key="admin-route-test",
        )
    # ``video.generation.status`` is a poll of an existing operation; there is
    # no standalone synthetic sample, so it cannot be probed on its own.
    raise RouteTestError(
        f"capability {capability.value!r} has no synthetic route-test sample"
    )


def _route_is_paid(route: ModelRoute) -> bool:
    """A route is paid unless it is explicitly free (plan Section 2.6)."""
    return not route.explicitly_free


def run_route_test(
    *,
    gateway: ModelGateway,
    route: ModelRoute,
    max_cost_usd: float,
    allow_paid: bool,
    audio_path: Path | None = None,
) -> RouteTestOutcome:
    """Run one synthetic test against ``route`` through ``gateway`` (plan Task 22).

    ``route`` must be a live gateway route (``gateway.all_routes``). The call is
    bound to this route only (route-affine); it never falls back.
    """
    capability_id = route.capability_id or ""
    try:
        capability = InferenceCapability(capability_id)
    except ValueError as exc:
        raise RouteTestError(
            f"route {route.route_id!r} has no fine-grained capability id"
        ) from exc

    circuit_state = gateway.circuit_state(route)

    # A paid async submit may create a real remote job, so it needs explicit
    # confirmation (plan Task 22). This check happens BEFORE the gateway is
    # contacted, so no request leaves the process without confirmation.
    if route.async_submit and _route_is_paid(route) and not allow_paid:
        return RouteTestOutcome(
            route_id=route.route_id,
            capability=capability_id,
            status="error",
            schema_valid=False,
            latency_ms=None,
            usage={},
            estimated_cost_usd=0.0,
            actual_cost_usd=0.0,
            cost_unknown=route.pricing_status == "unknown",
            error_code="paid_confirmation_required",
            circuit_state=circuit_state,
            paid_confirmation_required=True,
        )

    sample_audio = audio_path or Path("/dev/null")
    request = _synthetic_request(capability, audio_path=sample_audio)

    context = ModelCallContext(
        workspace_id=OPERATOR_TEST_WORKSPACE,
        # The per-test ceiling is the request budget; a price above it refuses
        # before the provider is contacted.
        budget_usd=max_cost_usd,
        # Bind to exactly this route and revision (route-affine, invariant 6).
        required_route_id=route.route_id,
        required_model_revision=route.model_revision,
    )

    try:
        result = gateway.invoke(request, context=context)
    except ModelProvidersExhausted as exc:
        code = _first_failure_code(exc) or exc.code
        return _failure_outcome(
            route=route,
            capability_id=capability_id,
            error_code=code,
            circuit_state=gateway.circuit_state(route),
        )
    except ModelBudgetExceeded as exc:
        return _failure_outcome(
            route=route,
            capability_id=capability_id,
            error_code=exc.code,
            circuit_state=gateway.circuit_state(route),
        )
    except ModelGatewayError as exc:
        return _failure_outcome(
            route=route,
            capability_id=capability_id,
            error_code=getattr(exc, "code", "model_gateway_error"),
            circuit_state=gateway.circuit_state(route),
        )

    metadata = result.metadata
    operation_id = metadata.provider_request_id if route.async_submit else None
    return RouteTestOutcome(
        route_id=route.route_id,
        capability=capability_id,
        status="ok",
        schema_valid=True,
        latency_ms=metadata.latency_ms,
        usage={
            "input_units": metadata.usage.input_units,
            "output_units": metadata.usage.output_units,
            "total_units": metadata.usage.total_units,
        },
        estimated_cost_usd=round(metadata.estimated_cost_usd, 8),
        actual_cost_usd=round(metadata.actual_cost_usd, 8),
        cost_unknown=route.pricing_status == "unknown",
        error_code=None,
        circuit_state=gateway.circuit_state(route),
        operation_id=operation_id,
    )


def _first_failure_code(exc: ModelProvidersExhausted) -> str | None:
    failures = getattr(exc, "failures", ())
    for item in failures:
        code = item.get("code")
        if code:
            return str(code)
    return None


def _failure_outcome(
    *,
    route: ModelRoute,
    capability_id: str,
    error_code: str,
    circuit_state: str,
) -> RouteTestOutcome:
    schema_invalid = error_code == _SCHEMA_INVALID_CODE
    return RouteTestOutcome(
        route_id=route.route_id,
        capability=capability_id,
        status="schema_invalid" if schema_invalid else "error",
        schema_valid=not schema_invalid,
        latency_ms=None,
        usage={},
        estimated_cost_usd=0.0,
        actual_cost_usd=0.0,
        cost_unknown=route.pricing_status == "unknown",
        error_code=error_code,
        circuit_state=circuit_state,
    )


__all__ = [
    "OPERATOR_TEST_WORKSPACE",
    "RouteTestError",
    "RouteTestOutcome",
    "run_route_test",
]
