from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.api.captions import translate_segments_if_requested
from app.schemas.captions import CaptionSegment, LanguageHint, TranscriptWord
from app.schemas.plans import BrollDensity, PlanCreateRequest
from app.services.model_adapters import AzureOpenAIAdapter, OpenAICompatibleAdapter
from app.services.model_gateway import (
    AdapterResult,
    AudioOutput,
    AudioRequest,
    ChatMessage,
    EmbeddingsOutput,
    EmbeddingsRequest,
    GeneratedArtifact,
    GenerationOutput,
    GenerationRequest,
    InMemoryBudgetLedger,
    InMemoryCircuitStore,
    ModelBudgetExceeded,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelProvidersExhausted,
    ModelRoute,
    ModelUsage,
    NoModelRoute,
    OperationsGatewayTelemetry,
    PlanningOutput,
    PlanningRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    TranscriptWordOutput,
    VisionOutput,
    VisionRequest,
    budget_scope,
    capability_for_request,
)
from app.services.model_gateway_factory import model_gateway_from_settings
from app.services.model_provider_bridges import (
    GatewayEditPlannerProvider,
    GatewayTranscriptionProvider,
)
from app.services.operations import MetricsRegistry, OperationsRepository
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo


class FakeAdapter:
    def __init__(self, outcomes: dict[str, AdapterResult | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def invoke(self, *, route: ModelRoute, request: object) -> AdapterResult:
        del request
        self.calls.append(route.route_id)
        outcome = self.outcomes[route.route_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def planning_result(content: str = "{}", *, input_units: int = 100, output_units: int = 50):
    payload: dict[str, object] = {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": input_units,
            "completion_tokens": output_units,
            "total_tokens": input_units + output_units,
        },
    }
    return AdapterResult(
        output=PlanningOutput(content=content, raw_response=payload),
        usage=ModelUsage(input_units=input_units, output_units=output_units),
        provider_request_id="provider-request",
    )


def route(
    route_id: str,
    *,
    capability: ModelCapability = ModelCapability.PLANNING,
    priority: int = 100,
    fixed_cost_usd: float = 0.0,
    adapter: str = "fake",
    provider: str | None = None,
) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        capability=capability,
        adapter=adapter,
        provider=provider or route_id,
        model=f"{route_id}-model",
        model_revision=f"{route_id}-revision-2026-07-14",
        priority=priority,
        fixed_cost_usd=fixed_cost_usd,
    )


def request() -> PlanningRequest:
    return PlanningRequest(messages=(ChatMessage(role="user", content="plan this video"),))


def test_all_six_capabilities_have_distinct_typed_requests() -> None:
    audio = Path("/tmp/audio.wav")
    cases = {
        PlanningRequest(messages=(ChatMessage(role="user", content="plan"),)): "planning",
        TranscriptionRequest(audio_path=audio): "transcription",
        VisionRequest(prompt="describe", image_urls=("https://example.com/frame.jpg",)): "vision",
        AudioRequest(prompt="classify", audio_path=audio): "audio",
        EmbeddingsRequest(inputs=("semantic query",)): "embeddings",
        GenerationRequest(prompt="generate b-roll"): "generation",
    }

    assert {capability_for_request(item).value for item in cases} == set(cases.values())


def test_routes_require_explicit_immutable_model_revision() -> None:
    with pytest.raises(ValueError, match="model_revision"):
        ModelRoute(
            route_id="primary",
            capability=ModelCapability.PLANNING,
            adapter="fake",
            provider="custom",
            model="planner",
            model_revision="",
        )
    with pytest.raises(ValueError, match="pinned"):
        ModelRoute(
            route_id="floating",
            capability=ModelCapability.PLANNING,
            adapter="fake",
            provider="custom",
            model="planner",
            model_revision="planner@latest",
        )
    with pytest.raises(ValueError, match="pinned"):
        ModelRoute(
            route_id="managed",
            capability=ModelCapability.PLANNING,
            adapter="fake",
            provider="custom",
            model="planner",
            model_revision="planner@provider-managed",
        )


def test_gateway_falls_back_and_returns_actual_route_provenance() -> None:
    failure = ProviderCallFailure(
        provider="primary",
        error_info=ProviderErrorInfo("provider_unavailable", True, 503),
    )
    adapter = FakeAdapter({"primary": failure, "secondary": planning_result("secondary")})
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[route("secondary", priority=20), route("primary", priority=10)],
        request_budget_usd=10,
    )

    result = gateway.invoke(request(), context=ModelCallContext(workspace_id=uuid4()))

    assert isinstance(result.output, PlanningOutput)
    assert result.output.content == "secondary"
    assert adapter.calls == ["primary", "secondary"]
    assert result.metadata.route_id == "secondary"
    assert result.metadata.model_revision == "secondary-revision-2026-07-14"
    assert result.metadata.fallback_count == 1


def test_shared_circuit_state_is_reused_by_multiple_gateway_instances() -> None:
    failure = ProviderCallFailure(
        provider="primary",
        error_info=ProviderErrorInfo("provider_timeout", True),
    )
    adapter = FakeAdapter(
        {
            "primary": failure,
            "secondary": planning_result("fallback"),
        }
    )
    shared_circuits = InMemoryCircuitStore(failure_threshold=1, cooldown_seconds=60)
    routes = [route("primary", priority=1), route("secondary", priority=2)]
    gateways = [
        ModelGateway(
            adapters={"fake": adapter},
            routes=routes,
            circuit_store=shared_circuits,
        )
        for _ in range(2)
    ]

    assert gateways[0].invoke(request()).metadata.route_id == "secondary"
    assert gateways[1].invoke(request()).metadata.route_id == "secondary"
    assert adapter.calls == ["primary", "secondary", "secondary"]


