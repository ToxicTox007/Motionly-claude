"""Task 17 object-detection template port: registry-only enablement.

Proves the objects enricher is selected by the *presence of a*
``vision.object_detection`` route in the gateway (a registry template route),
not by a vendor switch, that batch limits come from the route, that the alt
(differently-shaped) provider works through the same enricher, and that no
secret value is logged. No-route behaviour is unchanged: empty detections and
``objects: unavailable:not_configured``.

This file contains no vendor names (vendor tokens live only in the example
registry documents' operator fields and the archived differential module).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.services.inference_operations import InMemoryInferenceOperationRepository
from app.services.inference_runtime import build_inference_runtime
from app.services.inference_secrets import SecretResolver
from app.services.model_gateway import ModelOperation
from app.services.remote_visual import (
    GatewayObjectsEnricher,
    Keyframe,
    RemoteVisualIntelligenceProvider,
    _route_objects_enricher,
    objects_route,
    remote_visual_provider_from_settings,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "docs" / "ai-registry" / "examples"
SENTINEL = "sentinel-objects-secret-value"  # noqa: S105 - test sentinel, never real

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes"


def _keyframe(index: int, size: int = 32) -> Keyframe:
    return Keyframe(
        scene_index=index,
        sample_index=index,
        timestamp_ms=index * 1000,
        width=160,
        height=120,
        jpeg=b"\xff\xd8\xff" + bytes(size),
    )


def _write_bundle(registry_path: Path, tmp_path: Path) -> Path:
    """Map every ``bundle:`` secret_ref in the document to the sentinel value."""
    document = json.loads(registry_path.read_text(encoding="utf-8"))
    bundle: dict[str, str] = {}
    for adapter in document.get("adapters", []):
        ref = (adapter.get("auth") or {}).get("secret_ref")
        if isinstance(ref, str) and ref.startswith("bundle:"):
            bundle[ref.split(":", 1)[1]] = SENTINEL
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


def _runtime(registry_name: str, tmp_path: Path, transport: httpx.BaseTransport):
    registry_path = EXAMPLES / registry_name
    bundle = _write_bundle(registry_path, tmp_path)
    settings = get_settings().model_copy(
        update={
            "ai_registry_file": str(registry_path),
            "ai_secrets_bundle_file": str(bundle),
            "ai_secrets_dir": str(tmp_path / "secrets"),
            "redis_url": None,
        }
    )
    resolver = SecretResolver(settings=settings)
    runtime = build_inference_runtime(
        settings,
        operations_repository=InMemoryInferenceOperationRepository(),
        secret_resolver=resolver,
        transport=transport,
    )
    return runtime


# ---------------------------------------------------------------------------
# Fake providers (shaped like the two example registry documents)
# ---------------------------------------------------------------------------
def _google_shaped_transport(recorder: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        count = len(json.loads(request.content)["requests"])
        annotation = {
            "name": "Laptop",
            "score": 0.9,
            "boundingPoly": {
                "normalizedVertices": [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.6}]
            },
        }
        return httpx.Response(
            200,
            json={"responses": [{"localizedObjectAnnotations": [annotation]}] * count},
            request=request,
        )

    return httpx.MockTransport(handler)


def _alt_shaped_transport(recorder: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        count = len(json.loads(request.content)["frames"])
        item = {
            "objects": [
                {
                    "class_name": "Automobile",
                    "prob": 0.8,
                    "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
                }
            ]
        }
        return httpx.Response(200, json={"images": [item] * count}, request=request)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Registry-only enablement
# ---------------------------------------------------------------------------
def test_objects_route_detected_from_registry(tmp_path: Path) -> None:
    recorder: list[httpx.Request] = []
    runtime = _runtime("object-detection.json", tmp_path, _google_shaped_transport(recorder))

    route = objects_route(runtime.gateway)
    assert route is not None
    assert ModelOperation.VISION_OBJECT_DETECTION in route.operations
    # Batch limits come from the registry route, not a hard-coded default.
    assert route.max_batch == 16
    assert route.max_request_bytes == 9 * 1024 * 1024


def test_enricher_runs_through_the_registry_route(tmp_path: Path) -> None:
    recorder: list[httpx.Request] = []
    runtime = _runtime("object-detection.json", tmp_path, _google_shaped_transport(recorder))
    route = objects_route(runtime.gateway)

    enricher = _route_objects_enricher(
        runtime.gateway, route, model_version="objects:test"
    )
    results = enricher.enrich([_keyframe(i) for i in range(3)])

    assert len(results) == 3
    assert [d.label for d in results[0]] == ["laptop"]
    assert results[0][0].box.width == pytest.approx(0.4)
    # One request (3 images under the 16/9MiB limits).
    assert len(recorder) == 1
    assert json.loads(recorder[0].content).keys() == {"requests"}


def test_route_driven_batch_limits_split_requests(tmp_path: Path) -> None:
    recorder: list[httpx.Request] = []
    runtime = _runtime("object-detection.json", tmp_path, _google_shaped_transport(recorder))
    route = objects_route(runtime.gateway)

    enricher = GatewayObjectsEnricher(
        gateway=runtime.gateway,
        route_id=route.route_id,
        model_version="objects:test",
        max_images_per_request=route.max_batch,
        max_request_bytes=route.max_request_bytes,
    )
    results = enricher.enrich([_keyframe(i) for i in range(18)])

    # 18 keyframes under a 16-image cap split into 16 + 2.
    assert [len(json.loads(r.content)["requests"]) for r in recorder] == [16, 2]
    assert len(results) == 18


def test_alt_provider_runs_through_the_same_enricher(tmp_path: Path) -> None:
    recorder: list[httpx.Request] = []
    runtime = _runtime("object-detection-alt.json", tmp_path, _alt_shaped_transport(recorder))
    route = objects_route(runtime.gateway)
    assert route is not None
    # The alt provider declares smaller limits; the enricher honours them.
    assert route.max_batch == 8
    assert route.max_request_bytes == 6 * 1024 * 1024

    enricher = _route_objects_enricher(runtime.gateway, route, model_version="objects:alt")
    results = enricher.enrich([_keyframe(i) for i in range(10)])

    # 10 keyframes under an 8-image cap split into 8 + 2.
    assert [len(json.loads(r.content)["frames"]) for r in recorder] == [8, 2]
    assert len(results) == 10
    assert [d.label for d in results[0]] == ["car"]


# ---------------------------------------------------------------------------
# No-route behaviour (unchanged)
# ---------------------------------------------------------------------------
def test_no_route_leaves_objects_not_configured(tmp_path: Path) -> None:
    # A gateway with no vision.object_detection route (empty dev registry).
    settings = get_settings().model_copy(update={"ai_registry_file": None, "redis_url": None})
    runtime = build_inference_runtime(
        settings, operations_repository=InMemoryInferenceOperationRepository()
    )
    assert objects_route(runtime.gateway) is None

    provider = RemoteVisualIntelligenceProvider(
        face_model_path="/models/face.tflite",
        face_model_sha256="a" * 64,
        enrichers=(),
        face_detector_factory=lambda: _NoFaces(),
    )
    results, status = provider._enrich("objects", [_keyframe(0)])
    assert results == []
    assert status == "unavailable:not_configured"


class _NoFaces:
    def detect(self, rgb):  # noqa: ANN001, ARG002
        return []


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------
def test_no_secret_in_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    recorder: list[httpx.Request] = []
    runtime = _runtime("object-detection.json", tmp_path, _google_shaped_transport(recorder))
    route = objects_route(runtime.gateway)
    enricher = _route_objects_enricher(runtime.gateway, route, model_version="objects:test")

    with caplog.at_level(logging.DEBUG):
        enricher.enrich([_keyframe(i) for i in range(2)])

    blob = "\n".join(record.getMessage() for record in caplog.records)
    assert SENTINEL not in blob
    # The custom auth header carries the sentinel to the provider (and only
    # there); it must never appear in a log record.
    assert any(r.headers.get("x-goog-api-key") == SENTINEL for r in recorder)


def test_provider_from_settings_prefers_registry_route(tmp_path: Path) -> None:
    """When the gateway already serves objects, the builder uses that route and
    does not require any vendor objects setting."""
    recorder: list[httpx.Request] = []
    runtime = _runtime("object-detection.json", tmp_path, _google_shaped_transport(recorder))

    settings = get_settings().model_copy(
        update={
            "visual_provider": "remote",
            "visual_objects_provider": "disabled",
            "visual_captions_provider": "disabled",
            "visual_embeddings_provider": "disabled",
            "visual_cpu_face_model_path": "/models/face.tflite",
            "visual_cpu_face_model_sha256": "a" * 64,
        }
    )
    provider = remote_visual_provider_from_settings(settings, gateway=runtime.gateway)
    objects = [e for e in provider._enrichers.values() if e.capability == "objects"]
    assert len(objects) == 1
    assert objects[0].route_id == objects_route(runtime.gateway).route_id
    assert objects[0].max_images_per_request == 16
