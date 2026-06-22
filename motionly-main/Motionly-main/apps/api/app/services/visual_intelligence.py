import hashlib
import math
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.api.errors import ApiError
from app.repositories.visual import InMemoryVisualRepository, VideoManifestRecord
from app.schemas.uploads import UploadStatus, VideoMetadata
from app.schemas.visual import (
    BoundingBox,
    CameraMotion,
    DominantColor,
    FaceDetection,
    FrameSample,
    ObjectDetection,
    SceneSegment,
    SceneType,
    VideoManifest,
    VisualManifestStatus,
)
from app.services.local_storage import create_object_storage


class VisualIntelligenceProvider(Protocol):
    provider_name: str
    model: str

    def analyze(
        self,
        *,
        source_path: Path,
        metadata: VideoMetadata,
        sample_rate_fps: float,
        scene_threshold: float,
    ) -> VideoManifest:
        """Build a visual manifest from a source video."""


class FixtureVisualIntelligenceProvider:
    provider_name = "fixture_visual"
    model = "fixture-visual-v1"

    def analyze(
        self,
        *,
        source_path: Path,
        metadata: VideoMetadata,
        sample_rate_fps: float,
        scene_threshold: float,
    ) -> VideoManifest:
        del source_path, scene_threshold
        duration_ms = metadata.duration_ms or 6000
        width = metadata.width or 1080
        height = metadata.height or 1920
        timestamps = _fixture_timestamps(duration_ms=duration_ms, sample_rate_fps=sample_rate_fps)
        samples = [
            _fixture_frame_sample(
                index=index,
                timestamp_ms=timestamp_ms,
                width=width,
                height=height,
            )
            for index, timestamp_ms in enumerate(timestamps)
        ]
        scenes = _build_scenes_from_samples(samples=samples, duration_ms=duration_ms)
        return VideoManifest(
            duration_ms=duration_ms,
            sample_rate_fps=sample_rate_fps,
            frame_count=len(samples),
            provider=self.provider_name,
            model_versions={
                "clip": "fixture-hash-embedding",
                "blip": "fixture-captioner",
                "object_detection": "fixture-yolo",
                "face_detection": "fixture-mediapipe",
            },
            frame_samples=samples,
            scenes=scenes,
            summary=_manifest_summary(samples=samples, scenes=scenes),
        )


class WorkerOnlyVisualIntelligenceProvider:
    """Control-plane sentinel: production inference belongs to the ML worker image."""

    provider_name = "gpu_ml_worker"

    def __init__(self, *, model: str) -> None:
        self.model = model

    def analyze(
        self,
        *,
        source_path: Path,
        metadata: VideoMetadata,
        sample_rate_fps: float,
        scene_threshold: float,
    ) -> VideoManifest:
        del source_path, metadata, sample_rate_fps, scene_threshold
        raise ApiError(
            409,
            "visual_gpu_worker_required",
            "Visual inference runs only in the dedicated GPU/ML worker.",
        )