def test_dollar_budget_skips_expensive_route_and_uses_affordable_fallback() -> None:
    adapter = FakeAdapter(
        {
            "expensive": planning_result("expensive"),
            "affordable": planning_result("affordable"),
        }
    )
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[
            route("expensive", priority=1, fixed_cost_usd=2.0),
            route("affordable", priority=2, fixed_cost_usd=0.1),
        ],
        request_budget_usd=1.0,
        monthly_budget_usd=10.0,
    )

    result = gateway.invoke(request())

    assert result.metadata.route_id == "affordable"
    assert adapter.calls == ["affordable"]
    assert result.metadata.fallback_count == 1


def test_monthly_dollar_budget_is_shared_across_gateway_instances() -> None:
    adapter = FakeAdapter({"paid": planning_result("paid")})
    ledger = InMemoryBudgetLedger()
    model_route = route("paid", fixed_cost_usd=0.6)
    gateways = [
        ModelGateway(
            adapters={"fake": adapter},
            routes=[model_route],
            budget_ledger=ledger,
            monthly_budget_usd=1.0,
            request_budget_usd=1.0,
        )
        for _ in range(2)
    ]
    context = ModelCallContext(workspace_id="workspace-one")

    gateways[0].invoke(request(), context=context)
    with pytest.raises(ModelBudgetExceeded):
        gateways[1].invoke(request(), context=context)

    assert adapter.calls == ["paid"]


def test_in_memory_budget_settlement_charges_full_actual_before_raising() -> None:
    ledger = InMemoryBudgetLedger()
    scope = "workspace:2026-07"
    reservation = ledger.reserve(scope=scope, amount_usd=1.0, limit_usd=10.0)

    with pytest.raises(ModelBudgetExceeded, match="reservation"):
        ledger.commit(reservation, actual_usd=2.0)

    assert ledger.usage(scope) == pytest.approx(2.0)
    with pytest.raises(ModelBudgetExceeded, match="exhausted"):
        ledger.reserve(scope=scope, amount_usd=9.0, limit_usd=10.0)


def test_actual_cost_above_reservation_is_rejected_and_cannot_expand_budget() -> None:
    ledger = InMemoryBudgetLedger()
    adapter = FakeAdapter({"paid": planning_result(input_units=2_000_000, output_units=0)})
    paid_route = ModelRoute(
        route_id="paid",
        capability=ModelCapability.PLANNING,
        adapter="fake",
        provider="custom",
        model="planner",
        model_revision="planner-2026-07-14",
        input_cost_per_million_usd=1.0,
        output_cost_per_million_usd=0.0,
    )
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[paid_route],
        budget_ledger=ledger,
        monthly_budget_usd=0.01,
        request_budget_usd=0.01,
    )

    with pytest.raises(ModelBudgetExceeded, match="reservation"):
        gateway.invoke(request(), context=ModelCallContext(workspace_id="workspace-one"))

    assert adapter.calls == ["paid"]
    assert max(ledger._usage.values()) == pytest.approx(2.0)  # noqa: SLF001


def test_missing_usage_keeps_conservative_variable_cost_reservation() -> None:
    adapter = FakeAdapter(
        {
            "unreported": AdapterResult(
                output=PlanningOutput(content="{}", raw_response={"choices": []}),
                usage=ModelUsage(),
                usage_reported=False,
            )
        }
    )
    metered_route = ModelRoute(
        route_id="unreported",
        capability=ModelCapability.PLANNING,
        adapter="fake",
        provider="custom",
        model="planner",
        model_revision="planner-2026-07-14",
        input_cost_per_million_usd=1.0,
        output_cost_per_million_usd=0.0,
    )
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[metered_route],
        monthly_budget_usd=0.00002,
        request_budget_usd=1.0,
    )
    context = ModelCallContext(workspace_id="workspace-one")

    first = gateway.invoke(request(), context=context)
    with pytest.raises(ModelBudgetExceeded):
        gateway.invoke(request(), context=context)

    assert first.metadata.actual_cost_usd == first.metadata.estimated_cost_usd
    assert first.metadata.actual_cost_usd > 0
    assert adapter.calls == ["unreported"]


def test_empty_usage_mapping_keeps_conservative_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request(client, method, url, **kwargs):  # noqa: ANN001
        del client, method, kwargs
        return _response(
            {"choices": [{"message": {"content": "{}"}}], "usage": {}},
            url,
        )

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    adapter = OpenAICompatibleAdapter(base_url="https://models.test/v1", api_key="key")
    metered_route = ModelRoute(
        route_id="metered",
        capability=ModelCapability.PLANNING,
        adapter="openai",
        provider="custom",
        model="planner",
        model_revision="planner-2026-07-14",
        input_cost_per_million_usd=1.0,
    )
    gateway = ModelGateway(
        adapters={"openai": adapter},
        routes=[metered_route],
        monthly_budget_usd=0.00002,
    )
    first = gateway.invoke(request(), context=ModelCallContext(workspace_id="workspace"))

    with pytest.raises(ModelBudgetExceeded):
        gateway.invoke(request(), context=ModelCallContext(workspace_id="workspace"))
    assert first.metadata.actual_cost_usd == first.metadata.estimated_cost_usd


def test_gateway_records_low_cardinality_metrics_and_durable_revision_telemetry() -> None:
    metrics = MetricsRegistry()
    operations = OperationsRepository()
    adapter = FakeAdapter({"primary": planning_result()})
    model_route = ModelRoute(
        route_id="primary",
        capability=ModelCapability.PLANNING,
        adapter="fake",
        provider="azure",
        model="planner-deployment",
        model_revision="planner-deployment-2026-07-14",
        input_cost_per_million_usd=10,
        output_cost_per_million_usd=20,
    )
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[model_route],
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=metrics,
            operations_repository=operations,
        ),
    )

    result = gateway.invoke(
        request(),
        context=ModelCallContext(workspace_id=uuid4(), project_id=uuid4()),
    )

    event = operations.provider_events[0]
    assert event["route_id"] == "primary"
    assert event["model_revision"] == "planner-deployment-2026-07-14"
    assert event["request_id"] == result.metadata.request_id
    assert event["actual_cost_usd"] == pytest.approx(0.002)
    rendered = metrics.render()
    assert 'motionly_model_gateway_latency_ms_count{capability="planning"' in rendered
    assert 'model_revision="planner-deployment-2026-07-14"' in rendered


