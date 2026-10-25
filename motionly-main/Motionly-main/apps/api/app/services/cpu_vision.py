"""CPU MediaPipe Tasks models for the GPU-free cpu-vision worker image.

The locked MediaPipe release (0.10.35) only ships the Tasks API, so CPU matting
and face detection use ``mediapipe.tasks.python.vision`` with checksum-pinned
``.tflite`` models that the cpu-vision image bakes into ``/opt/motionly/models``.

``TasksSelfieSegmentation`` exposes the legacy ``SelfieSegmentation.process``
contract, so ``generate_selfie_segmentation_matte`` runs unchanged on CPU.
"""

from __future__ import annotations

import atexit
import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any


@dataclass(frozen=True)
class PinnedModel:
    name: str
    url: str
    sha256: str
    default_path: str

    @property
    def revision(self) -> str:
        return f"sha256:{self.sha256}"


# The landscape selfie segmenter is the model the legacy API selected with
# model_selection=1 (the setting the GPU matte path uses).
SELFIE_SEGMENTER_LANDSCAPE = PinnedModel(
    name="mediapipe-selfie-segmenter-landscape",
    url=(
        "https://storage.googleapis.com/mediapipe-models/image_segmenter/"
        "selfie_segmenter_landscape/float16/1/selfie_segmenter_landscape.tflite"
    ),
    sha256="490e9ea734313e0de10fa0cd9e3c6133e36ea4db2b7a49bde9ef019f72796b8e",
    default_path="/opt/motionly/models/selfie_segmenter_landscape.tflite",
)
# Full-range BlazeFace matches the legacy face_detection model_selection=1.
BLAZE_FACE_FULL_RANGE = PinnedModel(
    name="mediapipe-blaze-face-full-range",
    url=(
        "https://storage.googleapis.com/mediapipe-models/face_detector/"
        "blaze_face_full_range/float16/1/blaze_face_full_range.tflite"
    ),
    sha256="3698b18f063835bc609069ef052228fbe86d9c9a6dc8dcb7c7c2d69aed2b181b",
    default_path="/opt/motionly/models/blaze_face_full_range.tflite",
)


class CpuVisionModelError(RuntimeError):
    """A pinned CPU vision model is missing or does not match its checksum."""


def verify_model_file(path: str | Path, sha256: str) -> Path:
    model = Path(path)
    if not model.is_file():
        raise CpuVisionModelError(f"CPU vision model file is missing: {model}")
    digest = hashlib.sha256()
    with model.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha256.strip().lower():
        raise CpuVisionModelError(f"CPU vision model checksum mismatch: {model}")
    return model


class TasksSelfieSegmentation:
    """Legacy ``SelfieSegmentation.process(rgb)`` on the MediaPipe Tasks API."""

    def __init__(self, *, segmenter: Any, mp_module: Any, np_module: Any) -> None:
        self._segmenter = segmenter
        self._mp = mp_module
        self._np = np_module

    def process(self, rgb: Any) -> SimpleNamespace:
        np = self._np
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(rgb),
        )
        masks = self._segmenter.segment(image).confidence_masks or []
        if not masks:
            return SimpleNamespace(segmentation_mask=None)
        mask = masks[0].numpy_view()
        if mask.ndim == 3:
            mask = mask[..., 0]
        # Copy: the view belongs to the native result buffer.
        return SimpleNamespace(segmentation_mask=np.array(mask, dtype=np.float32))

    def close(self) -> None:
        close = getattr(self._segmenter, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - best effort during shutdown
                pass


def load_cpu_matte_models(*, model_path: str, model_sha256: str) -> dict[str, object]:
    """cv2, NumPy and a CPU selfie segmenter, in the shape the matte adapter expects."""
    path = verify_model_file(model_path, model_sha256)
    import cv2
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core import base_options

    segmenter = vision.ImageSegmenter.create_from_options(
        vision.ImageSegmenterOptions(
            base_options=base_options.BaseOptions(model_asset_path=str(path)),
            running_mode=vision.RunningMode.IMAGE,
            output_confidence_masks=True,
            output_category_mask=False,
        )
    )
    selfie_segmentation = TasksSelfieSegmentation(
        segmenter=segmenter, mp_module=mp, np_module=np
    )
    # Close while the native library is still loaded (avoids noisy __del__ errors).
    atexit.register(selfie_segmentation.close)
    return {"cv2": cv2, "np": np, "selfie_segmentation": selfie_segmentation}


class TasksFaceDetector:
    """BlazeFace on CPU (MediaPipe Tasks), returning manifest ``FaceDetection`` items.

    Output matches the GPU path's ``_mediapipe_faces``: boxes and keypoints are
    normalised to [0, 1], at most 12 faces, confidence rounded to 3 places.
    """

    max_faces = 12

    def __init__(self, *, detector: Any, mp_module: Any, np_module: Any) -> None:
        self._detector = detector
        self._mp = mp_module
        self._np = np_module

    def detect(self, rgb: Any) -> list:
        from app.schemas.visual import BoundingBox, FaceDetection

        height, width = rgb.shape[:2]
        if not width or not height:
            return []
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=self._np.ascontiguousarray(rgb),
        )
        detections = list(self._detector.detect(image).detections or [])
        faces = []
        for detection in detections[: self.max_faces]:
            box = detection.bounding_box
            score = float(detection.categories[0].score) if detection.categories else 0.0
            faces.append(
                FaceDetection(
                    confidence=round(_clamp(score), 3),
                    box=BoundingBox(
                        x=_clamp(box.origin_x / width),
                        y=_clamp(box.origin_y / height),
                        width=_clamp(box.width / width),
                        height=_clamp(box.height / height),
                    ),
                    landmarks={
                        f"point_{index}": (round(float(point.x), 4), round(float(point.y), 4))
                        for index, point in enumerate(detection.keypoints or [])
                    },
                )
            )
        return faces

    def close(self) -> None:
        close = getattr(self._detector, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - best effort during shutdown
                pass


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def load_cpu_face_detector(
    *,
    model_path: str,
    model_sha256: str,
    min_detection_confidence: float = 0.45,
) -> TasksFaceDetector:
    """Full-range BlazeFace, the model the GPU path selects (model_selection=1)."""
    path = verify_model_file(model_path, model_sha256)
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core import base_options

    detector = vision.FaceDetector.create_from_options(
        vision.FaceDetectorOptions(
            base_options=base_options.BaseOptions(model_asset_path=str(path)),
            running_mode=vision.RunningMode.IMAGE,
            min_detection_confidence=min_detection_confidence,
        )
    )
    face_detector = TasksFaceDetector(detector=detector, mp_module=mp, np_module=np)
    atexit.register(face_detector.close)
    return face_detector