class LocalMLVisualIntelligenceProvider:
    provider_name = "local_ml_visual"

    def __init__(
        self,
        *,
        clip_model: str,
        blip_model: str,
        yolo_model: str,
    ) -> None:
        self.clip_model_name = clip_model
        self.blip_model_name = blip_model
        self.yolo_model_name = yolo_model
        self.model = f"{clip_model}|{blip_model}|{yolo_model}"
        self._loaded: dict[str, object] | None = None

    def analyze(
        self,
        *,
        source_path: Path,
        metadata: VideoMetadata,
        sample_rate_fps: float,
        scene_threshold: float,
    ) -> VideoManifest:
        models = self._load_models()
        cv2 = models["cv2"]
        np = models["np"]
        Image = models["Image"]
        torch = models["torch"]
        device = models["device"]
        clip_processor = models["clip_processor"]
        clip_model = models["clip_model"]
        blip_processor = models["blip_processor"]
        blip_model = models["blip_model"]
        yolo_model = models["yolo_model"]
        face_detector = models["face_detector"]

        raw_frames = _sample_video_frames(
            cv2=cv2,
            np=np,
            source_path=source_path,
            sample_rate_fps=sample_rate_fps,
            scene_threshold=scene_threshold,
        )
        if not raw_frames:
            raise ApiError(422, "visual_frames_empty", "No frames could be sampled from video.")

        samples: list[FrameSample] = []
        previous_gray = None
        for raw in raw_frames:
            timestamp_ms = raw["timestamp_ms"]
            frame = raw["frame"]
            scene_change_score = raw["scene_change_score"]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            motion = _camera_motion(cv2=cv2, np=np, previous_gray=previous_gray, gray=gray)
            previous_gray = gray
            embedding = _clip_embedding(
                torch=torch,
                device=device,
                processor=clip_processor,
                model=clip_model,
                image=image,
            )
            caption = _blip_caption(
                torch=torch,
                device=device,
                processor=blip_processor,
                model=blip_model,
                image=image,
            )
            objects = _yolo_objects(yolo_model=yolo_model, frame=frame)
            faces = _mediapipe_faces(face_detector=face_detector, rgb=rgb)
            colors = _dominant_colors(image=image)
            scene_type = _classify_scene_type(
                caption=caption,
                objects=objects,
                faces=faces,
                width=int(frame.shape[1]),
                height=int(frame.shape[0]),
            )
            samples.append(
                FrameSample(
                    timestamp_ms=timestamp_ms,
                    width=int(frame.shape[1]),
                    height=int(frame.shape[0]),
                    scene_change_score=scene_change_score,
                    embedding=embedding,
                    caption=caption,
                    dominant_colors=colors,
                    objects=objects,
                    faces=faces,
                    camera_motion=motion,
                    scene_type=scene_type,
                    visual_importance_score=_importance_score(
                        objects=objects,
                        faces=faces,
                        scene_change_score=scene_change_score,
                        motion=motion,
                    ),
                )
            )

        duration_ms = metadata.duration_ms or max(sample.timestamp_ms for sample in samples)
        scenes = _build_scenes_from_samples(samples=samples, duration_ms=duration_ms)
        return VideoManifest(
            duration_ms=duration_ms,
            sample_rate_fps=sample_rate_fps,
            frame_count=len(samples),
            provider=self.provider_name,
            model_versions={
                "clip": self.clip_model_name,
                "blip": self.blip_model_name,
                "object_detection": self.yolo_model_name,
                "face_detection": "mediapipe-face-detection",
            },
            frame_samples=samples,
            scenes=scenes,
            summary=_manifest_summary(samples=samples, scenes=scenes),
        )

    def _load_models(self) -> dict[str, object]:
        if self._loaded is not None:
            return self._loaded
        try:
            import cv2
            import mediapipe as mp
            import numpy as np
            import torch
            from PIL import Image
            from transformers import (
                Blip2ForConditionalGeneration,
                Blip2Processor,
                CLIPModel,
                CLIPProcessor,
            )
            from ultralytics import YOLO
        except ImportError as exc:
            raise ApiError(
                503,
                "visual_ml_dependency_missing",
                "Install the visual ML dependencies before using VISUAL_PROVIDER=local_ml.",
            ) from exc

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        clip_processor = CLIPProcessor.from_pretrained(self.clip_model_name)
        clip_model = CLIPModel.from_pretrained(self.clip_model_name).to(device)
        blip_processor = Blip2Processor.from_pretrained(self.blip_model_name)
        blip_model = Blip2ForConditionalGeneration.from_pretrained(
            self.blip_model_name,
            torch_dtype=dtype,
        ).to(device)
        yolo_model = YOLO(self.yolo_model_name)
        face_detector = mp.solutions.face_detection.FaceDetection(
            model_selection=1,
            min_detection_confidence=0.45,
        )
        self._loaded = {
            "cv2": cv2,
            "np": np,
            "torch": torch,
            "Image": Image,
            "device": device,
            "clip_processor": clip_processor,
            "clip_model": clip_model,
            "blip_processor": blip_processor,
            "blip_model": blip_model,
            "yolo_model": yolo_model,
            "face_detector": face_detector,
        }
        return self._loaded