def _response(payload: dict[str, object], url: str) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        headers={"x-request-id": "recorded-request"},
        request=httpx.Request("POST", url),
    )


def test_openai_compatible_adapter_implements_all_typed_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_request(client, method, url, **kwargs):  # noqa: ANN001
        del client
        calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("/audio/transcriptions"):
            return _response(
                {
                    "text": "Build better",
                    "language": "en",
                    "words": [
                        {"word": "Build", "start": 0.1, "end": 0.4, "confidence": 0.9}
                    ],
                },
                url,
            )
        if url.endswith("/embeddings"):
            return _response(
                {"data": [{"index": 0, "embedding": [0.1, 0.2]}], "usage": {}},
                url,
            )
        if url.endswith("/images/generations"):
            return _response(
                {"id": "generation-1", "data": [{"url": "https://cdn.test/image.png"}]},
                url,
            )
        return _response(
            {
                "choices": [{"message": {"content": "typed response"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            url,
        )

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"RIFF-recorded-audio")
    adapter = OpenAICompatibleAdapter(
        base_url="https://models.motionly.test/v1",
        api_key="secret-test-key",
    )
    requests_and_routes = [
        (request(), route("planning", capability=ModelCapability.PLANNING)),
        (
            TranscriptionRequest(audio_path=audio_path),
            route("transcription", capability=ModelCapability.TRANSCRIPTION),
        ),
        (
            VisionRequest(prompt="describe", image_urls=("https://cdn.test/frame.jpg",)),
            route("vision", capability=ModelCapability.VISION),
        ),
        (
            AudioRequest(prompt="classify", audio_path=audio_path),
            route("audio", capability=ModelCapability.AUDIO),
        ),
        (
            EmbeddingsRequest(inputs=("semantic",)),
            route("embeddings", capability=ModelCapability.EMBEDDINGS),
        ),
        (
            GenerationRequest(prompt="create"),
            route("generation", capability=ModelCapability.GENERATION),
        ),
    ]

    outputs = [
        adapter.invoke(route=item_route, request=item)
        for item, item_route in requests_and_routes
    ]

    assert isinstance(outputs[0].output, PlanningOutput)
    assert isinstance(outputs[1].output, TranscriptionOutput)
    assert isinstance(outputs[2].output, VisionOutput)
    assert isinstance(outputs[3].output, AudioOutput)
    assert isinstance(outputs[4].output, EmbeddingsOutput)
    assert isinstance(outputs[5].output, GenerationOutput)
    assert outputs[1].output.words == (
        TranscriptWordOutput(text="Build", start_ms=100, end_ms=400, confidence=0.9),
    )
    assert outputs[4].output.vectors == ((0.1, 0.2),)
    assert outputs[5].output.artifacts == (
        GeneratedArtifact(url="https://cdn.test/image.png"),
    )
    assert all(
        call["headers"]["authorization"] == "Bearer secret-test-key"
        for call in calls
    )
    expected_models = {
        item_route.model for _item, item_route in requests_and_routes
    }
    sent_json_models = {
        call["json"]["model"] for call in calls if call.get("json")
    }
    assert sent_json_models.issubset(expected_models)
    assert not any("revision-2026-07-14" in model for model in sent_json_models)
    transcription_call = next(call for call in calls if call["url"].endswith("transcriptions"))
    assert transcription_call["data"]["model"] == "transcription-model"
    assert {call["url"].rsplit("/", 1)[-1] for call in calls} >= {
        "completions",
        "transcriptions",
        "embeddings",
        "generations",
    }


def test_model_adapters_reject_plaintext_endpoints_by_default() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleAdapter(
            base_url="http://models.internal/v1",
            api_key="must-not-cross-plaintext",
        )


def test_azure_adapter_uses_deployment_url_api_version_and_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_request(client, method, url, **kwargs):  # noqa: ANN001
        del client, method
        captured.update({"url": url, **kwargs})
        return _response({"choices": [{"message": {"content": "ok"}}]}, url)

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    adapter = AzureOpenAIAdapter(
        base_url="https://motionly-ai.openai.azure.com",
        api_key="azure-secret",
        api_version="2026-01-01-preview",
    )
    model_route = ModelRoute(
        route_id="azure-planning",
        capability=ModelCapability.PLANNING,
        adapter="azure",
        provider="azure",
        model="planner deployment",
        model_revision="planner-deployment-v7",
    )

    result = adapter.invoke(route=model_route, request=request())

    assert isinstance(result.output, PlanningOutput)
    assert "/openai/deployments/planner%20deployment/chat/completions" in captured["url"]
    assert captured["json"]["model"] == "planner deployment"
    assert "planner-deployment-v7" not in captured["url"]
    assert captured["url"].endswith("api-version=2026-01-01-preview")
    assert captured["headers"]["api-key"] == "azure-secret"
    assert "authorization" not in captured["headers"]


def test_factory_builds_azure_and_custom_openai_routes_with_explicit_revisions() -> None:
    configured_routes = json.dumps(
        [
            {
                "route_id": "azure-planning",
                "capability": "planning",
                "adapter": "azure",
                "provider": "azure",
                "model": "planner-deployment",
                "model_revision": "planner-v7",
                "priority": 1,
            },
            {
                "route_id": "custom-planning",
                "capability": "planning",
                "adapter": "openai_compatible",
                "provider": "private-gpu",
                "model": "planner-70b",
                "model_revision": "sha256:" + "a" * 64,
                "priority": 2,
            },
        ]
    )
    settings = SimpleNamespace(
        model_gateway_enabled=True,
        model_gateway_routes=configured_routes,
        model_gateway_openai_base_url="https://gpu.motionly.test/v1",
        model_gateway_openai_api_key="custom-key",
        azure_openai_endpoint="https://motionly-ai.openai.azure.com",
        azure_openai_api_key="azure-key",
        azure_openai_api_version="2026-01-01-preview",
        openrouter_api_key=None,
        groq_api_key=None,
        redis_url=None,
        model_gateway_failure_threshold=3,
        model_gateway_circuit_cooldown_seconds=60,
        model_gateway_monthly_budget_usd=100,
        model_gateway_request_budget_usd=5,
    )

    gateway = model_gateway_from_settings(settings)

    assert gateway is not None
    assert [item.route_id for item in gateway.routes] == [
        "azure-planning",
        "custom-planning",
    ]
    assert [item.model_revision for item in gateway.routes] == [
        "planner-v7",
        "sha256:" + "a" * 64,
    ]


def test_transcription_bridge_persists_gateway_revision_in_raw_payload(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"RIFF-audio")
    adapter_result = AdapterResult(
        output=TranscriptionOutput(
            text="Build",
            language="english",
            confidence=0.95,
            words=(TranscriptWordOutput("Build", 100, 400, 0.9),),
            raw_response={"text": "Build"},
        ),
        usage=ModelUsage(input_units=10, output_units=1),
    )
    adapter = FakeAdapter({"transcription": adapter_result})
    operations = OperationsRepository()
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[route("transcription", capability=ModelCapability.TRANSCRIPTION)],
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=None,
            operations_repository=operations,
        ),
    )
    provider = GatewayTranscriptionProvider(
        gateway=gateway,
        provider_name="custom",
        model="transcriber",
        model_revision="transcriber-v1",
    )

    workspace_id = uuid4()
    project_id = uuid4()
    result = provider.transcribe_with_context(
        audio_path=audio_path,
        language_hint=LanguageHint.AUTO,
        workspace_id=workspace_id,
        project_id=project_id,
    )

    assert result.language == "en"
    assert result.provider == "transcription"
    assert result.raw_payload["model_gateway"]["model_revision"] == (
        "transcription-revision-2026-07-14"
    )
    assert operations.provider_events[0]["workspace_id"] == workspace_id
    assert operations.provider_events[0]["project_id"] == project_id


def test_caption_translation_uses_gateway_with_tenant_context() -> None:
    translated_payload = '{"segments":["Construire mieux"]}'
    operations = OperationsRepository()
    adapter = FakeAdapter({"translation": planning_result(translated_payload)})
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[route("translation", capability=ModelCapability.PLANNING)],
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=None,
            operations_repository=operations,
        ),
    )
    workspace_id = uuid4()
    project_id = uuid4()
    segments = [CaptionSegment(index=0, start_ms=0, end_ms=1000, text="Build better")]

    translated = translate_segments_if_requested(
        segments=segments,
        source_language="en",
        output_language="fr",
        model_gateway=gateway,
        workspace_id=workspace_id,
        project_id=project_id,
    )

    assert translated[0].text == "Construire mieux"
    assert adapter.calls == ["translation"]
    assert operations.provider_events[0]["workspace_id"] == workspace_id
    assert operations.provider_events[0]["project_id"] == project_id


