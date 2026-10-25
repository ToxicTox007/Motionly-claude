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
import logging
from collections.abc import Callable, Mapping, Sequence
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
from app.services.inference_strategies import (
    CAPTION_PROMPT,
    MAX_CAPTION_CHARS,
    CaptionResponseInvalid,
    caption_response_format,
    parse_captions,
)
from app.services.model_gateway import (
    EmbeddingsOutput,
    ImageEmbeddingsRequest,
    ModelCallContext,
    ModelGatewayError,
    ModelOperation,
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
_MAX_CAPTION_CHARS = MAX_CAPTION_CHARS


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


# The caption prompt, structured-output schema and parser are Motionly-owned and
# shared by every route that can serve ``vision.caption`` (plan Task 11); they
# live in :mod:`app.services.inference_strategies`.
_CAPTION_PROMPT = CAPTION_PROMPT
_caption_response_format = caption_response_format


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
    """Captions in image order; images the model skipped get an empty caption.

    Thin wrapper over the Motionly-owned parser that maps its
    :class:`CaptionResponseInvalid` onto the enricher's degrade signal.
    """
    try:
        return parse_captions(content, count=count)
    except CaptionResponseInvalid as exc:
        raise EnrichmentUnavailable("provider_malformed_response") from exc


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
    """Model revisions of the remote enrichers that settings turn on.

    Object detection is driven by the presence of a ``vision.object_detection``
    route (registry template or local), not a vendor switch, so its version is
    resolved from that route in :func:`_objects_enricher_from_settings`.
    """
    del settings
    return {}


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
    objects_enricher = _objects_enricher_from_settings(settings, gateway, versions)
    if objects_enricher is not None:
        enrichers.append(objects_enricher)
    # Captions and embeddings enrichers (plan Tasks 11 and 12) are active when the
    # gateway serves a route for their capability — a chat/vision route, a local
    # GPU route or a generic_http mapping — not by a vendor switch.
    captions_enricher = _captions_enricher_from_routes(gateway)
    if captions_enricher is not None:
        enrichers.append(captions_enricher)
    embeddings_enricher = _embeddings_enricher_from_routes(gateway)
    if embeddings_enricher is not None:
        enrichers.append(embeddings_enricher)
    return RemoteVisualIntelligenceProvider(
        face_model_path=str(settings.visual_cpu_face_model_path),
        face_model_sha256=str(settings.visual_cpu_face_model_sha256),
        enrichers=tuple(enrichers),
        max_keyframes=int(getattr(settings, "visual_remote_max_keyframes", 24)),
        identity=remote_visual_identity(settings),
    )


def _route_for_operation(gateway: object | None, operation: ModelOperation) -> object | None:
    """The gateway's highest-priority enabled route carrying ``operation``.

    Several routes may serve one capability (plan Section 2.11); user choice is
    added to the gateway by another task. Here we pick the highest-priority route
    (``gateway.routes`` is priority-ordered) purely to decide whether the
    capability is available and to seed the enricher's model_version.
    """
    if gateway is None:
        return None
    for route in getattr(gateway, "routes", ()):  # type: ignore[attr-defined]
        if operation in getattr(route, "operations", ()):
            return route
    return None


def captions_route(gateway: object | None) -> object | None:
    """The gateway's ``vision.caption`` (chat/vision) route, if any."""
    return _route_for_operation(gateway, ModelOperation.VISION_PROMPT)


def image_embeddings_route(gateway: object | None) -> object | None:
    """The gateway's ``embeddings.image`` route, if any."""
    return _route_for_operation(gateway, ModelOperation.EMBEDDINGS_IMAGE)


def _captions_enricher_from_routes(gateway: object | None) -> GatewayCaptionsEnricher | None:
    """Build the captions enricher from the ``vision.caption`` route, if present."""
    route = captions_route(gateway)
    if route is None:
        return None
    revision = getattr(route, "model_revision", "") or ""
    model_version = f"captions:{getattr(route, 'route_id', 'route')}:{revision}"
    max_batch = getattr(route, "max_batch", None)
    return GatewayCaptionsEnricher(
        gateway=gateway,
        route_id=route.route_id,
        model_version=model_version,
        max_images_per_request=int(max_batch) if max_batch else 8,
    )


def _embeddings_enricher_from_routes(
    gateway: object | None,
) -> GatewayKeyframeEmbeddingsEnricher | None:
    """Build the keyframe-embeddings enricher from the ``embeddings.image`` route.

    ``embedding_revision`` is the served route's model revision; it is recorded
    in ``summary.embedding_revision`` so B-roll ranking can tell whether the
    scene vectors share a space with the active embeddings routes (plan Task 12).
    """
    route = image_embeddings_route(gateway)
    if route is None:
        return None
    revision = getattr(route, "model_revision", "") or ""
    model_version = f"embeddings:{getattr(route, 'route_id', 'route')}:{revision}"
    max_batch = getattr(route, "max_batch", None)
    max_request_bytes = getattr(route, "max_request_bytes", None)
    return GatewayKeyframeEmbeddingsEnricher(
        gateway=gateway,
        route_id=route.route_id,
        model_version=model_version,
        embedding_revision=revision,
        max_images_per_request=int(max_batch) if max_batch else 16,
        max_request_bytes=int(max_request_bytes) if max_request_bytes else 8 * 1024 * 1024,
    )


def objects_route(gateway: object | None) -> object | None:
    """The gateway's enabled ``vision.object_detection`` route, if any.

    Objects enrichment is enabled by the *presence of a route* for the capability
    — a registry template route (Task 17) or the still-present vendor route — not
    by a vendor switch. Returns the first matching :class:`ModelRoute` or ``None``.
    """
    if gateway is None:
        return None
    for route in getattr(gateway, "routes", ()):  # type: ignore[attr-defined]
        if ModelOperation.VISION_OBJECT_DETECTION in getattr(route, "operations", ()):
            return route
    return None


def _objects_enricher_from_settings(
    settings: object, gateway: object | None, versions: Mapping[str, str]
) -> GatewayObjectsEnricher | None:
    """Select the objects enricher from the registry-driven route, if any.

    Objects enrichment is enabled by the presence of a
    ``vision.object_detection`` route (a registry template route from Task 17 or
    a local route), not by a vendor switch. Returns ``None`` when the gateway
    serves no such route.
    """
    del settings
    route = objects_route(gateway)
    if route is not None:
        model_version = versions.get(OBJECTS) or _route_objects_model_version(route)
        return _route_objects_enricher(gateway, route, model_version=model_version)
    return None


def _route_objects_model_version(route: object) -> str:
    """A stable objects model_version string from a route (no vendor switch)."""
    revision = getattr(route, "model_revision", "") or ""
    return f"objects:{getattr(route, 'route_id', 'route')}:{revision}"


def _route_objects_enricher(
    gateway: object | None, route: object, *, model_version: str
) -> GatewayObjectsEnricher:
    """Build the objects enricher with batch limits taken from the route.

    ``ModelRoute.max_batch`` / ``max_request_bytes`` drive batching; they fall
    back to today's 16 images / 9 MiB only when the route declares none.
    """
    max_batch = getattr(route, "max_batch", None)
    max_request_bytes = getattr(route, "max_request_bytes", None)
    return GatewayObjectsEnricher(
        gateway=gateway,
        route_id=route.route_id,
        model_version=model_version,
        max_images_per_request=int(max_batch) if max_batch else 16,
        max_request_bytes=int(max_request_bytes) if max_request_bytes else 9 * 1024 * 1024,
    )
