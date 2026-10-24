"""GPU-free visual analysis (VISUAL_PROVIDER=remote).

Every sampled frame gets local CPU signals: OpenCV frame sampling and scene
changes, optical-flow camera motion, dominant colours, and MediaPipe Tasks face
detection. Only one keyframe per scene (the scene's most important frame, up to
``max_keyframes``) is sent to the paid remote enrichers for objects, captions and
embeddings, and each scene's keyframe results are copied to its other frames.

Each remote capability degrades on its own: if its enricher is not configured,
over budget or quota, or fails, the manifest is still built and the capability is
listed in ``summary.degraded_capabilities`` with ``model_versions`` set to
``"unavailable"``.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.api.errors import ApiError
from app.schemas.uploads import VideoMetadata
from app.schemas.visual import (
    BoundingBox,
    DominantColor,
    FrameSample,
    ObjectDetection,
    SceneSegment,
    VideoManifest,
)
from app.services.cpu_vision import (
    BLAZE_FACE_FULL_RANGE,
    CpuVisionModelError,
    load_cpu_face_detector,
)
from app.services.model_gateway import (
    EmbeddingsOutput,
    ImageEmbeddingsRequest,
    ModelCallContext,
    ModelGatewayError,
    ModelProvidersExhausted,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    VisionOutput,
    VisionRequest,
    inherited_call_context,
)
from app.services.provider_resilience import ProviderCallFailure, classify_provider_exception
from app.services.visual_intelligence import (
    _build_scenes_from_samples,
    _camera_motion,
    _classify_scene_type,
    _importance_score,
    _manifest_summary,
    _sample_video_frames,
)

logger = logging.getLogger(__name__)

OBJECTS = "objects"
CAPTIONS = "captions"
EMBEDDINGS = "embeddings"
REMOTE_CAPABILITIES = (OBJECTS, CAPTIONS, EMBEDDINGS)
# model_versions keys, aligned with the local GPU manifest where they overlap.
_MODEL_VERSION_KEYS = {OBJECTS: "object_detection", CAPTIONS: "captions", EMBEDDINGS: "clip"}
_MAX_CAPTION_CHARS = 240


@dataclass(frozen=True)
class Keyframe:
    """One scene's representative frame, as sent to remote enrichers."""

    scene_index: int
    sample_index: int
    timestamp_ms: int
    width: int
    height: int
    jpeg: bytes


class KeyframeEnricher(Protocol):
    """Adds one remote capability to keyframes.

    ``enrich`` returns one result per keyframe, in order: a list of
    ``ObjectDetection`` (objects), a caption string (captions), or a list of
    floats (embeddings). It raises to degrade the capability.
    """

    capability: str
    model_version: str

    def enrich(self, keyframes: Sequence[Keyframe]) -> Sequence[object]: ...


