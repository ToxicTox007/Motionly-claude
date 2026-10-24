"""Keyframe object detection through Google Cloud Vision (VISUAL_OBJECTS_PROVIDER)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.main import _validate_production_settings
from app.schemas.visual import SceneType
from app.services.google_vision import (
    GOOGLE_VISION_MAX_IMAGES_PER_REQUEST,
    GoogleVisionObjectsAdapter,
    normalize_label,
)
from app.services.model_domain_adapters import (
    GOOGLE_VISION_OBJECTS_ROUTE_ID,
    GatewayVisualIntelligenceProvider,
    register_google_vision_objects_route,
    register_visual_route,
)
from app.services.model_gateway import (
    InMemoryBudgetLedger,
    ModelCallContext,
    ModelGateway,
    ModelOperation,
    ModelProvidersExhausted,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    budget_scope,
    feature_budget_scope,
)
from app.services.provider_resilience import ProviderCallFailure
from app.services.remote_visual import (
    GatewayObjectsEnricher,
    Keyframe,
    RemoteVisualIntelligenceProvider,
    _batches,
    remote_visual_identity,
    remote_visual_provider_from_settings,
)
from tests.test_service_hardening import _production_settings

ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
JPEG = b"\xff\xd8\xff\xe0jpeg"
REVISION = "google-vision-objects-builtin-stable-2026-10-22"


def _annotation(name: str, score: float, vertices: list[dict]) -> dict:
    return {
        "mid": "/m/x",
        "name": name,
        "score": score,
        "boundingPoly": {"normalizedVertices": vertices},
    }


LAPTOP = _annotation(
    "Laptop",
    0.91,
    [{"x": 0.1, "y": 0.2}, {"x": 0.6, "y": 0.2}, {"x": 0.6, "y": 0.7}, {"x": 0.1, "y": 0.7}],
)
# Google omits zero coordinates (proto3 defaults).
PHONE = _annotation("Mobile phone", 0.55, [{}, {"x": 0.25}, {"x": 0.25, "y": 0.5}, {"y": 0.5}])


class FakeVision:
    def __init__(self, responder=None, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.responder = responder or (
            lambda count: [{"localizedObjectAnnotations": [LAPTOP, PHONE]}] * count
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) != ENDPOINT:
            return httpx.Response(404, json={})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": self.status}})
        count = len(json.loads(request.content)["requests"])
        return httpx.Response(200, json={"responses": self.responder(count)})


def _adapter(fake: FakeVision, **overrides) -> GoogleVisionObjectsAdapter:
    return GoogleVisionObjectsAdapter(
        api_key="vision-key", transport=httpx.MockTransport(fake.handler), **overrides
    )


def _gateway(fake: FakeVision, *, cost: float = 0.0015, **kwargs) -> ModelGateway:
    gateway = ModelGateway(adapters={}, routes=[], **kwargs)
    register_google_vision_objects_route(
        gateway,
        adapter=_adapter(fake),
        model="builtin/stable",
        revision=REVISION,
        cost_per_image_usd=cost,
    )
    return gateway


def test_request_shape_and_auth_header() -> None:
    fake = FakeVision()

    _gateway(fake).invoke(ObjectDetectionRequest(images=(JPEG, JPEG), max_results=12))

    (request,) = fake.requests
    assert request.headers["x-goog-api-key"] == "vision-key"
    assert "key=" not in str(request.url)
    body = json.loads(request.content)
    assert len(body["requests"]) == 2
    first = body["requests"][0]
    assert base64.b64decode(first["image"]["content"]) == JPEG
    assert first["features"] == [
        {"type": "OBJECT_LOCALIZATION", "maxResults": 12, "model": "builtin/stable"}
    ]


def test_annotations_map_to_sorted_normalised_boxes() -> None:
    result = _gateway(FakeVision()).invoke(ObjectDetectionRequest(images=(JPEG,)))

    assert isinstance(result.output, ObjectDetectionOutput)
    (detections,) = result.output.detections
    laptop, phone = detections
    assert (laptop.label, laptop.confidence) == ("laptop", 0.91)
    assert (laptop.x, laptop.y, laptop.width, laptop.height) == (0.1, 0.2, 0.5, 0.5)
    # Google name aliased to the COCO name; omitted coordinates read as 0.
    assert phone.label == "cell phone"
    assert (phone.x, phone.y, phone.width, phone.height) == (0.0, 0.0, 0.25, 0.5)


def test_results_are_capped_and_out_of_range_values_clamped() -> None:
    wide = _annotation("Box", 1.7, [{"x": -0.2, "y": 0.1}, {"x": 1.4, "y": 1.2}])
    tiny_box = [{"x": 0, "y": 0}, {"x": 0.1, "y": 0.1}]
    many = [_annotation(f"Item {i}", i / 100, tiny_box) for i in range(40)]
    fake = FakeVision(lambda count: [{"localizedObjectAnnotations": [wide, *many]}] * count)

    (detections,) = _gateway(fake).invoke(
        ObjectDetectionRequest(images=(JPEG,), max_results=5)
    ).output.detections

    assert len(detections) == 5
    assert (detections[0].label, detections[0].confidence) == ("box", 1.0)
    assert (detections[0].x, detections[0].y, detections[0].width, detections[0].height) == (
        0.0,
        0.1,
        1.0,
        0.9,
    )


def test_empty_images_yield_no_detections() -> None:
    fake = FakeVision(lambda count: [{}] * count)

    output = _gateway(fake).invoke(ObjectDetectionRequest(images=(JPEG, JPEG))).output

    assert output.detections == ((), ())


def test_partial_image_errors_bill_only_annotated_images() -> None:
    def responder(count):
        return [{"error": {"code": 3, "message": "Bad image data."}}] + [
            {"localizedObjectAnnotations": [LAPTOP]}
        ] * (count - 1)

    ledger = InMemoryBudgetLedger()
    result = _gateway(FakeVision(responder), budget_ledger=ledger).invoke(
        ObjectDetectionRequest(images=(JPEG, JPEG, JPEG)),
        context=ModelCallContext(workspace_id="workspace-one"),
    )

    assert result.output.detections[0] == ()
    assert len(result.output.detections[1]) == 1
    assert result.metadata.actual_cost_usd == pytest.approx(2 * 0.0015)
    assert ledger.usage(budget_scope("workspace-one")) == pytest.approx(2 * 0.0015)


@pytest.mark.parametrize(
    ("code", "expected"), [(3, "provider_request_rejected"), (13, "provider_unavailable")]
)
def test_all_image_errors_fail_without_charge(code: int, expected: str) -> None:
    fake = FakeVision(lambda count: [{"error": {"code": code, "message": "x"}}] * count)
    ledger = InMemoryBudgetLedger()

    with pytest.raises(ModelProvidersExhausted) as raised:
        _gateway(fake, budget_ledger=ledger).invoke(
            ObjectDetectionRequest(images=(JPEG,)),
            context=ModelCallContext(workspace_id="workspace-one"),
        )

    assert raised.value.failures[0]["code"] == expected
    assert ledger.usage(budget_scope("workspace-one")) == 0.0


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "provider_auth_failed"), (429, "provider_rate_limited"), (503, "provider_unavailable")],
)
def test_http_errors_are_classified(status: int, code: str) -> None:
    gateway = _gateway(FakeVision(status=status))
    route = gateway.routes[0]

    with pytest.raises(ProviderCallFailure) as raised:
        _adapter(FakeVision(status=status)).invoke(
            route=route, request=ObjectDetectionRequest(images=(JPEG,))
        )

    assert raised.value.error_info.code == code


def test_response_count_mismatch_is_malformed() -> None:
    fake = FakeVision(lambda count: [{}])
    route = _gateway(fake).routes[0]

    with pytest.raises(ProviderCallFailure) as raised:
        _adapter(fake).invoke(route=route, request=ObjectDetectionRequest(images=(JPEG, JPEG)))

    assert raised.value.error_info.code == "provider_malformed_response"


def test_oversized_batches_are_rejected_before_any_call() -> None:
    fake = FakeVision()
    route = _gateway(fake).routes[0]
    images = (JPEG,) * (GOOGLE_VISION_MAX_IMAGES_PER_REQUEST + 1)

    with pytest.raises(ProviderCallFailure) as raised:
        _adapter(fake).invoke(route=route, request=ObjectDetectionRequest(images=images))

    assert raised.value.error_info.code == "provider_request_rejected"
    assert fake.requests == []


def test_route_is_gated_priced_per_image_and_charged_to_vision_objects() -> None:
    ledger = InMemoryBudgetLedger()
    gateway = _gateway(
        FakeVision(), cost=0.0015, budget_ledger=ledger, feature_budgets_usd={"vision_objects": 5.0}
    )
    route = gateway.routes[0]

    result = gateway.invoke(
        ObjectDetectionRequest(images=(JPEG,) * 4),
        context=ModelCallContext(workspace_id="workspace-one"),
    )

    assert route.route_id == GOOGLE_VISION_OBJECTS_ROUTE_ID
    assert route.operations == (ModelOperation.VISION_OBJECT_DETECTION,)
    assert route.endpoint_host == "vision.googleapis.com"
    assert route.requires_live_effects_gate is True
    assert route.budget_feature == "vision_objects"
    assert result.metadata.estimated_cost_usd == pytest.approx(0.006)
    assert ledger.usage(feature_budget_scope("vision_objects")) == pytest.approx(0.006)


def test_label_aliases_keep_downstream_rules_working() -> None:
    assert normalize_label("  Mobile   Phone ") == "cell phone"
    assert normalize_label("Television") == "tv"
    assert normalize_label("Woman") == "person"
    assert normalize_label("Bicycle wheel") == "bicycle wheel"
    assert len(normalize_label("x" * 200)) == 80


def test_adapter_rejects_unpinnable_configuration() -> None:
    with pytest.raises(ValueError, match="API key"):
        GoogleVisionObjectsAdapter(api_key="")
    with pytest.raises(ValueError, match="model"):
        GoogleVisionObjectsAdapter(api_key="k", model="builtin/weekly")
    with pytest.raises(ValueError, match="HTTPS"):
        GoogleVisionObjectsAdapter(api_key="k", base_url="http://vision.googleapis.com/v1")


# -- enricher ------------------------------------------------------------------


def _keyframe(index: int, size: int = 64) -> Keyframe:
    return Keyframe(
        scene_index=index, sample_index=index, timestamp_ms=index * 1000,
        width=160, height=120, jpeg=b"\xff\xd8" + bytes(size),
    )


def test_batches_respect_the_image_and_size_limits() -> None:
    frames = [_keyframe(i) for i in range(20)]

    assert [len(b) for b in _batches(frames, max_items=16, max_bytes=10_000_000)] == [16, 4]
    # 3 frames of ~88 encoded bytes fit in 200 bytes per batch, 2 at a time.
    assert [len(b) for b in _batches(frames[:3], max_items=16, max_bytes=200)] == [2, 1]
    # A single oversized frame still forms its own batch.
    assert [len(b) for b in _batches([_keyframe(0, 5000)], max_items=16, max_bytes=100)] == [1]


def test_enricher_batches_keyframes_and_maps_detections() -> None:
    fake = FakeVision()
    gateway = _gateway(fake)
    enricher = GatewayObjectsEnricher(
        gateway=gateway, route_id=GOOGLE_VISION_OBJECTS_ROUTE_ID, model_version="google_vision:r"
    )

    results = enricher.enrich([_keyframe(i) for i in range(18)])

    assert [len(json.loads(r.content)["requests"]) for r in fake.requests] == [16, 2]
    assert len(results) == 18
    assert [item.label for item in results[0]] == ["laptop", "cell phone"]
    assert results[0][0].box.width == 0.5


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


def test_manifest_gets_google_objects_through_the_nested_gateway_call(tmp_path: Path) -> None:
    from app.schemas.uploads import VideoMetadata

    fake = FakeVision()
    ledger = InMemoryBudgetLedger()
    gateway = _gateway(fake, budget_ledger=ledger)
    provider = RemoteVisualIntelligenceProvider(
        face_model_path="/models/face.tflite",
        face_model_sha256="a" * 64,
        enrichers=(
            GatewayObjectsEnricher(
                gateway=gateway,
                route_id=GOOGLE_VISION_OBJECTS_ROUTE_ID,
                model_version=f"google_vision:{REVISION}",
            ),
        ),
        face_detector_factory=_NoFaces,
    )
    route = register_visual_route(
        gateway, provider=provider, model=provider.model, revision="sha256:" + "b" * 64
    )

    manifest = GatewayVisualIntelligenceProvider(gateway=gateway, route=route).analyze_with_context(
        source_path=_video(tmp_path),
        metadata=VideoMetadata(duration_ms=3000, width=160, height=120, has_audio=False),
        sample_rate_fps=2.0,
        scene_threshold=0.42,
        workspace_id="workspace-a",
        project_id="project-a",
    )

    assert "objects" not in manifest.summary["degraded_capabilities"]
    assert manifest.model_versions["object_detection"] == f"google_vision:{REVISION}"
    assert all(sample.scene_type == SceneType.SCREEN_RECORDING for sample in manifest.frame_samples)
    keyframes = manifest.summary["remote_keyframes"]["selected"]
    # Paid calls are attributed to the job's workspace, one unit per keyframe.
    assert ledger.usage(budget_scope("workspace-a")) == pytest.approx(keyframes * 0.0015)
    assert ledger.usage(budget_scope(None)) == 0.0


def test_vision_outage_degrades_only_objects(tmp_path: Path) -> None:
    from app.schemas.uploads import VideoMetadata

    gateway = _gateway(FakeVision(status=503))
    provider = RemoteVisualIntelligenceProvider(
        face_model_path="/models/face.tflite",
        face_model_sha256="a" * 64,
        enrichers=(
            GatewayObjectsEnricher(
                gateway=gateway, route_id=GOOGLE_VISION_OBJECTS_ROUTE_ID, model_version="g:r"
            ),
        ),
        face_detector_factory=_NoFaces,
    )

    manifest = provider.analyze(
        source_path=_video(tmp_path),
        metadata=VideoMetadata(duration_ms=3000, width=160, height=120, has_audio=False),
        sample_rate_fps=2.0,
        scene_threshold=0.42,
    )

    degraded = manifest.summary["degraded_capabilities"]
    assert degraded["objects"] == "unavailable:provider_unavailable"
    assert manifest.model_versions["object_detection"] == "unavailable"
    assert all(sample.objects == [] for sample in manifest.frame_samples)


# -- wiring --------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    values = {
        "APP_ENV": "local",
        "VISUAL_PROVIDER": "remote",
        "VISUAL_CAPTIONS_PROVIDER": "disabled",
        "VISUAL_EMBEDDINGS_PROVIDER": "disabled",
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_settings_build_the_google_objects_enricher_once_per_gateway() -> None:
    settings = _settings(
        GOOGLE_VISION_API_KEY="vision-key", GOOGLE_VISION_COST_PER_IMAGE_USD=0.0015
    )
    gateway = ModelGateway(adapters={}, routes=[])

    first = remote_visual_provider_from_settings(settings, gateway=gateway)
    second = remote_visual_provider_from_settings(settings, gateway=gateway)

    assert [r.route_id for r in gateway.routes] == [GOOGLE_VISION_OBJECTS_ROUTE_ID]
    route = gateway.routes[0]
    assert route.input_cost_per_million_usd == pytest.approx(1500.0)
    assert route.model == "google-vision-object-localization:builtin/stable"
    assert first.model == second.model == remote_visual_identity(settings)
    assert f"objects:google_vision:{REVISION}" in first.model


def test_missing_key_fails_loudly_and_disabled_needs_none() -> None:
    gateway = ModelGateway(adapters={}, routes=[])
    with pytest.raises(RuntimeError, match="GOOGLE_VISION_API_KEY"):
        remote_visual_provider_from_settings(_settings(), gateway=gateway)

    disabled = remote_visual_provider_from_settings(
        _settings(VISUAL_OBJECTS_PROVIDER="disabled"), gateway=gateway
    )
    assert gateway.routes == ()
    assert "objects:off" in disabled.model


def test_identity_changes_with_the_objects_revision() -> None:
    base = remote_visual_identity(_settings())
    bumped = remote_visual_identity(
        _settings(GOOGLE_VISION_OBJECTS_MODEL_REVISION="google-vision-objects-2027-01-01")
    )
    off = remote_visual_identity(_settings(VISUAL_OBJECTS_PROVIDER="disabled"))

    assert len({base, bumped, off}) == 3


_PRICED_VISION = {
    "visual_intelligence_provider": "remote",
    "visual_captions_provider": "disabled",
    "visual_embeddings_provider": "disabled",
    "google_vision_api_key": "vision-key",
    "google_vision_cost_per_image_usd": 0.0015,
}


def test_production_accepts_priced_google_objects() -> None:
    _validate_production_settings(_production_settings(process_role="worker", **_PRICED_VISION))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"google_vision_api_key": None}, "GOOGLE_VISION_API_KEY"),
        ({"google_vision_cost_per_image_usd": 0.0}, "GOOGLE_VISION_COST_PER_IMAGE_USD"),
        ({"google_vision_objects_model_revision": "builtin/stable"}, "immutable"),
    ],
)
def test_production_rejects_incomplete_google_objects(overrides: dict, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        _validate_production_settings(
            _production_settings(process_role="worker", **{**_PRICED_VISION, **overrides})
        )


def test_local_ml_visual_never_requires_google_settings() -> None:
    _validate_production_settings(_production_settings(process_role="worker"))