def test_planner_bridge_preserves_validation_and_returns_gateway_provenance() -> None:
    fixture_path = Path(__file__).parent / "fixtures/providers/openrouter_plan.json"
    payload = json.loads(fixture_path.read_text())
    content = payload["choices"][0]["message"]["content"]
    adapter = FakeAdapter(
        {
            "planning": AdapterResult(
                output=PlanningOutput(content=content, raw_response=payload),
                usage=ModelUsage(input_units=120, output_units=80),
            )
        }
    )
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[route("planning", capability=ModelCapability.PLANNING)],
    )
    provider = GatewayEditPlannerProvider(
        endpoint="https://unused.test/v1/chat/completions",
        api_key="gateway-managed",
        model="legacy-cache-model",
        provider_name="openai_compatible",
        gateway=gateway,
    )
    transcript = SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        updated_at=None,
        duration_ms=2000,
        raw_payload={},
        words=[
            TranscriptWord(index=0, text="Build", start_ms=0, end_ms=500, confidence=0.96),
            TranscriptWord(index=1, text="better", start_ms=550, end_ms=1100, confidence=0.95),
        ],
    )

    result = provider.create_plan(
        transcript=transcript,
        payload=PlanCreateRequest(
            style="founder",
            target_duration_s=10,
            broll_density=BrollDensity.NONE,
        ),
        video_duration_ms=2000,
        billing_repository=object(),
        operations_repository=object(),
    )

    assert result.provider == "planning"
    assert result.model == "planning-model"
    assert result.raw_payload["model_gateway"]["model_revision"] == (
        "planning-revision-2026-07-14"
    )
    assert result.plan.cuts[0].end_ms == 1100