class EnrichmentUnavailable(Exception):
    """Raised by an enricher to degrade its capability with a specific reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RemoteVisualIntelligenceProvider:
    provider_name = "remote_visual"

    def __init__(
        self,
        *,
        face_model_path: str,
        face_model_sha256: str,
        enrichers: Sequence[KeyframeEnricher] = (),
        max_keyframes: int = 24,
        jpeg_quality: int = 85,
        identity: str | None = None,
        face_detector_factory: Callable[[], object] | None = None,
    ) -> None:
        if max_keyframes < 1:
            raise ValueError("max_keyframes must be positive")
        capabilities = [enricher.capability for enricher in enrichers]
        unknown = sorted(set(capabilities) - set(REMOTE_CAPABILITIES))
        if unknown or len(capabilities) != len(set(capabilities)):
            raise ValueError(f"invalid keyframe enrichers: {capabilities}")
        self._enrichers = {enricher.capability: enricher for enricher in enrichers}
        self.face_model_path = face_model_path
        self.face_model_sha256 = face_model_sha256.strip().lower()
        self.max_keyframes = max_keyframes
        self.jpeg_quality = jpeg_quality
        self.model = identity or remote_visual_identity_from_parts(
            face_model_sha256=self.face_model_sha256,
            enricher_versions={cap: e.model_version for cap, e in self._enrichers.items()},
        )
        self._face_detector_factory = face_detector_factory or self._load_face_detector
        self._face_detector: object | None = None
        self._face_status: str | None = None

    def analyze(
        self,
        *,
        source_path: Path,
        metadata: VideoMetadata,
        sample_rate_fps: float,
        scene_threshold: float,
    ) -> VideoManifest:
        import cv2
        import numpy as np

        raw_frames = _sample_video_frames(
            cv2=cv2,
            np=np,
            source_path=source_path,
            sample_rate_fps=sample_rate_fps,
            scene_threshold=scene_threshold,
        )
        if not raw_frames:
            raise ApiError(422, "visual_frames_empty", "No frames could be sampled from video.")

        degraded: dict[str, str] = {}
        face_detector = self._faces_or_none(degraded)
        samples: list[FrameSample] = []
        frames = []
        previous_gray = None
        for raw in raw_frames:
            frame = raw["frame"]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            motion = _camera_motion(cv2=cv2, np=np, previous_gray=previous_gray, gray=gray)
            previous_gray = gray
            faces = []
            if face_detector is not None:
                try:
                    faces = face_detector.detect(rgb)
                except Exception as exc:  # noqa: BLE001 - CPU signal degrades, never fails
                    logger.warning(
                        "cpu_face_detection_failed", extra={"error_type": type(exc).__name__}
                    )
                    degraded["faces"] = "unavailable:runtime_error"
                    face_detector = None
                    faces = []
            samples.append(
                _frame_sample(
                    timestamp_ms=raw["timestamp_ms"],
                    frame=frame,
                    scene_change_score=raw["scene_change_score"],
                    colors=_dominant_colors(cv2=cv2, np=np, rgb=rgb),
                    faces=faces,
                    motion=motion,
                )
            )
            frames.append(frame)

        duration_ms = metadata.duration_ms or max(sample.timestamp_ms for sample in samples)
        scenes = _build_scenes_from_samples(samples=samples, duration_ms=duration_ms)
        keyframes = self._keyframes(cv2=cv2, samples=samples, frames=frames, scenes=scenes)
        model_versions = {
            "frame_sampling": f"opencv-{cv2.__version__}",
            "face_detection": (
                "unavailable"
                if "faces" in degraded
                else f"{BLAZE_FACE_FULL_RANGE.name}@sha256:{self.face_model_sha256}"
            ),
        }
        scene_results: dict[str, dict[int, object]] = {}
        for capability in REMOTE_CAPABILITIES:
            results, status = self._enrich(capability, keyframes)
            key = _MODEL_VERSION_KEYS[capability]
            if status is not None:
                degraded[capability] = status
                model_versions[key] = "unavailable"
                continue
            scene_results[capability] = {
                keyframe.scene_index: result
                for keyframe, result in zip(keyframes, results, strict=True)
            }
            model_versions[key] = self._enrichers[capability].model_version

        samples = _apply_scene_results(samples=samples, scenes=scenes, results=scene_results)
        scenes = _build_scenes_from_samples(samples=samples, duration_ms=duration_ms)
        summary = _manifest_summary(samples=samples, scenes=scenes)
        summary.update(
            {
                "execution": "cpu_remote",
                "degraded_capabilities": dict(sorted(degraded.items())),
                "remote_keyframes": {"selected": len(keyframes), "scenes": len(scenes)},
            }
        )
        embedding_revision = getattr(self._enrichers.get(EMBEDDINGS), "embedding_revision", None)
        if EMBEDDINGS in scene_results and embedding_revision:
            summary["embedding_revision"] = str(embedding_revision)
        return VideoManifest(
            duration_ms=duration_ms,
            sample_rate_fps=sample_rate_fps,
            frame_count=len(samples),
            provider=self.provider_name,
            model_versions=model_versions,
            frame_samples=samples,
            scenes=scenes,
            summary=summary,
        )

    # -- faces -----------------------------------------------------------------

    def _load_face_detector(self) -> object:
        return load_cpu_face_detector(
            model_path=self.face_model_path, model_sha256=self.face_model_sha256
        )

    def _faces_or_none(self, degraded: dict[str, str]) -> object | None:
        if self._face_detector is None and self._face_status is None:
            try:
                self._face_detector = self._face_detector_factory()
            except CpuVisionModelError:
                self._face_status = "unavailable:model_missing_or_mismatch"
            except ImportError:
                self._face_status = "unavailable:dependency_missing"
            except (OSError, RuntimeError) as exc:
                logger.warning(
                    "cpu_face_detector_unavailable", extra={"error_type": type(exc).__name__}
                )
                self._face_status = "unavailable:runtime_error"
        if self._face_status is not None:
            degraded["faces"] = self._face_status
        return self._face_detector

    # -- keyframes and enrichment -------------------------------------------------

    def _keyframes(self, *, cv2, samples, frames, scenes) -> list[Keyframe]:
        index_by_timestamp = {sample.timestamp_ms: i for i, sample in enumerate(samples)}
        ranked = sorted(
            scenes,
            key=lambda scene: (-scene.visual_importance_score, scene.start_ms),
        )[: self.max_keyframes]
        keyframes = []
        for scene in sorted(ranked, key=lambda item: item.start_ms):
            sample_index = index_by_timestamp[scene.representative_timestamp_ms]
            frame = frames[sample_index]
            ok, encoded = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
            )
            if not ok:
                continue
            keyframes.append(
                Keyframe(
                    scene_index=scene.index,
                    sample_index=sample_index,
                    timestamp_ms=scene.representative_timestamp_ms,
                    width=int(frame.shape[1]),
                    height=int(frame.shape[0]),
                    jpeg=encoded.tobytes(),
                )
            )
        return keyframes

    def _enrich(
        self, capability: str, keyframes: Sequence[Keyframe]
    ) -> tuple[list[object], str | None]:
        enricher = self._enrichers.get(capability)
        if enricher is None:
            return [], "unavailable:not_configured"
        if not keyframes:
            return [], None
        try:
            results = list(enricher.enrich(keyframes))
            if len(results) != len(keyframes):
                raise EnrichmentUnavailable("provider_malformed_response")
            results = [_validate_result(capability, result) for result in results]
        except EnrichmentUnavailable as exc:
            return [], f"unavailable:{exc.code}"
        except (ModelGatewayError, ProviderCallFailure) as exc:
            code = _failure_code(exc)
            logger.warning(
                "remote_visual_capability_degraded",
                extra={"capability": capability, "error_code": code},
            )
            return [], f"unavailable:{code}"
        except Exception as exc:  # noqa: BLE001 - one capability must not fail the manifest
            logger.error(
                "remote_visual_capability_failed",
                extra={"capability": capability, "error_type": type(exc).__name__},
            )
            return [], "unavailable:enrichment_failed"
        return results, None


def _validate_result(capability: str, result: object) -> object:
    if capability == OBJECTS:
        if not isinstance(result, list) or not all(
            isinstance(item, ObjectDetection) for item in result
        ):
            raise EnrichmentUnavailable("provider_malformed_response")
        return result[:24]
    if capability == CAPTIONS:
        if not isinstance(result, str):
            raise EnrichmentUnavailable("provider_malformed_response")
        return " ".join(result.split())[:_MAX_CAPTION_CHARS]
    if not isinstance(result, list | tuple) or not all(
        isinstance(value, int | float) and not isinstance(value, bool) for value in result
    ):
        raise EnrichmentUnavailable("provider_malformed_response")
    return [float(value) for value in result]


def _apply_scene_results(
    *,
    samples: list[FrameSample],
    scenes: list[SceneSegment],
    results: dict[str, dict[int, object]],
) -> list[FrameSample]:
    """Copy each scene's keyframe results to every frame of that scene."""
    if not results or not scenes:
        return samples
    updated: list[FrameSample] = []
    scene_position = 0
    for sample in samples:
        while (
            scene_position + 1 < len(scenes)
            and sample.timestamp_ms >= scenes[scene_position + 1].start_ms
        ):
            scene_position += 1
        scene_index = scenes[scene_position].index
        changes: dict[str, object] = {}
        if scene_index in results.get(OBJECTS, {}):
            changes["objects"] = results[OBJECTS][scene_index]
        if scene_index in results.get(CAPTIONS, {}):
            changes["caption"] = results[CAPTIONS][scene_index]
        if scene_index in results.get(EMBEDDINGS, {}):
            changes["embedding"] = results[EMBEDDINGS][scene_index]
        if not changes:
            updated.append(sample)
            continue
        enriched = sample.model_copy(update=changes)
        enriched = enriched.model_copy(
            update={
                "scene_type": _classify_scene_type(
                    caption=enriched.caption,
                    objects=enriched.objects,
                    faces=enriched.faces,
                    width=enriched.width,
                    height=enriched.height,
                ),
                "visual_importance_score": _importance_score(
                    objects=enriched.objects,
                    faces=enriched.faces,
                    scene_change_score=enriched.scene_change_score,
                    motion=enriched.camera_motion,
                ),
            }
        )
        updated.append(enriched)
    return updated