def run_visual_analysis_for_upload(
    *,
    repository: InMemoryVisualRepository,
    provider: VisualIntelligenceProvider,
    workspace_id: UUID,
    project_id: UUID,
    video_id: UUID,
    media_work_dir: str,
    supabase_url: str | None,
    service_role_key: str | None,
    sample_rate_fps: float,
    scene_threshold: float,
) -> VideoManifestRecord:
    upload = repository.get_upload(
        project_id=project_id,
        video_id=video_id,
        workspace_id=workspace_id,
    )
    if upload.status != UploadStatus.READY:
        raise ApiError(
            409,
            "source_video_not_ready",
            "Finish upload preprocessing before running visual analysis.",
        )
    record = repository.latest_for_upload(
        uploaded_video_id=video_id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    if record is not None and record.status == VisualManifestStatus.READY and record.manifest:
        return record
    if record is None:
        record = repository.create_manifest(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=video_id,
            status=VisualManifestStatus.PROCESSING,
            provider=provider.provider_name,
            model=provider.model,
            manifest=None,
        )
    else:
        record = repository.transition_manifest(
            record=record,
            status=VisualManifestStatus.PROCESSING,
        )
    storage = create_object_storage(
        root=media_work_dir,
        supabase_url=supabase_url,
        service_role_key=service_role_key,
    )
    source_path = storage.ensure_object(bucket=upload.storage_bucket, path=upload.storage_path)
    try:
        manifest = provider.analyze(
            source_path=source_path,
            metadata=upload.metadata,
            sample_rate_fps=sample_rate_fps,
            scene_threshold=scene_threshold,
        )
    except ApiError as exc:
        repository.transition_manifest(
            record=record,
            status=VisualManifestStatus.FAILED,
            manifest=None,
            error_code=exc.code,
            error_message=exc.message,
        )
        raise
    except Exception as exc:
        repository.transition_manifest(
            record=record,
            status=VisualManifestStatus.FAILED,
            manifest=None,
            error_code="visual_analysis_failed",
            error_message="Visual analysis failed; planning will continue without visual context.",
        )
        raise ApiError(
            502,
            "visual_analysis_failed",
            "Visual analysis failed; planning will continue without visual context.",
        ) from exc
    return repository.transition_manifest(
        record=record,
        status=VisualManifestStatus.READY,
        manifest=manifest,
    )


def _fixture_timestamps(*, duration_ms: int, sample_rate_fps: float) -> list[int]:
    interval_ms = max(250, round(1000 / sample_rate_fps))
    timestamps = list(range(0, max(duration_ms, 1), interval_ms))
    return timestamps[: max(2, min(len(timestamps), 12))]


def _fixture_frame_sample(
    *,
    index: int,
    timestamp_ms: int,
    width: int,
    height: int,
) -> FrameSample:
    caption = "direct to camera founder explanation" if index % 2 == 0 else "product detail scene"
    objects = [
        ObjectDetection(
            label="person" if index % 2 == 0 else "product",
            confidence=0.82,
            box=BoundingBox(x=0.28, y=0.18, width=0.44, height=0.56),
        )
    ]
    faces = (
        [
            FaceDetection(
                confidence=0.76,
                box=BoundingBox(x=0.38, y=0.16, width=0.22, height=0.18),
            )
        ]
        if index % 2 == 0
        else []
    )
    scene_type = SceneType.TALKING_HEAD if faces else SceneType.PRODUCT
    return FrameSample(
        timestamp_ms=timestamp_ms,
        width=width,
        height=height,
        scene_change_score=0.72 if index in {0, 4, 8} else 0.12,
        embedding=_hash_embedding(f"{caption}:{timestamp_ms}", dimensions=32),
        caption=caption,
        dominant_colors=[
            DominantColor(hex="#111827", rgb=(17, 24, 39), percentage=0.52),
            DominantColor(hex="#f59e0b", rgb=(245, 158, 11), percentage=0.24),
        ],
        objects=objects,
        faces=faces,
        camera_motion=CameraMotion.STATIC if index % 3 else CameraMotion.PAN,
        scene_type=scene_type,
        visual_importance_score=0.8 if faces else 0.62,
    )


def _hash_embedding(seed: str, *, dimensions: int) -> list[float]:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    values = []
    while len(values) < dimensions:
        for byte in digest:
            values.append(round((byte / 255) * 2 - 1, 6))
            if len(values) == dimensions:
                break
        digest = hashlib.sha256(digest).digest()
    return values


def _sample_video_frames(
    *,
    cv2,
    np,
    source_path: Path,
    sample_rate_fps: float,
    scene_threshold: float,
) -> list[dict[str, object]]:
    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise ApiError(422, "visual_video_open_failed", "Video could not be opened for analysis.")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30
    frame_interval = max(1, int(round(fps / sample_rate_fps)))
    samples: list[dict[str, object]] = []
    previous_hist = None
    frame_index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        scene_score = 0.0
        hist = cv2.calcHist([frame], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        cv2.normalize(hist, hist)
        if previous_hist is not None:
            scene_score = float(1 - cv2.compareHist(previous_hist, hist, cv2.HISTCMP_CORREL))
        should_sample = frame_index % frame_interval == 0 or scene_score >= scene_threshold
        if should_sample:
            samples.append(
                {
                    "timestamp_ms": round((frame_index / fps) * 1000),
                    "frame": frame.copy(),
                    "scene_change_score": max(0.0, scene_score),
                }
            )
        previous_hist = hist
        frame_index += 1
        if len(samples) >= 240:
            break
    capture.release()
    return samples


def _clip_embedding(*, torch, device, processor, model, image) -> list[float]:
    inputs = processor(images=image, return_tensors="pt").to(device)
    with torch.no_grad():
        embedding = model.get_image_features(**inputs)
    embedding = embedding / embedding.norm(dim=-1, keepdim=True)
    return [round(float(value), 6) for value in embedding[0].detach().cpu().tolist()]


def _blip_caption(*, torch, device, processor, model, image) -> str:
    inputs = processor(images=image, return_tensors="pt").to(device)
    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens=24)
    return str(processor.decode(output[0], skip_special_tokens=True)).strip()


def _yolo_objects(*, yolo_model, frame) -> list[ObjectDetection]:
    results = yolo_model.predict(frame, verbose=False)
    if not results:
        return []
    height, width = frame.shape[:2]
    detections = []
    names = getattr(results[0], "names", {}) or {}
    for box in getattr(results[0], "boxes", [])[:24]:
        xyxy = box.xyxy[0].detach().cpu().tolist()
        confidence = float(box.conf[0].detach().cpu())
        class_id = int(box.cls[0].detach().cpu())
        x1, y1, x2, y2 = xyxy
        detections.append(
            ObjectDetection(
                label=str(names.get(class_id, class_id)),
                confidence=round(confidence, 3),
                box=BoundingBox(
                    x=max(0, min(1, x1 / width)),
                    y=max(0, min(1, y1 / height)),
                    width=max(0, min(1, (x2 - x1) / width)),
                    height=max(0, min(1, (y2 - y1) / height)),
                ),
            )
        )
    return detections


def _mediapipe_faces(*, face_detector, rgb) -> list[FaceDetection]:
    result = face_detector.process(rgb)
    detections = []
    height, width = rgb.shape[:2]
    for detection in list(result.detections or [])[:12]:
        box = detection.location_data.relative_bounding_box
        landmarks = {}
        for index, point in enumerate(detection.location_data.relative_keypoints or []):
            landmarks[f"point_{index}"] = (round(point.x, 4), round(point.y, 4))
        detections.append(
            FaceDetection(
                confidence=round(float(detection.score[0]), 3),
                box=BoundingBox(
                    x=max(0, min(1, box.xmin)),
                    y=max(0, min(1, box.ymin)),
                    width=max(0, min(1, box.width)),
                    height=max(0, min(1, box.height)),
                ),
                landmarks=landmarks,
            )
        )
    return detections


def _dominant_colors(*, image) -> list[DominantColor]:
    small = image.resize((64, 64)).convert("RGB")
    pixels = list(small.getdata())
    quantized = [(round(r / 32) * 32, round(g / 32) * 32, round(b / 32) * 32) for r, g, b in pixels]
    total = max(len(quantized), 1)
    colors = []
    for (r, g, b), count in sorted(
        _count_items(quantized).items(),
        key=lambda item: item[1],
        reverse=True,
    )[:6]:
        rgb = (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
        colors.append(
            DominantColor(
                hex=f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}",
                rgb=rgb,
                percentage=round(count / total, 3),
            )
        )
    return colors


def _count_items(items: list[tuple[int, int, int]]) -> dict[tuple[int, int, int], int]:
    counts: dict[tuple[int, int, int], int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return counts


def _camera_motion(*, cv2, np, previous_gray, gray) -> CameraMotion:
    if previous_gray is None:
        return CameraMotion.UNKNOWN
    flow = cv2.calcOpticalFlowFarneback(previous_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    mean_x = float(np.mean(flow[..., 0]))
    mean_y = float(np.mean(flow[..., 1]))
    magnitude = math.sqrt(mean_x * mean_x + mean_y * mean_y)
    if magnitude < 0.18:
        return CameraMotion.STATIC
    if abs(mean_x) > abs(mean_y) * 1.4:
        return CameraMotion.PAN
    if abs(mean_y) > abs(mean_x) * 1.4:
        return CameraMotion.TILT
    return CameraMotion.HANDHELD


def _classify_scene_type(
    *,
    caption: str,
    objects: list[ObjectDetection],
    faces: list[FaceDetection],
    width: int,
    height: int,
) -> SceneType:
    labels = {item.label.lower() for item in objects}
    caption_text = caption.lower()
    if faces:
        return SceneType.TALKING_HEAD
    if {"laptop", "keyboard", "tv", "monitor"} & labels or "screen" in caption_text:
        return SceneType.SCREEN_RECORDING
    if {"bottle", "cup", "handbag", "cell phone", "book"} & labels:
        return SceneType.PRODUCT
    if {"person"} & labels and len(objects) >= 3:
        return SceneType.CROWD
    if width > height and ("mountain" in caption_text or "beach" in caption_text):
        return SceneType.LANDSCAPE
    if "text" in caption_text or "sign" in caption_text:
        return SceneType.TEXT_HEAVY
    return SceneType.UNKNOWN


def _importance_score(
    *,
    objects: list[ObjectDetection],
    faces: list[FaceDetection],
    scene_change_score: float,
    motion: CameraMotion,
) -> float:
    object_score = min(0.28, len(objects) * 0.05)
    face_score = 0.24 if faces else 0
    scene_score = min(0.22, scene_change_score * 0.22)
    motion_score = (
        0.12 if motion in {CameraMotion.PAN, CameraMotion.TILT, CameraMotion.PUSH_IN} else 0
    )
    return round(min(1, 0.28 + object_score + face_score + scene_score + motion_score), 3)


def _build_scenes_from_samples(
    *,
    samples: list[FrameSample],
    duration_ms: int,
) -> list[SceneSegment]:
    if not samples:
        return []
    boundaries = [0]
    for index, sample in enumerate(samples[1:], start=1):
        if sample.scene_change_score >= 0.42:
            boundaries.append(index)
    boundaries.append(len(samples))
    scenes = []
    for scene_index, start_index in enumerate(boundaries[:-1]):
        end_index = boundaries[scene_index + 1]
        scene_samples = samples[start_index:end_index]
        representative = max(scene_samples, key=lambda sample: sample.visual_importance_score)
        start_ms = scene_samples[0].timestamp_ms
        end_ms = (
            samples[end_index].timestamp_ms
            if end_index < len(samples)
            else max(duration_ms, representative.timestamp_ms + 500)
        )
        scenes.append(
            SceneSegment(
                index=scene_index,
                start_ms=start_ms,
                end_ms=max(start_ms + 1, end_ms),
                representative_timestamp_ms=representative.timestamp_ms,
                caption=representative.caption,
                embedding=representative.embedding,
                dominant_colors=representative.dominant_colors,
                objects=representative.objects,
                faces=representative.faces,
                camera_motion=representative.camera_motion,
                scene_type=representative.scene_type,
                visual_importance_score=representative.visual_importance_score,
            )
        )
    return scenes


def _manifest_summary(
    *,
    samples: list[FrameSample],
    scenes: list[SceneSegment],
) -> dict[str, object]:
    scene_types: dict[str, int] = {}
    motions: dict[str, int] = {}
    object_labels: dict[str, int] = {}
    for sample in samples:
        scene_types[sample.scene_type.value] = scene_types.get(sample.scene_type.value, 0) + 1
        motions[sample.camera_motion.value] = motions.get(sample.camera_motion.value, 0) + 1
        for obj in sample.objects:
            object_labels[obj.label] = object_labels.get(obj.label, 0) + 1
    return {
        "scene_count": len(scenes),
        "sample_count": len(samples),
        "scene_types": scene_types,
        "camera_motion": motions,
        "top_objects": sorted(object_labels, key=object_labels.get, reverse=True)[:8],
        "face_sample_count": sum(1 for sample in samples if sample.faces),
    }