def test_visual_manifest_bundle_routes_through_gateway_with_tenant_telemetry(
    tmp_path: Path,
) -> None:
    from app.schemas.uploads import VideoMetadata
    from app.services.model_domain_adapters import (
        GatewayVisualIntelligenceProvider,
        register_visual_route,
    )
    from app.services.visual_intelligence import FixtureVisualIntelligenceProvider

    operations = OperationsRepository()
    gateway = ModelGateway(
        adapters={},
        routes=[],
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=None,
            operations_repository=operations,
        ),
    )
    native = FixtureVisualIntelligenceProvider()
    visual_route = register_visual_route(
        gateway,
        provider=native,
        model=native.model,
        revision="fixture-visual-2026-07-14",
    )
    provider = GatewayVisualIntelligenceProvider(gateway=gateway, route=visual_route)
    source = tmp_path / "video.mp4"
    source.write_bytes(b"fixture-video")
    workspace_id = uuid4()
    project_id = uuid4()

    manifest = provider.analyze_with_context(
        source_path=source,
        metadata=VideoMetadata(duration_ms=2_000, width=1080, height=1920),
        sample_rate_fps=2.0,
        scene_threshold=0.42,
        workspace_id=workspace_id,
        project_id=project_id,
    )

    assert manifest.frame_count > 0
    assert operations.provider_events[0]["operation"] == "vision.video_manifest"
    assert operations.provider_events[0]["workspace_id"] == workspace_id


def test_audio_intelligence_bundle_preserves_partial_degradation(tmp_path: Path) -> None:
    from app.services.model_domain_adapters import (
        GatewayAudioIntelligenceProvider,
        register_audio_route,
    )

    class BrokenDiarization:
        provider_name = "broken-diarization"
        model = "diarization-v1"

        def diarize(self, *, audio_path):
            del audio_path
            raise RuntimeError("inference failed")

    class Emotion:
        def classify_windows(self, *, audio_path):
            del audio_path
            return [{"emotion": "joy"}]

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF-audio")
    gateway = ModelGateway(adapters={}, routes=[])
    audio_route = register_audio_route(
        gateway,
        providers={"speaker_diarization": BrokenDiarization(), "emotion": Emotion()},
        revision="audio-bundle-2026-07-14",
    )
    provider = GatewayAudioIntelligenceProvider(gateway=gateway, route=audio_route)

    insights = provider.analyze_with_context(
        audio_path=audio,
        workspace_id=uuid4(),
        project_id=uuid4(),
    )

    assert insights["speaker_diarization"]["degraded"] is True
    assert insights["emotion_windows"] == [{"emotion": "joy"}]


def test_clip_failure_uses_explicit_hash_route_and_reports_revision() -> None:
    from app.services.model_domain_adapters import (
        GatewayClipEmbeddingProvider,
        register_clip_routes,
    )
    from app.services.stock_video import HashClipEmbeddingProvider

    class BrokenClip:
        model_name = "clip-primary"

        def embed_text(self, text):
            del text
            raise RuntimeError("weights unavailable")

        def embed_image(self, *, image_bytes, source_hint=None):
            del image_bytes, source_hint
            raise RuntimeError("weights unavailable")

    gateway = ModelGateway(adapters={}, routes=[])
    register_clip_routes(
        gateway,
        primary_provider=BrokenClip(),
        primary_revision="clip-primary-2026-07-14",
        fallback_provider=HashClipEmbeddingProvider(),
    )
    provider = GatewayClipEmbeddingProvider(gateway=gateway)

    embedding = provider.embed_text(
        "founder office",
        workspace_id=uuid4(),
        project_id=uuid4(),
    )

    assert len(embedding) == 32
    assert provider.model_version == "compatible-hash-clip-v1"


def test_runway_local_rejection_is_terminal_nonbillable_and_not_circuit_worthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.model_domain_adapters import register_runway_route
    from app.services.model_gateway import VideoGenerationSubmitRequest
    from app.services.scale import RunwayGen3Provider

    def reject_network(*_args, **_kwargs):
        raise AssertionError("local rejection must not make a network call")

    monkeypatch.setattr("app.services.scale.httpx.post", reject_network)
    ledger = InMemoryBudgetLedger()
    circuits = InMemoryCircuitStore(failure_threshold=1, cooldown_seconds=60)
    operations = OperationsRepository()
    gateway = ModelGateway(
        adapters={},
        routes=[],
        budget_ledger=ledger,
        circuit_store=circuits,
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=None,
            operations_repository=operations,
        ),
        monthly_budget_usd=10.0,
    )
    route = register_runway_route(
        gateway,
        provider=RunwayGen3Provider(api_key="unused"),
        model="gen3a_turbo",
        revision="gen3a_turbo-2024-11-06",
        fixed_cost_usd=1.0,
    )
    context = ModelCallContext(workspace_id="workspace-one", project_id=uuid4())
    request = VideoGenerationSubmitRequest(
        prompt="Founder enters office",
        prompt_image_url=None,
        duration_s=5,
        ratio="768:1280",
        idempotency_key="resource-123",
    )

    for _ in range(2):
        with pytest.raises(ModelProvidersExhausted) as exc_info:
            gateway.invoke(request, context=context)
        assert exc_info.value.failures == (
            {"route_id": route.route_id, "code": "provider_request_rejected"},
        )

    assert ledger.usage(budget_scope("workspace-one")) == 0.0
    circuits.check(route.circuit_key)
    assert [event["actual_cost_usd"] for event in operations.provider_events] == [0.0, 0.0]
    assert all(
        event["error_code"] == "provider_request_rejected"
        for event in operations.provider_events
    )


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "provider_auth_failed"),
        (402, "provider_quota_exceeded"),
        (429, "provider_rate_limited"),
        (503, "provider_unavailable"),
    ],
)
def test_runway_remote_http_failures_keep_distinct_classifications(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_code: str,
) -> None:
    from app.services.scale import RunwayGen3Provider

    response = httpx.Response(
        status_code,
        json={"error": "recorded"},
        request=httpx.Request("POST", "https://api.dev.runwayml.com/v1/image_to_video"),
    )
    monkeypatch.setattr("app.services.scale.httpx.post", lambda *_a, **_k: response)

    with pytest.raises(ProviderCallFailure) as exc_info:
        RunwayGen3Provider(api_key="unused").initiate_generation(
            prompt="Founder enters office",
            prompt_image_url="https://cdn.test/frame.jpg",
            duration_s=5,
            ratio="768:1280",
        )

    assert exc_info.value.error_info.code == expected_code
    assert exc_info.value.error_info.status_code == status_code


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (
            httpx.ReadTimeout(
                "recorded timeout",
                request=httpx.Request("POST", "https://api.dev.runwayml.com"),
            ),
            "provider_timeout",
        ),
        (
            httpx.ConnectError(
                "recorded network failure",
                request=httpx.Request("POST", "https://api.dev.runwayml.com"),
            ),
            "provider_network_error",
        ),
    ],
)
def test_runway_remote_transport_failures_keep_distinct_classifications(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_code: str,
) -> None:
    from app.services.scale import RunwayGen3Provider

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr("app.services.scale.httpx.post", fail)
    with pytest.raises(ProviderCallFailure) as exc_info:
        RunwayGen3Provider(api_key="unused").initiate_generation(
            prompt="Founder enters office",
            prompt_image_url="https://cdn.test/frame.jpg",
            duration_s=5,
            ratio="768:1280",
        )

    assert exc_info.value.error_info.code == expected_code