def _frame_sample(*, timestamp_ms, frame, scene_change_score, colors, faces, motion):
    height, width = int(frame.shape[0]), int(frame.shape[1])
    return FrameSample(
        timestamp_ms=timestamp_ms,
        width=width,
        height=height,
        scene_change_score=scene_change_score,
        dominant_colors=colors,
        faces=faces,
        camera_motion=motion,
        scene_type=_classify_scene_type(
            caption="", objects=[], faces=faces, width=width, height=height
        ),
        visual_importance_score=_importance_score(
            objects=[], faces=faces, scene_change_score=scene_change_score, motion=motion
        ),
    )


def _dominant_colors(*, cv2, np, rgb) -> list[DominantColor]:
    """NumPy port of the GPU path's 64x64, 32-level quantised colour histogram."""
    small = cv2.resize(rgb, (64, 64), interpolation=cv2.INTER_AREA).reshape(-1, 3)
    quantized = np.clip(np.round(small.astype(np.float32) / 32) * 32, 0, 255).astype(np.int32)
    colors, counts = np.unique(quantized, axis=0, return_counts=True)
    total = max(int(counts.sum()), 1)
    order = sorted(range(len(counts)), key=lambda i: (-int(counts[i]), tuple(colors[i])))[:6]
    return [
        DominantColor(
            hex="#{:02x}{:02x}{:02x}".format(*(int(value) for value in colors[i])),
            rgb=tuple(int(value) for value in colors[i]),
            percentage=round(int(counts[i]) / total, 3),
        )
        for i in order
    ]


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, ModelProvidersExhausted) and exc.failures:
        return str(exc.failures[0].get("code") or exc.code)
    if isinstance(exc, ModelGatewayError):
        return exc.code
    return classify_provider_exception(exc).code


