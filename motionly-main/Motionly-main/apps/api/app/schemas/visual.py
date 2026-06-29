from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.request_body import JsonStringBodyMixin

VISUAL_MANIFEST_SCHEMA_VERSION = 1


class VisualManifestStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class MatteStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CameraMotion(StrEnum):
    STATIC = "static"
    PAN = "pan"
    TILT = "tilt"
    PUSH_IN = "push_in"
    HANDHELD = "handheld"
    UNKNOWN = "unknown"


class SceneType(StrEnum):
    TALKING_HEAD = "talking_head"
    SCREEN_RECORDING = "screen_recording"
    PRODUCT = "product"
    LANDSCAPE = "landscape"
    CROWD = "crowd"
    TEXT_HEAVY = "text_heavy"
    ABSTRACT = "abstract"
    UNKNOWN = "unknown"


class BoundingBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)
    height: float = Field(ge=0, le=1)


class DominantColor(BaseModel):
    hex: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    rgb: tuple[int, int, int]
    percentage: float = Field(ge=0, le=1)


class ObjectDetection(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0, le=1)
    box: BoundingBox


class FaceDetection(BaseModel):
    confidence: float = Field(ge=0, le=1)
    box: BoundingBox
    landmarks: dict[str, tuple[float, float]] = Field(default_factory=dict)


class FrameSample(BaseModel):
    timestamp_ms: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    scene_change_score: float = Field(default=0, ge=0)
    embedding: list[float] = Field(default_factory=list)
    caption: str = Field(default="", max_length=240)
    dominant_colors: list[DominantColor] = Field(default_factory=list, max_length=6)
    objects: list[ObjectDetection] = Field(default_factory=list, max_length=24)
    faces: list[FaceDetection] = Field(default_factory=list, max_length=12)
    camera_motion: CameraMotion = CameraMotion.UNKNOWN
    scene_type: SceneType = SceneType.UNKNOWN
    visual_importance_score: float = Field(default=0, ge=0, le=1)


class SegmentationMatte(BaseModel):
    bucket: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=512)
    model: str = Field(default="mediapipe-selfie-segmentation", max_length=120)
    fps: float = Field(gt=0, le=120)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    cache_key: str | None = Field(default=None, max_length=160)
    status: MatteStatus = MatteStatus.COMPLETED
    failure_reason: str | None = Field(default=None, max_length=120)


class SceneSegment(BaseModel):
    index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    representative_timestamp_ms: int = Field(ge=0)
    caption: str = Field(default="", max_length=240)
    embedding: list[float] = Field(default_factory=list)
    dominant_colors: list[DominantColor] = Field(default_factory=list, max_length=6)
    objects: list[ObjectDetection] = Field(default_factory=list, max_length=24)
    faces: list[FaceDetection] = Field(default_factory=list, max_length=12)
    camera_motion: CameraMotion = CameraMotion.UNKNOWN
    scene_type: SceneType = SceneType.UNKNOWN
    visual_importance_score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_range(self) -> "SceneSegment":
        if self.end_ms <= self.start_ms:
            raise ValueError("scene segment end_ms must be greater than start_ms")
        return self


class VideoManifest(BaseModel):
    schema_version: int = VISUAL_MANIFEST_SCHEMA_VERSION
    duration_ms: int | None = Field(default=None, ge=0)
    sample_rate_fps: float = Field(default=2.0, gt=0, le=10)
    frame_count: int = Field(default=0, ge=0)
    provider: str
    model_versions: dict[str, str] = Field(default_factory=dict)
    frame_samples: list[FrameSample] = Field(default_factory=list)
    scenes: list[SceneSegment] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    segmentation_matte: SegmentationMatte | None = None


class VisualAnalyzeRequest(JsonStringBodyMixin, BaseModel):
    force: bool = False
    sample_rate_fps: float | None = Field(default=None, gt=0, le=10)
    scene_threshold: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class VideoManifestResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    uploaded_video_id: UUID
    version: int
    schema_version: int
    status: VisualManifestStatus
    provider: str
    model: str
    manifest: VideoManifest | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class VisualAnalyzeResponse(BaseModel):
    manifest: VideoManifestResponse
    job_id: str
    status: Literal["ready", "queued"] = "ready"
    cached: bool = False


class VisualStatusResponse(BaseModel):
    status: Literal["processing", "ready", "unavailable"]
    manifest: VideoManifestResponse | None = None
    message: str