def test_runway_submit_and_poll_are_route_affine_and_poll_is_not_billed() -> None:
    from app.services.model_domain_adapters import register_runway_route
    from app.services.model_gateway import (
        GenerationOperationOutput,
        GenerationOutput,
        VideoGenerationPollRequest,
        VideoGenerationSubmitRequest,
    )

    class RunwayTransport:
        model = "gen3a_turbo"
        api_version = "2024-11-06"

        def __init__(self):
            self.submits = 0
            self.polls = 0
            self.idempotency_keys = []

        def initiate_generation(self, *, idempotency_key, **_kwargs):
            self.submits += 1
            self.idempotency_keys.append(idempotency_key)
            return "task-123"

        def poll_task(self, task_id):
            self.polls += 1
            assert task_id == "task-123"
            return "https://cdn.test/task-123.mp4"

    transport = RunwayTransport()
    gateway = ModelGateway(adapters={}, routes=[], monthly_budget_usd=10)
    runway_route = register_runway_route(
        gateway,
        provider=transport,
        model=transport.model,
        revision="gen3a_turbo-2024-11-06",
        fixed_cost_usd=0.5,
    )
    context = ModelCallContext(
        workspace_id=uuid4(),
        project_id=uuid4(),
        required_route_id=runway_route.route_id,
    )

    submitted = gateway.invoke(
        VideoGenerationSubmitRequest(
            prompt="Founder enters office",
            prompt_image_url="https://cdn.test/frame.jpg",
            duration_s=5,
            ratio="768:1280",
            idempotency_key="resource-123",
        ),
        context=context,
    )
    assert isinstance(submitted.output, GenerationOperationOutput)
    polled = gateway.invoke(
        VideoGenerationPollRequest(operation_id=submitted.output.operation_id),
        context=context,
    )

    assert isinstance(polled.output, GenerationOutput)
    assert submitted.metadata.actual_cost_usd == 0.5
    assert polled.metadata.actual_cost_usd == 0
    assert polled.metadata.route_id == submitted.metadata.route_id
    assert transport.submits == 1
    assert transport.polls == 1
    assert transport.idempotency_keys == ["resource-123"]

    with pytest.raises(NoModelRoute):
        gateway.invoke(
            VideoGenerationPollRequest(operation_id="task-123"),
            context=ModelCallContext(
                required_route_id=runway_route.route_id,
                required_model_revision="gen3a_turbo-2025-01-01",
            ),
        )


@pytest.mark.parametrize(
    "error_info",
    [
        ProviderErrorInfo(
            "provider_auth_failed",
            False,
            401,
            fallback_eligible=True,
            circuit_worthy=True,
        ),
        ProviderErrorInfo(
            "provider_quota_exceeded",
            False,
            402,
            fallback_eligible=True,
            circuit_worthy=True,
        ),
        ProviderErrorInfo(
            "provider_malformed_response",
            False,
            fallback_eligible=True,
            circuit_worthy=True,
        ),
    ],
)
def test_route_local_nonretryable_failures_fall_back_and_open_circuit(
    error_info: ProviderErrorInfo,
) -> None:
    adapter = FakeAdapter(
        {
            "primary": ProviderCallFailure(provider="primary", error_info=error_info),
            "secondary": planning_result("secondary"),
        }
    )
    circuits = InMemoryCircuitStore(failure_threshold=1, cooldown_seconds=60)
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[route("primary", priority=1), route("secondary", priority=2)],
        circuit_store=circuits,
    )

    assert gateway.invoke(request()).metadata.route_id == "secondary"
    assert gateway.invoke(request()).metadata.route_id == "secondary"
    assert adapter.calls == ["primary", "secondary", "secondary"]


def test_request_rejection_is_terminal_across_routes() -> None:
    adapter = FakeAdapter(
        {
            "primary": ProviderCallFailure(
                provider="primary",
                error_info=ProviderErrorInfo(
                    "provider_request_rejected",
                    False,
                    400,
                    fallback_eligible=False,
                    circuit_worthy=False,
                ),
            ),
            "secondary": planning_result("must-not-run"),
        }
    )
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[route("primary", priority=1), route("secondary", priority=2)],
    )

    with pytest.raises(ModelProvidersExhausted) as exc_info:
        gateway.invoke(request())

    assert adapter.calls == ["primary"]
    assert exc_info.value.failures == (
        {"route_id": "primary", "code": "provider_request_rejected"},
    )


def test_duplicate_enabled_route_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate route_id"):
        ModelGateway(
            adapters={"fake": FakeAdapter({})},
            routes=[route("duplicate"), route("duplicate", priority=2)],
        )


