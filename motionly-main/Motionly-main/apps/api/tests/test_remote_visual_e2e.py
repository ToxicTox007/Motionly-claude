"""Remote keyframe embeddings and the full GPU-free visual manifest, end to end."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.main import _validate_production_settings
from app.schemas.broll import BrollGenerateRequest
from app.schemas.plans import BrollRequest
from app.schemas.uploads import VideoMetadata
from app.schemas.visual import BoundingBox, FaceDetection, SceneSegment, VideoManifest
from app.services.broll_intelligence import manifest_embeddings_match, without_scene_embeddings
from app.services.model_gateway import (
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelRoute,
)
from app.services.remote_visual import (
    GatewayKeyframeEmbeddingsEnricher,
    Keyframe,
    RemoteVisualIntelligenceProvider,
)
from tests.test_service_hardening import _production_settings

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

EMBEDDING_REVISION = "remote-embed-rev-2024-11-05"
DIMS = 32
FACE = FaceDetection(confidence=0.9, box=BoundingBox(x=0.4, y=0.2, width=0.2, height=0.2))

# Task 11/12 rewire vision.caption and embeddings.image to capability routes;
# Task 19 removed the settings-driven captions/embeddings wiring (objects stays,
# gated on the item-2 live run). These tests register the caption and embeddings
# routes directly and build the remote provider with explicit enrichers so the
# kept GatewayCaptionsEnricher / GatewayKeyframeEmbeddingsEnricher stay covered.
CAPTIONS_ROUTE_ID = "remote-vision-captions"
EMBEDDINGS_ROUTE_ID = "remote-embeddings-image"
OBJECTS_ROUTE_ID = "remote-vision-objects"




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
# -- embeddings enricher -------------------------------------------------------------


def _keyframe(index: int) -> Keyframe:
    return Keyframe(
        scene_index=index, sample_index=index, timestamp_ms=index * 1000,
        width=160, height=120, jpeg=b"\xff\xd8\xff\xe0frame" + bytes([index]),
    )


def test_enricher_batches_and_rounds_like_local_clip() -> None:
    # Task 19 removed the vendor embeddings adapter; drive the kept
    # GatewayKeyframeEmbeddingsEnricher against a registered embeddings route
    # with a deterministic fake adapter so batching/rounding stays covered.
    from app.services.model_gateway import AdapterResult, EmbeddingsOutput, ModelUsage

    batch_sizes: list[int] = []

    class _EmbeddingsAdapter:
        def invoke(self, *, route: ModelRoute, request: object) -> AdapterResult:
            images = list(getattr(request, "images", ()))
            batch_sizes.append(len(images))
            vectors = tuple(
                tuple(round(math.sin(i + d), 6) for d in range(DIMS)) for i in range(len(images))
            )
            return AdapterResult(
                output=EmbeddingsOutput(vectors=vectors, raw_response={}),
                usage=ModelUsage(output_units=len(images)),
            )

    gateway = ModelGateway(adapters={"embeddings": _EmbeddingsAdapter()}, routes=[])
    gateway.register_route(
        ModelRoute(
            route_id=EMBEDDINGS_ROUTE_ID,
            capability=ModelCapability.EMBEDDINGS,
            adapter="embeddings",
            provider="remote",
            model="remote-clip",
            model_revision=EMBEDDING_REVISION,
            operations=(ModelOperation.EMBEDDINGS_IMAGE,),
            priority=10,
        )
    )
    enricher = GatewayKeyframeEmbeddingsEnricher(
        gateway=gateway,
        route_id=EMBEDDINGS_ROUTE_ID,
        model_version=f"remote:{EMBEDDING_REVISION}:{DIMS}",
        embedding_revision=EMBEDDING_REVISION,
        max_images_per_request=4,
    )

    vectors = enricher.enrich([_keyframe(i) for i in range(10)])

    assert batch_sizes == [4, 4, 2]
    assert len(vectors) == 10 and all(len(vector) == DIMS for vector in vectors)
    assert all(value == round(value, 6) for vector in vectors for value in vector)


def test_wrong_vector_count_degrades_embeddings(tmp_path) -> None:
    class Short:
        def invoke(self, request, *, context):
            from app.services.model_gateway import EmbeddingsOutput

            return SimpleNamespace(output=EmbeddingsOutput(vectors=((0.1,),), raw_response={}))

    enricher = GatewayKeyframeEmbeddingsEnricher(
        gateway=Short(), route_id=EMBEDDINGS_ROUTE_ID, model_version="remote:r",
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
    assert manifest_embeddings_match(_manifest(EMBEDDING_REVISION), EMBEDDING_REVISION)
    assert not manifest_embeddings_match(_manifest(EMBEDDING_REVISION), "3d74acf9a28c")
    # Manifests that declare nothing (local GPU, fixtures) keep existing behaviour.
    assert manifest_embeddings_match(_manifest(None), "3d74acf9a28c")
    assert manifest_embeddings_match(None, EMBEDDING_REVISION)
    assert manifest_embeddings_match(_manifest(EMBEDDING_REVISION), None)
    stripped = without_scene_embeddings(_manifest(EMBEDDING_REVISION))
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
        monkeypatch, manifest=_manifest(EMBEDDING_REVISION), preferred_revision=EMBEDDING_REVISION
    )

    assert captured["manifest"].scenes[0].embedding == [0.1] * DIMS
    assert captured["requests"][0].semantic_embedding == [0.2] * DIMS


def test_broll_drops_vectors_from_a_different_space(monkeypatch) -> None:
    captured = _run_broll_until_placement(
        monkeypatch,
        manifest=_manifest(EMBEDDING_REVISION),
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
    "visual_caption_input_cost_per_million_usd": 0.3,
    "visual_caption_output_cost_per_million_usd": 2.5,
}


def test_production_accepts_the_fully_priced_remote_visual_stack() -> None:
    # Keyframe object detection, captions and embeddings are registry capability
    # routes (plan Tasks 17, 11, 12); the remote visual stack no longer requires
    # a vendor object-detection or embeddings setting or a code-level
    # embedding-space check (plan Tasks 19 item 2 and 26). B-roll ranking uses
    # local CLIP or its lexical fallback until that route lands.
    _validate_production_settings(_production_settings(process_role="worker", **_PRICED_REMOTE))
