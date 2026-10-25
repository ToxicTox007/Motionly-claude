"""Keyframe captions on a remote vision route (the Tier-2 vision.caption route)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.schemas.uploads import VideoMetadata
from app.schemas.visual import SceneType
from app.services.model_adapters import OpenAICompatibleAdapter
from app.services.model_domain_adapters import (
    GatewayVisualIntelligenceProvider,
    register_visual_route,
)
from app.services.model_gateway import (
    InMemoryBudgetLedger,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelRoute,
    budget_scope,
    feature_budget_scope,
)
from app.services.remote_visual import (
    EnrichmentUnavailable,
    GatewayCaptionsEnricher,
    Keyframe,
    RemoteVisualIntelligenceProvider,
    _parse_captions,
    remote_visual_provider_from_settings,
)

CHAT_URL = "https://remote-captions.example/api/v1/chat/completions"
MODEL = "google/gemini-3.5-flash-lite"
REVISION = "google/gemini-3.5-flash-lite-2026-07-21"

# Task 11 rewires vision.caption to a capability route; Task 19 removed the
# vendor registration helpers. These tests register a vision.caption route
# directly so the kept GatewayCaptionsEnricher stays covered.
CAPTIONS_ROUTE_ID = "remote-vision-captions"
CAPTIONS_ADAPTER = "remote-vision-captions-adapter"


def _completion(content: object, *, prompt_tokens: int = 900, completion_tokens: int = 80):
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def _captions_for(count: int, prefix: str = "a laptop on a desk") -> dict:
    return {"captions": [{"index": i, "caption": f"{prefix} {i}"} for i in range(count)]}


def _install(monkeypatch: pytest.MonkeyPatch, responder) -> list[dict]:
    calls: list[dict] = []

    def fake_request(client, method, url, **kwargs):  # noqa: ANN001
        del client
        calls.append({"method": method, "url": url, **kwargs})
        status, body = responder(kwargs["json"])
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    return calls


def _image_count(body: dict) -> int:
    return sum(1 for part in body["messages"][0]["content"] if part["type"] == "image_url")


def _register_captions_route(gateway: ModelGateway) -> ModelRoute:
    gateway.register_adapter(
        CAPTIONS_ADAPTER,
        OpenAICompatibleAdapter(
            base_url="https://remote-captions.example/api/v1",
            api_key="remote-captions-key",
            extra_body={"provider": {"zdr": True, "data_collection": "deny"}},
        ),
    )
    route = ModelRoute(
        route_id=CAPTIONS_ROUTE_ID,
        capability=ModelCapability.VISION,
        adapter=CAPTIONS_ADAPTER,
        provider="remote",
        model=MODEL,
        model_revision=REVISION,
        operations=(ModelOperation.VISION_PROMPT,),
        priority=10,
        input_cost_per_million_usd=0.3,
        output_cost_per_million_usd=2.5,
        max_output_units=1024,
        endpoint_host="remote-captions.example",
        requires_live_effects_gate=True,
        budget_feature="vision_captions",
    )
    gateway.register_route(route)
    return route


def _gateway(**kwargs) -> ModelGateway:
    gateway = ModelGateway(adapters={}, routes=[], **kwargs)
    _register_captions_route(gateway)
    return gateway


def _keyframe(index: int) -> Keyframe:
    return Keyframe(
        scene_index=index,
        sample_index=index,
        timestamp_ms=index * 1000,
        width=160,
        height=120,
        jpeg=b"\xff\xd8\xff\xe0frame-" + str(index).encode(),
    )


def _enricher(gateway: ModelGateway, **overrides) -> GatewayCaptionsEnricher:
    return GatewayCaptionsEnricher(
        gateway=gateway,
        route_id=CAPTIONS_ROUTE_ID,
        model_version=f"remote-captions:{REVISION}",
        **overrides,
    )


def test_requests_use_zdr_routing_structured_output_and_data_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch, lambda body: (200, _completion(_captions_for(2))))

    captions = _enricher(_gateway()).enrich([_keyframe(0), _keyframe(1)])

    assert captions == ["a laptop on a desk 0", "a laptop on a desk 1"]
    (call,) = calls
    body = call["json"]
    assert call["url"] == CHAT_URL
    assert call["headers"]["authorization"] == "Bearer remote-captions-key"
    assert body["provider"] == {"zdr": True, "data_collection": "deny"}
    assert body["model"] == MODEL
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["max_tokens"] == 64 + 96 * 2
    text, first, second = body["messages"][0]["content"]
    assert "There are 2 images" in text["text"]
    assert "Do not name or identify people" in text["text"]
    prefix = "data:image/jpeg;base64,"
    assert first["image_url"]["url"].startswith(prefix)
    assert base64.b64decode(first["image_url"]["url"][len(prefix) :]) == _keyframe(0).jpeg
    assert second["image_url"]["url"] != first["image_url"]["url"]


def test_keyframes_are_batched(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(
        monkeypatch, lambda body: (200, _completion(_captions_for(_image_count(body))))
    )

    captions = _enricher(_gateway(), max_images_per_request=8).enrich(
        [_keyframe(i) for i in range(19)]
    )

    assert [_image_count(call["json"]) for call in calls] == [8, 8, 3]
    assert len(captions) == 19


def test_out_of_order_skipped_and_duplicate_items_are_handled() -> None:
    reply = json.dumps(
        {
            "captions": [
                {"index": 2, "caption": "  a  city\nstreet "},
                {"index": 0, "caption": "first"},
                {"index": 0, "caption": "ignored duplicate"},
                {"index": 7, "caption": "out of range"},
                {"index": True, "caption": "bool index"},
                {"index": 1, "caption": "   "},
            ]
        }
    )

    assert _parse_captions(reply, count=3) == ["first", "", "a city street"]
    assert _parse_captions("```json\n" + reply + "\n```", count=3)[0] == "first"


@pytest.mark.parametrize(
    "content",
    ["not json", json.dumps({"items": []}), json.dumps({"captions": []}), json.dumps([])],
)
def test_unusable_replies_raise_enrichment_unavailable(content: str) -> None:
    with pytest.raises(EnrichmentUnavailable) as raised:
        _parse_captions(content, count=2)

    assert raised.value.code == "provider_malformed_response"


def test_route_is_gated_priced_and_charged_to_vision_captions() -> None:
    gateway = _gateway()
    routes = {route.route_id: route for route in gateway.routes}
    captions = routes[CAPTIONS_ROUTE_ID]

    assert captions.operations == (ModelOperation.VISION_PROMPT,)
    assert captions.adapter == CAPTIONS_ADAPTER
    assert captions.endpoint_host == "remote-captions.example"
    assert captions.requires_live_effects_gate is True
    assert captions.budget_feature == "vision_captions"


def test_cost_is_settled_from_reported_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        lambda body: (200, _completion(_captions_for(1), prompt_tokens=1200, completion_tokens=40)),
    )
    ledger = InMemoryBudgetLedger()
    gateway = _gateway(budget_ledger=ledger, feature_budgets_usd={"vision_captions": 5.0})

    _enricher(gateway).enrich([_keyframe(0)])

    expected = 1200 * 0.3 / 1_000_000 + 40 * 2.5 / 1_000_000
    assert ledger.usage(budget_scope(None)) == pytest.approx(expected)
    assert ledger.usage(feature_budget_scope("vision_captions")) == pytest.approx(expected)


def _video(tmp_path: Path) -> Path:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    path = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for color in ((200, 40, 40), (40, 40, 200)):
        for i in range(15):
            frame = np.full((120, 160, 3), color, dtype=np.uint8)
            cv2.rectangle(frame, (10 + i * 4, 40), (30 + i * 4, 80), (255, 255, 255), -1)
            writer.write(frame)
    writer.release()
    return path


class _NoFaces:
    def detect(self, rgb):
        return []


def _provider(gateway: ModelGateway) -> RemoteVisualIntelligenceProvider:
    return RemoteVisualIntelligenceProvider(
        face_model_path="/models/face.tflite",
        face_model_sha256="a" * 64,
        enrichers=(_enricher(gateway),),
        face_detector_factory=_NoFaces,
    )


def test_captions_fill_scenes_and_drive_scene_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        lambda body: (
            200,
            _completion(_captions_for(_image_count(body), "a laptop screen showing code")),
        ),
    )
    ledger = InMemoryBudgetLedger()
    gateway = _gateway(budget_ledger=ledger)
    provider = _provider(gateway)
    route = register_visual_route(
        gateway, provider=provider, model=provider.model, revision="sha256:" + "c" * 64
    )

    manifest = GatewayVisualIntelligenceProvider(gateway=gateway, route=route).analyze_with_context(
        source_path=_video(tmp_path),
        metadata=VideoMetadata(duration_ms=3000, width=160, height=120, has_audio=False),
        sample_rate_fps=2.0,
        scene_threshold=0.42,
        workspace_id="workspace-a",
        project_id="project-a",
    )

    assert "captions" not in manifest.summary["degraded_capabilities"]
    assert manifest.model_versions["captions"] == f"remote-captions:{REVISION}"
    assert all(sample.caption.startswith("a laptop screen") for sample in manifest.frame_samples)
    # "screen" in the caption classifies the scene as a screen recording.
    assert {scene.scene_type for scene in manifest.scenes} == {SceneType.SCREEN_RECORDING}
    assert ledger.usage(budget_scope("workspace-a")) > 0
    assert ledger.usage(budget_scope(None)) == 0.0


@pytest.mark.parametrize(
    ("response", "status"),
    [
        ((503, {"error": "busy"}), "unavailable:provider_unavailable"),
        ((200, _completion("I cannot help with that.")), "unavailable:provider_malformed_response"),
    ],
)
def test_caption_failures_degrade_only_captions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, response, status: str
) -> None:
    _install(monkeypatch, lambda body: response)

    manifest = _provider(_gateway()).analyze(
        source_path=_video(tmp_path),
        metadata=VideoMetadata(duration_ms=3000, width=160, height=120, has_audio=False),
        sample_rate_fps=2.0,
        scene_threshold=0.42,
    )

    assert manifest.summary["degraded_capabilities"]["captions"] == status
    assert manifest.model_versions["captions"] == "unavailable"
    assert all(sample.caption == "" for sample in manifest.frame_samples)


def test_budget_exhaustion_degrades_captions_without_a_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install(monkeypatch, lambda body: (200, _completion(_captions_for(1))))

    manifest = _provider(_gateway(feature_budgets_usd={"vision_captions": 0.000001})).analyze(
        source_path=_video(tmp_path),
        metadata=VideoMetadata(duration_ms=3000, width=160, height=120, has_audio=False),
        sample_rate_fps=2.0,
        scene_threshold=0.42,
    )

    assert manifest.summary["degraded_capabilities"]["captions"] == (
        "unavailable:model_budget_exceeded"
    )
    assert calls == []


# -- wiring --------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    values = {
        "APP_ENV": "local",
        "VISUAL_PROVIDER": "remote",
        "VISUAL_OBJECTS_PROVIDER": "disabled",
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_captions_enricher_is_not_built_from_settings_after_task_19() -> None:
    # Task 19 removed the settings-driven captions wiring and Task 26 removed the
    # dead caption settings; captions are rebuilt as a capability route in Tier 2
    # (plan Task 11). With no route configured, the remote visual provider
    # carries no captions enricher.
    settings = _settings()
    gateway = ModelGateway(adapters={}, routes=[])

    provider = remote_visual_provider_from_settings(settings, gateway=gateway)

    assert gateway.routes == ()
    assert "captions:off" in provider.model


def test_captions_enricher_is_built_when_a_capability_route_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Task 11: the enricher becomes active purely because the gateway serves a
    # ``vision.caption`` route (no vendor switch). It then produces captions.
    _install(monkeypatch, lambda body: (200, _completion(_captions_for(_image_count(body)))))
    gateway = _gateway()  # registers one vision.caption route

    provider = remote_visual_provider_from_settings(_settings(), gateway=gateway)

    captions = provider._enrichers[  # noqa: SLF001 - verifying the wired enricher
        "captions"
    ].enrich([_keyframe(0), _keyframe(1)])
    assert captions == ["a laptop on a desk 0", "a laptop on a desk 1"]
    assert f":{CAPTIONS_ROUTE_ID}:" in provider._enrichers["captions"].model_version  # noqa: SLF001