def test_telemetry_sink_failures_are_isolated() -> None:
    class BrokenMetrics:
        def observe(self, *_args, **_kwargs):
            raise RuntimeError("metrics unavailable")

    operations = OperationsRepository()
    telemetry = OperationsGatewayTelemetry(
        metrics_registry=BrokenMetrics(),
        operations_repository=operations,
    )
    gateway = ModelGateway(
        adapters={"fake": FakeAdapter({"primary": planning_result()})},
        routes=[route("primary")],
        telemetry=telemetry,
    )

    result = gateway.invoke(request())

    assert result.metadata.route_id == "primary"
    assert len(operations.provider_events) == 1
    assert operations.provider_events[0]["model_revision"] == result.metadata.model_revision

    class BrokenOperations:
        def record_provider_slo(self, **_values):
            raise RuntimeError("database unavailable")

    metrics = MetricsRegistry()
    gateway = ModelGateway(
        adapters={"fake": FakeAdapter({"primary": planning_result()})},
        routes=[route("primary")],
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=metrics,
            operations_repository=BrokenOperations(),
        ),
    )
    gateway.invoke(request())
    rendered = metrics.render()
    assert "motionly_model_gateway_latency_ms_count" in rendered
    assert "motionly_model_gateway_cost_usd_count" in rendered


def test_factory_expands_http_operations_and_rejects_domain_operation_claims() -> None:
    base_route = {
        "route_id": "custom-vision",
        "capability": "vision",
        "adapter": "openai_compatible",
        "provider": "private-gpu",
        "model": "vision-model",
        "model_revision": "vision-model-2026-07-14",
        "fixed_cost_usd": 0.1,
    }
    settings = SimpleNamespace(
        model_gateway_enabled=True,
        model_gateway_routes=json.dumps([base_route]),
        model_gateway_openai_base_url="https://gpu.motionly.test/v1",
        model_gateway_openai_api_key="key",
        azure_openai_endpoint=None,
        openrouter_api_key=None,
        groq_api_key=None,
        redis_url=None,
        model_gateway_failure_threshold=3,
        model_gateway_circuit_cooldown_seconds=60,
        model_gateway_monthly_budget_usd=100,
        model_gateway_request_budget_usd=5,
    )

    gateway = model_gateway_from_settings(settings)

    assert gateway is not None
    assert gateway.routes[0].operations == (ModelOperation.VISION_PROMPT,)
    incompatible = {**base_route, "operations": ["vision.video_manifest"]}
    settings.model_gateway_routes = json.dumps([incompatible])
    with pytest.raises(ValueError, match="supports only 'vision.prompt'"):
        model_gateway_from_settings(settings)

    malformed_boolean = {**base_route, "enabled": "false"}
    settings.model_gateway_routes = json.dumps([malformed_boolean])
    with pytest.raises(ValueError, match="enabled must be a JSON boolean"):
        model_gateway_from_settings(settings)


@pytest.mark.parametrize(
    ("gateway_request", "payload"),
    [
        (
            EmbeddingsRequest(inputs=("one", "two")),
            {"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
        ),
        (
            EmbeddingsRequest(inputs=("one",), dimensions=2),
            {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]},
        ),
        (
            GenerationRequest(prompt="two images", count=2),
            {"data": [{"url": "https://cdn.test/only-one.png"}]},
        ),
    ],
)
def test_adapter_rejects_mismatched_typed_output_shapes(
    monkeypatch: pytest.MonkeyPatch,
    gateway_request: object,
    payload: dict[str, object],
) -> None:
    def fake_request(client, method, url, **kwargs):  # noqa: ANN001
        del client, method, kwargs
        return _response(payload, url)

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    capability = capability_for_request(gateway_request)
    adapter = OpenAICompatibleAdapter(base_url="https://models.test/v1", api_key="key")

    with pytest.raises(ProviderCallFailure) as exc_info:
        adapter.invoke(route=route("shape", capability=capability), request=gateway_request)

    assert exc_info.value.error_info.code == "provider_malformed_response"
    assert exc_info.value.error_info.can_fallback is True


def test_adapter_rejects_invalid_transcription_timestamps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_request(client, method, url, **kwargs):  # noqa: ANN001
        del client, method, kwargs
        return _response(
            {
                "text": "bad timing",
                "words": [{"word": "bad", "start": 1.0, "end": 0.5}],
            },
            url,
        )

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    adapter = OpenAICompatibleAdapter(base_url="https://models.test/v1", api_key="key")

    with pytest.raises(ProviderCallFailure, match="provider_malformed_response"):
        adapter.invoke(
            route=route("transcription-shape", capability=ModelCapability.TRANSCRIPTION),
            request=TranscriptionRequest(audio_path=audio),
        )


def test_audio_domain_wrapper_allows_central_route_fallback(tmp_path: Path) -> None:
    from app.services.model_domain_adapters import GatewayAudioIntelligenceProvider
    from app.services.model_gateway import AudioIntelligenceOutput

    primary = ModelRoute(
        route_id="audio-primary",
        capability=ModelCapability.AUDIO,
        adapter="fake",
        provider="local-primary",
        model="audio-primary",
        model_revision="audio-primary-2026-07-14",
        operations=(ModelOperation.AUDIO_INTELLIGENCE,),
        priority=1,
    )
    secondary = ModelRoute(
        route_id="audio-secondary",
        capability=ModelCapability.AUDIO,
        adapter="fake",
        provider="local-secondary",
        model="audio-secondary",
        model_revision="audio-secondary-2026-07-14",
        operations=(ModelOperation.AUDIO_INTELLIGENCE,),
        priority=2,
    )
    adapter = FakeAdapter(
        {
            "audio-primary": ProviderCallFailure(
                provider="local-primary",
                error_info=ProviderErrorInfo("provider_unavailable", True),
            ),
            "audio-secondary": AdapterResult(
                output=AudioIntelligenceOutput(insights={"fallback": True})
            ),
        }
    )
    gateway = ModelGateway(adapters={"fake": adapter}, routes=[primary, secondary])
    provider = GatewayAudioIntelligenceProvider(gateway=gateway, route=primary)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")

    result = provider.analyze_with_context(
        audio_path=audio,
        workspace_id=uuid4(),
        project_id=uuid4(),
    )

    assert result == {"fallback": True}
    assert adapter.calls == ["audio-primary", "audio-secondary"]