def _precheck_quota(gateway: object, route_id: str, context: object, keyframes: int) -> None:
    """Refuse a capability up front rather than pay for some of its batches when
    the rest would not fit the plan's quota (each call is still checked too)."""
    check = getattr(gateway, "check_feature_quota", None)
    if check is not None:
        check(route_id=route_id, context=context, quantity=float(keyframes))


class GatewayObjectsEnricher:
    """Keyframe objects through the gateway's ``vision.object_detection`` route."""

    capability = OBJECTS

    def __init__(
        self,
        *,
        gateway: object,
        route_id: str,
        model_version: str,
        max_images_per_request: int = 16,
        max_request_bytes: int = 9 * 1024 * 1024,
        max_results: int = 24,
    ) -> None:
        self.gateway = gateway
        self.route_id = route_id
        self.model_version = model_version
        self.max_images_per_request = max_images_per_request
        self.max_request_bytes = max_request_bytes
        self.max_results = max_results

    def enrich(self, keyframes: Sequence[Keyframe]) -> list[list[ObjectDetection]]:
        context = inherited_call_context()
        _precheck_quota(self.gateway, self.route_id, context, len(keyframes))
        results: list[list[ObjectDetection]] = []
        for batch in _batches(
            keyframes,
            max_items=self.max_images_per_request,
            max_bytes=self.max_request_bytes,
        ):
            result = self.gateway.invoke(  # type: ignore[attr-defined]
                ObjectDetectionRequest(
                    images=tuple(keyframe.jpeg for keyframe in batch),
                    max_results=self.max_results,
                ),
                context=ModelCallContext(
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    required_route_id=self.route_id,
                ),
            )
            output = result.output
            if not isinstance(output, ObjectDetectionOutput) or len(output.detections) != len(
                batch
            ):
                raise EnrichmentUnavailable("provider_malformed_response")
            results.extend(
                [
                    ObjectDetection(
                        label=item.label,
                        confidence=item.confidence,
                        box=BoundingBox(x=item.x, y=item.y, width=item.width, height=item.height),
                    )
                    for item in detections
                ]
                for detections in output.detections
            )
        return results


