"""Contract tests for the shared fake inference adapter (Task 1).

With every vendor API key cleared and the network blocked by the conftest guard,
a real ``ModelGateway`` driven by ``DeterministicInferenceAdapter`` must:

- produce a schema-valid typed output for every request type in the current
  ``ModelRequest`` union;
- select routes by priority and fall back on retryable pre-send failures;
- honour dollar budgets, the per-feature quota guard and the shared circuit;
- map every scripted failure mode to the documented gateway error;
- run async submit then status against the in-memory job store;
- never fall back to a second route after a video submit failure.

Where current behaviour differs from the plan's end state, the test asserts
today's behaviour and points at Task 16A.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.schemas.uploads import VideoMetadata
from app.services.inference_fake import (
    DeterministicInferenceAdapter,
    ProductionAdapterRejected,
    ScriptedOutcome,
    supported_request_types,
)
from app.services.model_gateway import (
    AudioIntelligenceOutput,
    AudioIntelligenceRequest,
    AudioOutput,
    AudioRequest,
    ChatMessage,
    DiarizationOutput,
    DiarizationRequest,
    EmbeddingsOutput,
    EmbeddingsRequest,
    FeatureQuotaExceeded,
    GenerationOperationOutput,
    GenerationOutput,
    GenerationRequest,
    ImageEmbeddingsRequest,
    InMemoryCircuitStore,
    MatteSegmentationOutput,
    MatteSegmentationRequest,
    ModelBudgetExceeded,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelProvidersExhausted,
    ModelRoute,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    PlanningOutput,
    PlanningRequest,
    StemSeparationOutput,
    StemSeparationRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    VideoGenerationPollRequest,
    VideoGenerationSubmitRequest,
    VisionOutput,
    VisionRequest,
    VisualManifestOutput,
    VisualManifestRequest,
    capability_for_request,
)


# Every ``*_API_KEY`` setting alias in app/core/config.py is cleared so no real
# credential is present (plan acceptance for Task 1). The names are discovered by
# introspecting the Settings model rather than hard-coded, so this file carries
# no provider identifiers and stays correct as settings change.
def _api_key_env_aliases() -> tuple[str, ...]:
    from app.core.config import Settings

    aliases: list[str] = []
    for info in Settings.model_fields.values():
        alias = info.alias or ""
        if alias.endswith("_API_KEY"):
            aliases.append(alias)
    return tuple(sorted(aliases))


@pytest.fixture(autouse=True)
def _clear_vendor_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _api_key_env_aliases():
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def _media_file(tmp_path: Path, name: str = "clip.wav") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x00" * 4096)
    return path


def _route(
    route_id: str,
    capability: ModelCapability,
    *,
    priority: int = 100,
    fixed_cost_usd: float = 0.0,
    budget_feature: str | None = None,
    provider: str | None = None,
) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        capability=capability,
        adapter="fake",
        provider=provider or route_id,
        model=f"{route_id}-model",
        model_revision=f"{route_id}-2026-07-14",
        priority=priority,
        fixed_cost_usd=fixed_cost_usd,
        budget_feature=budget_feature,
    )


def _gateway(routes, adapter, **kwargs) -> ModelGateway:
    return ModelGateway(
        adapters={"fake": adapter},
        routes=routes,
        request_budget_usd=kwargs.pop("request_budget_usd", 100.0),
        monthly_budget_usd=kwargs.pop("monthly_budget_usd", 1000.0),
        **kwargs,
    )


def _requests(tmp_path: Path):
    """One request of every ModelRequest type, with its capability and output."""
    audio = _media_file(tmp_path, "audio.wav")
    video = _media_file(tmp_path, "video.mp4")
    image = b"\xff\xd8\xff" + b"\x00" * 64
    metadata = VideoMetadata(duration_ms=1000, width=1920, height=1080, fps=30.0)
    return [
        (
            PlanningRequest(messages=(ChatMessage(role="user", content="plan"),)),
            ModelCapability.PLANNING,
            PlanningOutput,
        ),
        (
            TranscriptionRequest(audio_path=audio, language="en"),
            ModelCapability.TRANSCRIPTION,
            TranscriptionOutput,
        ),
        (
            VisionRequest(prompt="describe", image_urls=("https://x.invalid/f.jpg",)),
            ModelCapability.VISION,
            VisionOutput,
        ),
        (
            AudioRequest(prompt="classify", audio_path=audio, duration_s=4.0),
            ModelCapability.AUDIO,
            AudioOutput,
        ),
        (
            EmbeddingsRequest(inputs=("a", "b"), dimensions=8),
            ModelCapability.EMBEDDINGS,
            EmbeddingsOutput,
        ),
        (
            GenerationRequest(prompt="make b-roll", count=2),
            ModelCapability.GENERATION,
            GenerationOutput,
        ),
        (
            VisualManifestRequest(
                source_path=video,
                metadata=metadata,
                sample_rate_fps=2.0,
                scene_threshold=0.3,
            ),
            ModelCapability.VISION,
            VisualManifestOutput,
        ),
        (
            AudioIntelligenceRequest(audio_path=audio),
            ModelCapability.AUDIO,
            AudioIntelligenceOutput,
        ),
        (
            ImageEmbeddingsRequest(images=(image, image)),
            ModelCapability.EMBEDDINGS,
            EmbeddingsOutput,
        ),
        (
            MatteSegmentationRequest(
                source_path=video,
                output_path=tmp_path / "matte.mp4",
                metadata=metadata,
                max_dimension=480,
            ),
            ModelCapability.VISION,
            MatteSegmentationOutput,
        ),
        (
            StemSeparationRequest(source_path=audio, model="demucs", duration_s=5.0),
            ModelCapability.AUDIO,
            StemSeparationOutput,
        ),
        (
            DiarizationRequest(audio_path=audio, duration_s=30.0),
            ModelCapability.AUDIO,
            DiarizationOutput,
        ),
        (
            ObjectDetectionRequest(images=(image,)),
            ModelCapability.VISION,
            ObjectDetectionOutput,
        ),
    ]


def test_every_request_type_produces_schema_valid_output(tmp_path: Path) -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    for request, capability, output_type in _requests(tmp_path):
        assert capability_for_request(request) == capability
        gateway = _gateway([_route("primary", capability)], adapter)
        result = gateway.invoke(request, context=ModelCallContext(workspace_id=uuid4()))
        assert isinstance(result.output, output_type), type(request).__name__


def test_dispatch_table_covers_the_whole_request_union(tmp_path: Path) -> None:
    # The fake must build a success output for every type the gateway can route,
    # so Task 3 only needs to add a dispatch entry per new request class.
    covered = supported_request_types()
    for request, _capability, _output in _requests(tmp_path):
        assert type(request) in covered


def test_unknown_request_type_raises_type_error() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")

    class _Unknown:
        pass

    route = _route("primary", ModelCapability.PLANNING)
    with pytest.raises(TypeError):
        adapter.invoke(route=route, request=_Unknown())  # type: ignore[arg-type]


def test_production_construction_is_refused() -> None:
    with pytest.raises(ProductionAdapterRejected):
        DeterministicInferenceAdapter(app_env="production")


def _planning_request() -> PlanningRequest:
    return PlanningRequest(messages=(ChatMessage(role="user", content="plan"),))


def test_route_selection_prefers_lowest_priority_number() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    routes = [
        _route("secondary", ModelCapability.PLANNING, priority=20),
        _route("primary", ModelCapability.PLANNING, priority=10),
    ]
    gateway = _gateway(routes, adapter)
    result = gateway.invoke(_planning_request())
    assert result.metadata.route_id == "primary"
    assert [call.route_id for call in adapter.calls] == ["primary"]


def test_priority_fallback_on_retryable_presend_failure() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.set_default("primary", ScriptedOutcome.TIMEOUT_BEFORE_SEND)
    routes = [
        _route("primary", ModelCapability.PLANNING, priority=10),
        _route("secondary", ModelCapability.PLANNING, priority=20),
    ]
    gateway = _gateway(routes, adapter)
    result = gateway.invoke(_planning_request())
    assert result.metadata.route_id == "secondary"
    assert result.metadata.fallback_count == 1
    assert [call.route_id for call in adapter.calls] == ["primary", "secondary"]


@pytest.mark.parametrize(
    ("outcome", "expected_code"),
    [
        (ScriptedOutcome.HTTP_400, "provider_request_rejected"),
        (ScriptedOutcome.HTTP_401, "provider_auth_failed"),
        (ScriptedOutcome.HTTP_403, "provider_auth_failed"),
        (ScriptedOutcome.HTTP_402, "provider_quota_exceeded"),
        (ScriptedOutcome.HTTP_429, "provider_rate_limited"),
        (ScriptedOutcome.HTTP_5XX, "provider_unavailable"),
        (ScriptedOutcome.TIMEOUT_BEFORE_SEND, "provider_timeout"),
        (ScriptedOutcome.TIMEOUT_AFTER_SEND, "provider_timeout"),
        (ScriptedOutcome.SCHEMA_INVALID, "provider_malformed_response"),
    ],
)
def test_scripted_failure_modes_yield_documented_gateway_error(
    outcome: ScriptedOutcome, expected_code: str
) -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.set_default("only", outcome)
    gateway = _gateway([_route("only", ModelCapability.PLANNING)], adapter)
    with pytest.raises(ModelProvidersExhausted) as excinfo:
        gateway.invoke(_planning_request())
    codes = {item["code"] for item in excinfo.value.failures}
    assert codes == {expected_code}


def test_timeout_after_send_records_sent_flag() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.set_default("only", ScriptedOutcome.TIMEOUT_AFTER_SEND)
    gateway = _gateway([_route("only", ModelCapability.PLANNING)], adapter)
    with pytest.raises(ModelProvidersExhausted):
        gateway.invoke(_planning_request())
    assert adapter.calls[-1].sent is True
    # NOTE (Task 16A): today a non-video after-send timeout is a retryable
    # pre-send-class failure and still falls back. 16A adds the ``sent`` flag so
    # the gateway stops falling back after send. Here there is one route, so the
    # distinction is not observable yet; this pins the recorded sent flag only.


def test_timeout_before_send_records_not_sent() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.set_default("only", ScriptedOutcome.TIMEOUT_BEFORE_SEND)
    gateway = _gateway([_route("only", ModelCapability.PLANNING)], adapter)
    with pytest.raises(ModelProvidersExhausted):
        gateway.invoke(_planning_request())
    assert adapter.calls[-1].sent is False


def test_dollar_budget_skips_expensive_route() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    routes = [
        _route("expensive", ModelCapability.PLANNING, priority=1, fixed_cost_usd=2.0),
        _route("cheap", ModelCapability.PLANNING, priority=2, fixed_cost_usd=0.1),
    ]
    gateway = _gateway(routes, adapter, request_budget_usd=1.0)
    result = gateway.invoke(_planning_request())
    assert result.metadata.route_id == "cheap"


def test_feature_budget_exhaustion_raises_budget_error() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _route(
        "paid", ModelCapability.PLANNING, fixed_cost_usd=0.6, budget_feature="planning"
    )
    gateway = _gateway(
        [route], adapter, feature_budgets_usd={"planning": 1.0}
    )
    context = ModelCallContext(workspace_id="workspace-one")
    gateway.invoke(_planning_request(), context=context)
    with pytest.raises(ModelBudgetExceeded):
        gateway.invoke(_planning_request(), context=context)


class _StubQuotaGuard:
    """A minimal FeatureQuotaGuard stand-in for the gateway contract."""

    def __init__(self, *, limit: float) -> None:
        self._limit = limit
        self._used = 0.0
        self.recorded: list[float] = []

    def applies(self, *, route, context) -> bool:  # noqa: ANN001
        return route.budget_feature is not None and context.workspace_id is not None

    def check(self, *, route, context, quantity: float) -> None:  # noqa: ANN001
        del route, context
        if self._used + quantity > self._limit + 1e-9:
            raise FeatureQuotaExceeded("quota used up", feature="embeddings")

    def record(self, *, route, context, quantity: float, actual_cost_usd: float) -> None:  # noqa: ANN001, E501
        del route, context, actual_cost_usd
        self._used += quantity
        self.recorded.append(quantity)


def test_feature_quota_guard_blocks_and_records() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    guard = _StubQuotaGuard(limit=2.0)
    route = _route(
        "emb", ModelCapability.EMBEDDINGS, budget_feature="embeddings"
    )
    gateway = _gateway([route], adapter, quota_guard=guard)
    context = ModelCallContext(workspace_id=uuid4())
    # Two embedding inputs = 2 units; fits the limit and records.
    gateway.invoke(EmbeddingsRequest(inputs=("a", "b")), context=context)
    assert guard.recorded == [2.0]
    # Another two-input call would exceed the quota of 2.0.
    with pytest.raises(FeatureQuotaExceeded):
        gateway.invoke(EmbeddingsRequest(inputs=("c", "d")), context=context)


def test_circuit_opens_after_repeated_retryable_failures() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.set_default("primary", ScriptedOutcome.HTTP_5XX)
    circuits = InMemoryCircuitStore(failure_threshold=1, cooldown_seconds=60)
    routes = [
        _route("primary", ModelCapability.PLANNING, priority=1),
        _route("secondary", ModelCapability.PLANNING, priority=2),
    ]
    gateway = _gateway(routes, adapter, circuit_store=circuits)
    # First call opens the circuit on primary (threshold 1) and falls back.
    first = gateway.invoke(_planning_request())
    assert first.metadata.route_id == "secondary"
    # Second call: the circuit is open, so primary is skipped entirely.
    adapter.calls.clear()
    second = gateway.invoke(_planning_request())
    assert second.metadata.route_id == "secondary"
    assert all(call.route_id == "secondary" for call in adapter.calls)


def test_async_video_submit_then_status_progresses_to_succeeded() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    submit = VideoGenerationSubmitRequest(
        prompt="a sunset",
        prompt_image_url=None,
        duration_s=4,
        ratio="16:9",
        idempotency_key="job-key-1",
    )
    adapter.script_video_poll("job-key-1", ["running", "succeeded"])
    routes = [_route("video", ModelCapability.GENERATION)]
    gateway = _gateway(routes, adapter)

    submit_result = gateway.invoke(submit)
    assert isinstance(submit_result.output, GenerationOperationOutput)
    operation_id = submit_result.output.operation_id

    running = gateway.invoke(VideoGenerationPollRequest(operation_id=operation_id))
    assert isinstance(running.output, GenerationOperationOutput)

    done = gateway.invoke(VideoGenerationPollRequest(operation_id=operation_id))
    assert isinstance(done.output, GenerationOutput)
    assert done.output.artifacts


def test_video_submit_failure_does_not_fall_back_to_another_route() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.set_default("video-a", ScriptedOutcome.HTTP_5XX)
    routes = [
        _route("video-a", ModelCapability.GENERATION, priority=1),
        _route("video-b", ModelCapability.GENERATION, priority=2),
    ]
    gateway = _gateway(routes, adapter)
    submit = VideoGenerationSubmitRequest(
        prompt="a sunset",
        prompt_image_url=None,
        duration_s=4,
        ratio="16:9",
        idempotency_key="job-key-2",
    )
    with pytest.raises(ModelProvidersExhausted):
        gateway.invoke(submit)
    # Only the first route was contacted; a submit is never retried elsewhere.
    assert [call.route_id for call in adapter.calls] == ["video-a"]


# --- protocol-level fake provider server (tests/support) -------------------


def _http_route(route_id: str, capability: ModelCapability) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        capability=capability,
        adapter="openai_compatible",
        provider=route_id,
        model=f"{route_id}-model",
        model_revision=f"{route_id}-2026-07-14",
    )


def test_fake_provider_server_drives_openai_compatible_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.model_adapters import OpenAICompatibleAdapter
    from tests.support.fake_provider_server import FakeProviderServer

    server = FakeProviderServer()
    server.install(monkeypatch)
    adapter = OpenAICompatibleAdapter(base_url="https://example.invalid/v1", api_key="k")
    gateway = ModelGateway(
        adapters={"openai_compatible": adapter},
        routes=[_http_route("primary", ModelCapability.PLANNING)],
        request_budget_usd=100.0,
        monthly_budget_usd=1000.0,
    )
    result = gateway.invoke(_planning_request())
    assert isinstance(result.output, PlanningOutput)
    assert server.requests
    assert server.requests[-1].endpoint == "chat/completions"


def test_fake_provider_server_malformed_and_status_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.model_adapters import OpenAICompatibleAdapter
    from app.services.provider_resilience import ProviderCallFailure
    from tests.support.fake_provider_server import (
        CHAT_COMPLETIONS,
        FakeProviderServer,
        ServerOutcome,
    )

    adapter = OpenAICompatibleAdapter(base_url="https://example.invalid/v1", api_key="k")

    for outcome, expected_code in (
        (ServerOutcome.MALFORMED_JSON, "provider_malformed_response"),
        (ServerOutcome.HTTP_401, "provider_auth_failed"),
        (ServerOutcome.HTTP_429, "provider_rate_limited"),
        (ServerOutcome.TIMEOUT, "provider_timeout"),
    ):
        server = FakeProviderServer()
        server.program(CHAT_COMPLETIONS, outcome=outcome)
        server.install(monkeypatch)
        with pytest.raises(ProviderCallFailure) as excinfo:
            adapter.invoke(
                route=_http_route("primary", ModelCapability.PLANNING),
                request=_planning_request(),
            )
        assert excinfo.value.error_info.code == expected_code


def test_fake_provider_server_transcription_has_word_timestamps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.model_adapters import OpenAICompatibleAdapter
    from tests.support.fake_provider_server import FakeProviderServer

    server = FakeProviderServer()
    server.install(monkeypatch)
    adapter = OpenAICompatibleAdapter(base_url="https://example.invalid/v1", api_key="k")
    audio = _media_file(tmp_path, "speech.wav")
    result = adapter.invoke(
        route=_http_route("stt", ModelCapability.TRANSCRIPTION),
        request=TranscriptionRequest(audio_path=audio, language="en"),
    )
    assert isinstance(result.output, TranscriptionOutput)
    assert result.output.words
    assert result.output.words[0].end_ms > result.output.words[0].start_ms
