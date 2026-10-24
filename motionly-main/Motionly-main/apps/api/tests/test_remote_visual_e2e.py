"""Remote keyframe embeddings and the full GPU-free visual manifest, end to end."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.main import _validate_production_settings
from app.schemas.broll import BrollGenerateRequest
from app.schemas.plans import BrollRequest
from app.schemas.uploads import VideoMetadata
from app.schemas.visual import BoundingBox, FaceDetection, SceneSegment, SceneType, VideoManifest
from app.services.broll_intelligence import manifest_embeddings_match, without_scene_embeddings
from app.services.model_domain_adapters import (
    GatewayClipEmbeddingProvider,
    GatewayVisualIntelligenceProvider,
    register_visual_route,
)
from app.services.model_gateway import (
    InMemoryBudgetLedger,
    ModelGateway,
    budget_scope,
    feature_budget_scope,
)
from app.services.model_gateway_factory import model_gateway_from_settings
from app.services.remote_visual import (
    GatewayKeyframeEmbeddingsEnricher,
    Keyframe,
    RemoteVisualIntelligenceProvider,
    remote_visual_identity,
    remote_visual_provider_from_settings,
)
from tests.test_service_hardening import _production_settings

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

JINA_REVISION = "jina-clip-v2-2024-11-05"
DIMS = 32
FACE = FaceDetection(confidence=0.9, box=BoundingBox(x=0.4, y=0.2, width=0.2, height=0.2))


# -- fake remote APIs ------------------------------------------------------------


class FakeRemoteApis:
    """One router for Google Vision, OpenRouter and Jina; any host can be failed."""

    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: dict[str, list[dict]] = {"vision": [], "openrouter": [], "jina": []}

    def __call__(self, client, method, url, **kwargs):  # noqa: ANN001
        del client
        host = httpx.URL(url).host
        name = {
            "vision.googleapis.com": "vision",
            "openrouter.ai": "openrouter",
            "api.jina.ai": "jina",
        }[host]
        body = kwargs.get("json") or {}
        self.calls[name].append(body)
        request = httpx.Request(method, url)
        if self.fail == name:
            return httpx.Response(503, json={"error": "busy"}, request=request)
        return httpx.Response(200, json=getattr(self, f"_{name}")(body), request=request)

    @staticmethod
    def _vision(body: dict) -> dict:
        laptop = {
            "name": "Laptop",
            "score": 0.9,
            "boundingPoly": {
                "normalizedVertices": [{"x": 0.1, "y": 0.1}, {"x": 0.6, "y": 0.6}]
            },
        }
        return {"responses": [{"localizedObjectAnnotations": [laptop]} for _ in body["requests"]]}

    @staticmethod
    def _openrouter(body: dict) -> dict:
        count = sum(1 for p in body["messages"][0]["content"] if p["type"] == "image_url")
        captions = [{"index": i, "caption": f"a desk with a laptop {i}"} for i in range(count)]
        return {
            "choices": [{"message": {"content": json.dumps({"captions": captions})}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 40},
        }

    @staticmethod
    def _jina(body: dict) -> dict:
        dims = body["dimensions"]
        data = []
        for index, _item in enumerate(body["input"]):
            vector = [math.sin(index + d) for d in range(dims)]
            norm = math.sqrt(sum(v * v for v in vector))
            data.append({"index": index, "embedding": [v / norm for v in vector]})
        return {"model": "jina-clip-v2", "data": data, "usage": {"total_tokens": 10}}


def _install(monkeypatch: pytest.MonkeyPatch, apis: FakeRemoteApis) -> None:
    # A plain function, so httpx binds it as Client.request(self, ...).
    def request(client, method, url, **kwargs):  # noqa: ANN001
        return apis(client, method, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "request", request)


class FakeFaces:
    def detect(self, rgb):
        return [FACE]


def _video(tmp_path: Path, colors=((200, 40, 40), (40, 40, 200), (40, 200, 40))) -> Path:
    path = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for color in colors:
        for i in range(12):
            frame = np.full((120, 160, 3), color, dtype=np.uint8)
            cv2.rectangle(frame, (10 + i * 4, 40), (30 + i * 4, 80), (255, 255, 255), -1)
            writer.write(frame)
    writer.release()
    return path


def _settings(**overrides) -> Settings:
    values = {
        "APP_ENV": "local",
        "VISUAL_PROVIDER": "remote",
        "EMBEDDINGS_PROVIDER": "jina",
        "GOOGLE_VISION_API_KEY": "vision-key",
        "GOOGLE_VISION_COST_PER_IMAGE_USD": 0.0015,
        "OPENROUTER_API_KEY": "openrouter-key",
        "VISUAL_CAPTION_INPUT_COST_PER_MILLION_USD": 0.3,
        "VISUAL_CAPTION_OUTPUT_COST_PER_MILLION_USD": 2.5,
        "JINA_API_KEY": "jina-key",
        "JINA_EMBEDDINGS_DIMENSIONS": DIMS,
        "JINA_TEXT_COST_PER_MILLION_TOKENS_USD": 0.05,
        "JINA_IMAGE_COST_PER_IMAGE_USD": 0.0005,
        "MODEL_GATEWAY_FEATURE_BUDGETS_USD": (
            '{"vision_objects": 5, "vision_captions": 5, "embeddings": 5}'
        ),
        **overrides,
    }
    return Settings(_env_file=None, **values)


def _run_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, fail: str | None = None, **overrides
):
    apis = FakeRemoteApis(fail=fail)
    _install(monkeypatch, apis)
    monkeypatch.setattr(
        "app.services.remote_visual.load_cpu_face_detector", lambda **_kwargs: FakeFaces()
    )
    settings = _settings(**overrides)
    gateway = model_gateway_from_settings(settings)
    ledger = InMemoryBudgetLedger()
    gateway._budget_ledger = ledger  # noqa: SLF001 - observe spend in the test
    provider = remote_visual_provider_from_settings(settings, gateway=gateway)
    route = register_visual_route(
        gateway, provider=provider, model=provider.model, revision="sha256:" + "d" * 64
    )
    manifest = GatewayVisualIntelligenceProvider(gateway=gateway, route=route).analyze_with_context(
        source_path=_video(tmp_path),
        metadata=VideoMetadata(duration_ms=3600, width=160, height=120, has_audio=False),
        sample_rate_fps=2.0,
        scene_threshold=0.42,
        workspace_id="workspace-a",
        project_id="project-a",
    )
    return manifest, apis, ledger, settings, gateway


# -- full remote manifest ----------------------------------------------------------


def test_full_remote_manifest_has_every_capability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest, apis, ledger, settings, _gateway = _run_manifest(monkeypatch, tmp_path)

    VideoManifest.model_validate(manifest.model_dump())
    summary = manifest.summary
    assert summary["degraded_capabilities"] == {}
    assert summary["embedding_revision"] == JINA_REVISION
    keyframes = summary["remote_keyframes"]["selected"]
    assert keyframes == len(manifest.scenes) >= 3
    for sample in manifest.frame_samples:
        assert [item.label for item in sample.objects] == ["laptop"]
        assert sample.caption.startswith("a desk with a laptop")
        assert len(sample.embedding) == DIMS
        assert sample.faces and sample.dominant_colors
    for scene in manifest.scenes:
        assert len(scene.embedding) == DIMS
        assert scene.scene_type == SceneType.TALKING_HEAD  # faces win, as on the GPU path
    versions = manifest.model_versions
    assert versions["object_detection"] == (
        f"google_vision:{settings.google_vision_objects_model_revision}"
    )
    assert versions["captions"] == f"openrouter:{settings.visual_caption_model_revision}"
    assert versions["clip"] == f"jina:{JINA_REVISION}:{DIMS}"
    # One keyframe per scene went to each provider, in as few requests as allowed.
    assert [len(body["requests"]) for body in apis.calls["vision"]] == [keyframes]
    assert [len(body["input"]) for body in apis.calls["jina"]] == [keyframes]
    assert len(apis.calls["openrouter"]) == math.ceil(keyframes / 8)
    assert all(
        body["provider"] == {"zdr": True, "data_collection": "deny"}
        for body in apis.calls["openrouter"]
    )
    # Every paid call is attributed to the job's workspace and its feature cap.
    assert ledger.usage(budget_scope("workspace-a")) > 0
    assert ledger.usage(budget_scope(None)) == 0.0
    assert ledger.usage(feature_budget_scope("vision_objects")) == pytest.approx(
        keyframes * 0.0015
    )
    assert ledger.usage(feature_budget_scope("embeddings")) == pytest.approx(keyframes * 0.0005)
    assert ledger.usage(feature_budget_scope("vision_captions")) > 0


@pytest.mark.parametrize(
    ("fail", "capability", "version_key"),
    [
        ("vision", "objects", "object_detection"),
        ("openrouter", "captions", "captions"),
        ("jina", "embeddings", "clip"),
    ],
)
def test_each_provider_outage_degrades_only_its_capability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail: str, capability: str, version_key: str
) -> None:
    manifest, _apis, _ledger, _settings_, _gateway = _run_manifest(
        monkeypatch, tmp_path, fail=fail
    )

    assert manifest.summary["degraded_capabilities"] == {
        capability: "unavailable:provider_unavailable"
    }
    assert manifest.model_versions[version_key] == "unavailable"
    sample = manifest.frame_samples[0]
    assert bool(sample.objects) is (capability != "objects")
    assert bool(sample.caption) is (capability != "captions")
    assert bool(sample.embedding) is (capability != "embeddings")
    assert ("embedding_revision" in manifest.summary) is (capability != "embeddings")


def test_identity_reflects_every_enricher_and_the_embedding_dimensions() -> None:
    settings = _settings()
    identity = remote_visual_identity(settings)

    assert f"embeddings:jina:{JINA_REVISION}:{DIMS}" in identity
    assert "objects:google_vision:" in identity and "captions:openrouter:" in identity
    assert remote_visual_identity(_settings(JINA_EMBEDDINGS_DIMENSIONS=64)) != identity
    assert "embeddings:off" in remote_visual_identity(
        _settings(VISUAL_EMBEDDINGS_PROVIDER="disabled")
    )


# -- embeddings enricher -------------------------------------------------------------


def _keyframe(index: int) -> Keyframe:
    return Keyframe(
        scene_index=index, sample_index=index, timestamp_ms=index * 1000,
        width=160, height=120, jpeg=b"\xff\xd8\xff\xe0frame" + bytes([index]),
    )


def test_enricher_batches_and_rounds_like_local_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    apis = FakeRemoteApis()
    _install(monkeypatch, apis)
    settings = _settings(VISUAL_OBJECTS_PROVIDER="disabled", VISUAL_CAPTIONS_PROVIDER="disabled")
    gateway = ModelGateway(adapters={}, routes=[])
    provider = remote_visual_provider_from_settings(settings, gateway=gateway)
    enricher = provider._enrichers["embeddings"]  # noqa: SLF001
    enricher.max_images_per_request = 4

    vectors = enricher.enrich([_keyframe(i) for i in range(10)])

    assert [len(body["input"]) for body in apis.calls["jina"]] == [4, 4, 2]
    assert len(vectors) == 10 and all(len(vector) == DIMS for vector in vectors)
    assert all(value == round(value, 6) for vector in vectors for value in vector)


def test_wrong_vector_count_degrades_embeddings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    class Short:
        def invoke(self, request, *, context):
            from app.services.model_gateway import EmbeddingsOutput

            return SimpleNamespace(output=EmbeddingsOutput(vectors=((0.1,),), raw_response={}))

    enricher = GatewayKeyframeEmbeddingsEnricher(
        gateway=Short(), route_id="jina-embeddings-image", model_version="jina:r",
        embedding_revision="r",
    )
    provider = RemoteVisualIntelligenceProvider(
        face_model_path="/m", face_model_sha256="a" * 64, enrichers=(enricher,),
        face_detector_factory=FakeFaces,
    )

    manifest = provider.analyze(
        source_path=_video(tmp_path),
        metadata=VideoMetadata(duration_ms=3600, width=160, height=120, has_audio=False),
        sample_rate_fps=2.0,
        scene_threshold=0.42,
    )

    assert manifest.summary["degraded_capabilities"]["embeddings"] == (
        "unavailable:provider_malformed_response"
    )
    assert "embedding_revision" not in manifest.summary


def test_builder_registers_the_jina_routes_once_and_reuses_existing_ones() -> None:
    settings = _settings(VISUAL_OBJECTS_PROVIDER="disabled", VISUAL_CAPTIONS_PROVIDER="disabled")
    bare = ModelGateway(adapters={}, routes=[])

    remote_visual_provider_from_settings(settings, gateway=bare)
    remote_visual_provider_from_settings(settings, gateway=bare)

    assert sorted(route.route_id for route in bare.routes) == [
        "jina-embeddings-image",
        "jina-embeddings-text",
    ]
    image_route = next(r for r in bare.routes if r.route_id == "jina-embeddings-image")
    assert image_route.model_revision == JINA_REVISION
    # The API gateway already has the B-roll routes; the visual provider shares them.
    api_gateway = model_gateway_from_settings(settings)
    from app.services.model_adapters import JinaEmbeddingsAdapter
    from app.services.model_domain_adapters import register_jina_embedding_routes

    register_jina_embedding_routes(
        api_gateway,
        adapter=JinaEmbeddingsAdapter(api_key="jina-key", dimensions=DIMS),
        model="jina-clip-v2",
        revision=JINA_REVISION,
        text_cost_per_million_tokens_usd=0.05,
        image_cost_per_image_usd=0.0005,
    )
    before = len(api_gateway.routes)
    remote_visual_provider_from_settings(settings, gateway=api_gateway)
    assert len(api_gateway.routes) == before
    assert GatewayClipEmbeddingProvider(gateway=api_gateway).preferred_model_revision == (
        JINA_REVISION
    )


def test_missing_jina_key_fails_loudly() -> None:
    with pytest.raises(RuntimeError, match="JINA_API_KEY"):
        remote_visual_provider_from_settings(
            _settings(VISUAL_OBJECTS_PROVIDER="disabled", VISUAL_CAPTIONS_PROVIDER="disabled",
                      JINA_API_KEY=""),
            gateway=ModelGateway(adapters={}, routes=[]),
        )


def test_jina_dimensions_cannot_exceed_the_broll_vector_limit() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, JINA_EMBEDDINGS_DIMENSIONS=2048)


# -- B-roll embedding-space gate -------------------------------------------------------


def _manifest(embedding_revision: str | None) -> VideoManifest:
    summary = {"scene_count": 1}
    if embedding_revision:
        summary["embedding_revision"] = embedding_revision
    return VideoManifest(
        duration_ms=4000,
        provider="remote_visual",
        scenes=[
            SceneSegment(
                index=0, start_ms=0, end_ms=4000, representative_timestamp_ms=1000,
                caption="a desk", embedding=[0.1] * DIMS, visual_importance_score=0.6,
            )
        ],
        summary=summary,
    )


def test_embedding_space_rules() -> None:
    assert manifest_embeddings_match(_manifest(JINA_REVISION), JINA_REVISION)
    assert not manifest_embeddings_match(_manifest(JINA_REVISION), "3d74acf9a28c")
    # Manifests that declare nothing (local GPU, fixtures) keep existing behaviour.
    assert manifest_embeddings_match(_manifest(None), "3d74acf9a28c")
    assert manifest_embeddings_match(None, JINA_REVISION)
    assert manifest_embeddings_match(_manifest(JINA_REVISION), None)
    stripped = without_scene_embeddings(_manifest(JINA_REVISION))
    assert stripped.scenes[0].embedding == [] and stripped.scenes[0].caption == "a desk"


class _StopAtPlacement(Exception):
    pass


def _run_broll_until_placement(monkeypatch, *, manifest, preferred_revision):
    import app.services.layer_jobs as layer_jobs

    captured: dict[str, object] = {}
    planner_request = BrollRequest(
        start_ms=500, end_ms=2000, keywords=["desk"], priority=0.8,
        semantic_embedding=[0.2] * DIMS,
    )
    monkeypatch.setattr(layer_jobs, "latest_visual_manifest_for_broll", lambda **_k: manifest)
    monkeypatch.setattr(layer_jobs, "desired_requests", lambda plan, density: [planner_request])

    def capture(*, requests, visual_manifest, duration_ms):
        captured.update(requests=requests, manifest=visual_manifest)
        raise _StopAtPlacement

    monkeypatch.setattr(layer_jobs, "apply_stage4_broll_rules", capture)
    repository = SimpleNamespace(
        active_plan=lambda **_k: SimpleNamespace(
            id=uuid4(), plan=SimpleNamespace(target_duration_s=4)
        )
    )
    with pytest.raises(_StopAtPlacement):
        layer_jobs.execute_broll_generation(
            repository=repository,
            billing_repository=None,
            providers=[object()],
            storage_signer=None,
            stock_asset_enrichment_queue=None,
            settings=SimpleNamespace(billing_enabled=False),
            embedding_provider=SimpleNamespace(preferred_model_revision=preferred_revision),
            workspace_id=uuid4(),
            project_id=uuid4(),
            user_id=None,
            payload=BrollGenerateRequest(),
        )
    return captured


def test_broll_uses_manifest_vectors_when_the_space_matches(monkeypatch) -> None:
    captured = _run_broll_until_placement(
        monkeypatch, manifest=_manifest(JINA_REVISION), preferred_revision=JINA_REVISION
    )

    assert captured["manifest"].scenes[0].embedding == [0.1] * DIMS
    assert captured["requests"][0].semantic_embedding == [0.2] * DIMS


def test_broll_drops_vectors_from_a_different_space(monkeypatch) -> None:
    captured = _run_broll_until_placement(
        monkeypatch,
        manifest=_manifest(JINA_REVISION),
        preferred_revision="3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268",
    )

    assert captured["manifest"].scenes[0].embedding == []
    assert captured["manifest"].scenes[0].caption == "a desk"
    # Planner-copied scene vectors are dropped too; each slot embeds its query.
    assert captured["requests"][0].semantic_embedding == []


def test_broll_keeps_undeclared_manifests_unchanged(monkeypatch) -> None:
    captured = _run_broll_until_placement(
        monkeypatch, manifest=_manifest(None), preferred_revision="anything"
    )

    assert captured["manifest"].scenes[0].embedding == [0.1] * DIMS
    assert captured["requests"][0].semantic_embedding == [0.2] * DIMS


# -- production ---------------------------------------------------------------------


_PRICED_REMOTE = {
    "visual_intelligence_provider": "remote",
    "embeddings_provider": "jina",
    "jina_api_key": "jina-key",
    "jina_text_cost_per_million_tokens_usd": 0.05,
    "jina_image_cost_per_image_usd": 0.0005,
    "google_vision_api_key": "vision-key",
    "google_vision_cost_per_image_usd": 0.0015,
    "visual_caption_input_cost_per_million_usd": 0.3,
    "visual_caption_output_cost_per_million_usd": 2.5,
}


def test_production_accepts_the_fully_priced_remote_visual_stack() -> None:
    _validate_production_settings(_production_settings(process_role="worker", **_PRICED_REMOTE))


def test_production_requires_one_embedding_space() -> None:
    with pytest.raises(RuntimeError, match="share one embedding space"):
        _validate_production_settings(
            _production_settings(
                process_role="worker", **{**_PRICED_REMOTE, "embeddings_provider": "local_clip"}
            )
        )
    # Turning keyframe embeddings off lifts the requirement.
    _validate_production_settings(
        _production_settings(
            process_role="worker",
            **{
                **_PRICED_REMOTE,
                "embeddings_provider": "local_clip",
                "visual_embeddings_provider": "disabled",
            },
        )
    )