class GatewayKeyframeEmbeddingsEnricher:
    """Keyframe image embeddings through the gateway's ``embeddings.image`` route.

    ``embedding_revision`` is the route's model revision. The manifest records it
    in ``summary.embedding_revision`` so B-roll ranking can tell whether the scene
    vectors share a space with the active embedding routes.
    """

    capability = EMBEDDINGS

    def __init__(
        self,
        *,
        gateway: object,
        route_id: str,
        model_version: str,
        embedding_revision: str,
        max_images_per_request: int = 16,
        max_request_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.gateway = gateway
        self.route_id = route_id
        self.model_version = model_version
        self.embedding_revision = embedding_revision
        self.max_images_per_request = max_images_per_request
        self.max_request_bytes = max_request_bytes

    def enrich(self, keyframes: Sequence[Keyframe]) -> list[list[float]]:
        context = inherited_call_context()
        _precheck_quota(self.gateway, self.route_id, context, len(keyframes))
        vectors: list[list[float]] = []
        for batch in _batches(
            keyframes,
            max_items=self.max_images_per_request,
            max_bytes=self.max_request_bytes,
        ):
            result = self.gateway.invoke(  # type: ignore[attr-defined]
                ImageEmbeddingsRequest(images=tuple(keyframe.jpeg for keyframe in batch)),
                context=ModelCallContext(
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    required_route_id=self.route_id,
                ),
            )
            output = result.output
            if not isinstance(output, EmbeddingsOutput) or len(output.vectors) != len(batch):
                raise EnrichmentUnavailable("provider_malformed_response")
            # Same precision as the local CLIP path.
            vectors.extend([round(float(v), 6) for v in vector] for vector in output.vectors)
        return vectors


_CAPTION_PROMPT = """You write short captions for video keyframes used to plan edits.

There are {count} images, in order; the first image has index 0. For each image
write one factual sentence of at most 24 words describing the main subject, the
setting, and any visible screen, device or on-screen text.

Do not name or identify people, and do not guess age, ethnicity, health or other
sensitive traits. Respond with JSON only."""


def _caption_response_format(count: int) -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "keyframe_captions",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["captions"],
                "properties": {
                    "captions": {
                        "type": "array",
                        "maxItems": count,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["index", "caption"],
                            "properties": {
                                "index": {"type": "integer", "minimum": 0, "maximum": count - 1},
                                "caption": {"type": "string", "maxLength": _MAX_CAPTION_CHARS},
                            },
                        },
                    }
                },
            },
        },
    }


class GatewayCaptionsEnricher:
    """Keyframe captions through the gateway's ``vision.prompt`` route."""

    capability = CAPTIONS

    def __init__(
        self,
        *,
        gateway: object,
        route_id: str,
        model_version: str,
        max_images_per_request: int = 8,
        output_units_per_image: int = 96,
    ) -> None:
        if max_images_per_request < 1:
            raise ValueError("max_images_per_request must be positive")
        self.gateway = gateway
        self.route_id = route_id
        self.model_version = model_version
        self.max_images_per_request = max_images_per_request
        self.output_units_per_image = output_units_per_image

    def enrich(self, keyframes: Sequence[Keyframe]) -> list[str]:
        context = inherited_call_context()
        _precheck_quota(self.gateway, self.route_id, context, len(keyframes))
        captions: list[str] = []
        for start in range(0, len(keyframes), self.max_images_per_request):
            batch = keyframes[start : start + self.max_images_per_request]
            result = self.gateway.invoke(  # type: ignore[attr-defined]
                VisionRequest(
                    prompt=_CAPTION_PROMPT.format(count=len(batch)),
                    image_urls=tuple(_jpeg_data_url(keyframe.jpeg) for keyframe in batch),
                    max_output_units=64 + self.output_units_per_image * len(batch),
                    response_format=_caption_response_format(len(batch)),
                ),
                context=ModelCallContext(
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    required_route_id=self.route_id,
                ),
            )
            if not isinstance(result.output, VisionOutput):
                raise EnrichmentUnavailable("provider_malformed_response")
            captions.extend(_parse_captions(result.output.content, count=len(batch)))
        return captions


def _jpeg_data_url(jpeg: bytes) -> str:
    return f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}"