def test_legacy_groq_and_openrouter_gateway_send_callable_model_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_request(client, method, url, **kwargs):  # noqa: ANN001
        del client, method
        calls.append({"url": url, **kwargs})
        if url.endswith("/audio/transcriptions"):
            return _response({"text": "Build", "words": []}, url)
        return _response(
            {"choices": [{"message": {"content": "{}"}}], "usage": {}},
            url,
        )

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    settings = SimpleNamespace(
        model_gateway_enabled=True,
        model_gateway_routes="",
        model_gateway_openai_base_url=None,
        azure_openai_endpoint=None,
        openrouter_api_key="openrouter-key",
        groq_api_key="groq-key",
        edit_planner_provider="openrouter",
        edit_planner_model="openai/gpt-4.1-mini",
        edit_planner_model_revision="openai/gpt-4.1-mini-2025-04-14",
        edit_planner_input_cost_per_million_usd=2,
        edit_planner_output_cost_per_million_usd=2,
        transcription_provider="groq",
        transcription_model="whisper-large-v3-turbo",
        transcription_model_revision="whisper-large-v3-turbo-2025-02-01",
        transcription_estimated_cost_per_request_usd=0.25,
        redis_url=None,
        model_gateway_failure_threshold=3,
        model_gateway_circuit_cooldown_seconds=60,
        model_gateway_monthly_budget_usd=100,
        model_gateway_request_budget_usd=5,
    )
    gateway = model_gateway_from_settings(settings)
    assert gateway is not None
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")

    planning = gateway.invoke(request())
    transcription = gateway.invoke(TranscriptionRequest(audio_path=audio))

    assert planning.metadata.model_revision == "openai/gpt-4.1-mini-2025-04-14"
    assert transcription.metadata.model_revision == "whisper-large-v3-turbo-2025-02-01"
    planning_call = next(call for call in calls if str(call["url"]).endswith("completions"))
    transcription_call = next(
        call for call in calls if str(call["url"]).endswith("transcriptions")
    )
    assert planning_call["json"]["model"] == "openai/gpt-4.1-mini"
    assert transcription_call["data"]["model"] == "whisper-large-v3-turbo"


def test_typed_requests_and_outputs_reject_invalid_shapes(tmp_path: Path) -> None:
    from app.services.model_gateway import (
        EmbeddingsOutput,
        GenerationOperationOutput,
        ImageEmbeddingsRequest,
        VideoGenerationPollRequest,
        VideoGenerationSubmitRequest,
    )

    with pytest.raises(ValueError, match="prompt"):
        AudioRequest(prompt="", audio_path=tmp_path / "audio.wav")
    with pytest.raises(ValueError, match="cannot be empty"):
        ImageEmbeddingsRequest(images=(b"",))
    with pytest.raises(ValueError, match="positive"):
        VideoGenerationSubmitRequest(
            prompt="video",
            prompt_image_url=None,
            duration_s=0,
            ratio="768:1280",
            idempotency_key="key",
        )
    with pytest.raises(ValueError, match="cannot be empty"):
        VideoGenerationPollRequest(operation_id="")
    with pytest.raises(ValueError, match="timestamps"):
        TranscriptWordOutput(text="word", start_ms=10, end_ms=5)
    with pytest.raises(ValueError, match="dimensionally aligned"):
        EmbeddingsOutput(vectors=((0.1,), (0.1, 0.2)), raw_response={})
    with pytest.raises(ValueError, match="cannot be empty"):
        GenerationOperationOutput(operation_id="")


def test_video_generation_submit_never_falls_back_after_attempt() -> None:
    from app.services.model_gateway import VideoGenerationSubmitRequest

    primary = ModelRoute(
        route_id="generation-primary",
        capability=ModelCapability.GENERATION,
        adapter="fake",
        provider="runway-primary",
        model="gen3a_turbo",
        model_revision="gen3a_turbo-2024-11-06",
        operations=(ModelOperation.GENERATION_VIDEO_SUBMIT,),
        priority=1,
        fixed_cost_usd=0.5,
    )
    secondary = ModelRoute(
        route_id="generation-secondary",
        capability=ModelCapability.GENERATION,
        adapter="fake",
        provider="runway-secondary",
        model="gen4_turbo",
        model_revision="gen4_turbo-2026-01-01",
        operations=(ModelOperation.GENERATION_VIDEO_SUBMIT,),
        priority=2,
        fixed_cost_usd=0.5,
    )
    adapter = FakeAdapter(
        {
            "generation-primary": ProviderCallFailure(
                provider="runway-primary",
                error_info=ProviderErrorInfo("provider_timeout", True),
            ),
            "generation-secondary": RuntimeError("must not run"),
        }
    )
    gateway = ModelGateway(adapters={"fake": adapter}, routes=[primary, secondary])

    with pytest.raises(ModelProvidersExhausted):
        gateway.invoke(
            VideoGenerationSubmitRequest(
                prompt="Founder enters office",
                prompt_image_url=None,
                duration_s=5,
                ratio="768:1280",
                idempotency_key="resource-123",
            )
        )

    assert adapter.calls == ["generation-primary"]