def _parse_captions(content: str, *, count: int) -> list[str]:
    """Captions in image order; images the model skipped get an empty caption."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip())
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EnrichmentUnavailable("provider_malformed_response") from exc
    items = payload.get("captions") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise EnrichmentUnavailable("provider_malformed_response")
    captions: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        index, caption = item.get("index"), item.get("caption")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
            continue
        if not isinstance(caption, str) or not caption.strip() or index in captions:
            continue
        captions[index] = " ".join(caption.split())[:_MAX_CAPTION_CHARS]
    if not captions:
        raise EnrichmentUnavailable("provider_malformed_response")
    return [captions.get(index, "") for index in range(count)]


def _batches(
    keyframes: Sequence[Keyframe], *, max_items: int, max_bytes: int
) -> list[list[Keyframe]]:
    """Consecutive batches within an item cap and an encoded (base64) size cap."""
    batches: list[list[Keyframe]] = []
    current: list[Keyframe] = []
    current_bytes = 0
    for keyframe in keyframes:
        encoded = (len(keyframe.jpeg) + 2) // 3 * 4
        if current and (len(current) >= max_items or current_bytes + encoded > max_bytes):
            batches.append(current)
            current, current_bytes = [], 0
        current.append(keyframe)
        current_bytes += encoded
    if current:
        batches.append(current)
    return batches


def remote_visual_identity_from_parts(
    *, face_model_sha256: str, enricher_versions: dict[str, str]
) -> str:
    """Stable identity of the remote visual stack (part of the visual cache key)."""
    parts = [f"faces:{face_model_sha256[:12] or 'none'}"]
    parts += [
        f"{capability}:{enricher_versions.get(capability, 'off')}"
        for capability in REMOTE_CAPABILITIES
    ]
    return "remote|" + "|".join(parts)


def remote_visual_enricher_versions(settings: object) -> dict[str, str]:
    """Model revisions of the remote enrichers that settings turn on."""
    versions: dict[str, str] = {}
    if str(getattr(settings, "visual_objects_provider", "disabled")) == "google_vision":
        versions[OBJECTS] = (
            f"google_vision:{getattr(settings, 'google_vision_objects_model_revision', '')}"
        )
    if str(getattr(settings, "visual_captions_provider", "disabled")) == "openrouter":
        versions[CAPTIONS] = (
            f"openrouter:{getattr(settings, 'visual_caption_model_revision', '')}"
        )
    if str(getattr(settings, "visual_embeddings_provider", "disabled")) == "jina":
        versions[EMBEDDINGS] = (
            f"jina:{getattr(settings, 'jina_embeddings_model_revision', '')}"
            f":{getattr(settings, 'jina_embeddings_dimensions', '')}"
        )
    return versions


def remote_visual_identity(settings: object) -> str:
    return remote_visual_identity_from_parts(
        face_model_sha256=str(getattr(settings, "visual_cpu_face_model_sha256", "")).lower(),
        enricher_versions=remote_visual_enricher_versions(settings),
    )


def remote_visual_provider_from_settings(
    settings: object, *, gateway: object | None
) -> RemoteVisualIntelligenceProvider:
    versions = remote_visual_enricher_versions(settings)
    enrichers: list[KeyframeEnricher] = []
    if OBJECTS in versions:
        enrichers.append(
            _google_vision_objects_enricher(settings, gateway, model_version=versions[OBJECTS])
        )
    if CAPTIONS in versions:
        enrichers.append(
            _openrouter_captions_enricher(settings, gateway, model_version=versions[CAPTIONS])
        )
    if EMBEDDINGS in versions:
        enrichers.append(
            _jina_keyframe_embeddings_enricher(
                settings, gateway, model_version=versions[EMBEDDINGS]
            )
        )
    return RemoteVisualIntelligenceProvider(
        face_model_path=str(settings.visual_cpu_face_model_path),
        face_model_sha256=str(settings.visual_cpu_face_model_sha256),
        enrichers=tuple(enrichers),
        max_keyframes=int(getattr(settings, "visual_remote_max_keyframes", 24)),
        identity=remote_visual_identity(settings),
    )


def _google_vision_objects_enricher(
    settings: object, gateway: object | None, *, model_version: str
) -> GatewayObjectsEnricher:
    from app.services.google_vision import (
        GOOGLE_VISION_MAX_IMAGES_PER_REQUEST,
        GoogleVisionObjectsAdapter,
    )
    from app.services.model_domain_adapters import (
        GOOGLE_VISION_OBJECTS_ROUTE_ID,
        register_google_vision_objects_route,
    )

    if gateway is None:
        raise RuntimeError("VISUAL_OBJECTS_PROVIDER=google_vision requires the model gateway")
    api_key = getattr(settings, "google_vision_api_key", None)
    if not api_key:
        raise RuntimeError(
            "GOOGLE_VISION_API_KEY is required when VISUAL_PROVIDER=remote and "
            "VISUAL_OBJECTS_PROVIDER=google_vision"
        )
    route = next(
        (item for item in gateway.routes if item.route_id == GOOGLE_VISION_OBJECTS_ROUTE_ID),
        None,
    ) or register_google_vision_objects_route(
        gateway,
        adapter=GoogleVisionObjectsAdapter(
            api_key=str(api_key), model=str(settings.google_vision_objects_model)
        ),
        model=str(settings.google_vision_objects_model),
        revision=str(settings.google_vision_objects_model_revision),
        cost_per_image_usd=float(settings.google_vision_cost_per_image_usd),
    )
    return GatewayObjectsEnricher(
        gateway=gateway,
        route_id=route.route_id,
        model_version=model_version,
        max_images_per_request=GOOGLE_VISION_MAX_IMAGES_PER_REQUEST,
    )


def _openrouter_captions_enricher(
    settings: object, gateway: object | None, *, model_version: str
) -> GatewayCaptionsEnricher:
    from app.services.model_domain_adapters import (
        OPENROUTER_VISION_CAPTIONS_ROUTE_ID,
        register_openrouter_vision_captions_route,
    )

    if gateway is None:
        raise RuntimeError("VISUAL_CAPTIONS_PROVIDER=openrouter requires the model gateway")
    api_key = getattr(settings, "openrouter_api_key", None)
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is required when VISUAL_PROVIDER=remote and "
            "VISUAL_CAPTIONS_PROVIDER=openrouter"
        )
    route = next(
        (item for item in gateway.routes if item.route_id == OPENROUTER_VISION_CAPTIONS_ROUTE_ID),
        None,
    ) or register_openrouter_vision_captions_route(
        gateway,
        api_key=str(api_key),
        model=str(settings.visual_caption_model),
        revision=str(settings.visual_caption_model_revision),
        input_cost_per_million_usd=float(settings.visual_caption_input_cost_per_million_usd),
        output_cost_per_million_usd=float(settings.visual_caption_output_cost_per_million_usd),
    )
    return GatewayCaptionsEnricher(
        gateway=gateway,
        route_id=route.route_id,
        model_version=model_version,
        max_images_per_request=int(settings.visual_caption_max_images_per_request),
    )


def _jina_keyframe_embeddings_enricher(
    settings: object, gateway: object | None, *, model_version: str
) -> GatewayKeyframeEmbeddingsEnricher:
    from app.services.model_adapters import JinaEmbeddingsAdapter
    from app.services.model_domain_adapters import register_jina_embedding_routes

    if gateway is None:
        raise RuntimeError("VISUAL_EMBEDDINGS_PROVIDER=jina requires the model gateway")
    api_key = getattr(settings, "jina_api_key", None)
    if not api_key:
        raise RuntimeError(
            "JINA_API_KEY is required when VISUAL_PROVIDER=remote and "
            "VISUAL_EMBEDDINGS_PROVIDER=jina"
        )
    route = next(
        (item for item in gateway.routes if item.route_id == "jina-embeddings-image"),
        None,
    )
    if route is None:
        # The visual worker's gateway has no B-roll routes; register the same
        # Jina routes (same model, revision, dimensions) so vectors share a space.
        _text_route, route = register_jina_embedding_routes(
            gateway,
            adapter=JinaEmbeddingsAdapter(
                api_key=str(api_key),
                dimensions=int(settings.jina_embeddings_dimensions),
            ),
            model=str(settings.jina_embeddings_model),
            revision=str(settings.jina_embeddings_model_revision),
            text_cost_per_million_tokens_usd=float(
                settings.jina_text_cost_per_million_tokens_usd
            ),
            image_cost_per_image_usd=float(settings.jina_image_cost_per_image_usd),
        )
    return GatewayKeyframeEmbeddingsEnricher(
        gateway=gateway,
        route_id=route.route_id,
        model_version=model_version,
        embedding_revision=route.model_revision,
    )
