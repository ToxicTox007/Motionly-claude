from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.request_body import JsonStringBodyMixin


class RenderJobStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RENDERING = "rendering"
    UPLOADING = "uploading"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELED = "canceled"


class RenderOutputFormat(StrEnum):
    MP4 = "mp4"


class DurationPreset(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class PlatformPreset(StrEnum):
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    SHORTS = "shorts"
    YOUTUBE_LANDSCAPE = "youtube_landscape"
    INSTAGRAM_POST = "instagram_post"
    LINKEDIN = "linkedin"
    X_LANDSCAPE = "x_landscape"
    CUSTOM = "custom"


class ExportResolution(StrEnum):
    FREE_720P = "720p"
    PAID_1080P = "1080p"
    PRO_4K = "4k"


class OutputAspect(StrEnum):
    ORIGINAL = "original"
    VERTICAL_9_16 = "9:16"
    SQUARE_1_1 = "1:1"
    PORTRAIT_4_5 = "4:5"
    LANDSCAPE_16_9 = "16:9"


class ExportFitMode(StrEnum):
    FIT = "fit"
    FILL = "fill"
    CUSTOM = "custom"


class RenderAdjustmentAction(StrEnum):
    MAKE_PUNCHIER = "make_punchier"
    MORE_CINEMATIC = "more_cinematic"


class RenderTemplateId(StrEnum):
    LUXURY_REEL_V1 = "luxury_reel_v1"
    FOUNDER_REEL_V1 = "founder_reel_v1"
    TRAVEL_REEL_V1 = "travel_reel_v1"
    PODCAST_REEL_V1 = "podcast_reel_v1"
    FASHION_REEL_V1 = "fashion_reel_v1"
    TALKING_HEAD_REEL_V1 = "talking_head_reel_v1"
    PRODUCT_PROMO_REEL_V1 = "product_promo_reel_v1"


class RenderEngine(StrEnum):
    FFMPEG_BASIC = "ffmpeg_basic"
    REMOTION_PREMIUM = "remotion_premium"


class CutSegment(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=120)


class RenderSpecs(BaseModel):
    width: int = Field(default=1080, gt=0)
    height: int = Field(default=1920, gt=0)
    fps: int = Field(default=30, gt=0)
    video_codec: str = "h264"
    audio_codec: str = "aac"
    pixel_format: str = "yuv420p"
    format: RenderOutputFormat = RenderOutputFormat.MP4


class ExportCropSettings(BaseModel):
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)
    zoom: float = Field(default=1, ge=1, le=3)


class ExportFraming(BaseModel):
    output_aspect: OutputAspect = OutputAspect.VERTICAL_9_16
    fit_mode: ExportFitMode = ExportFitMode.FILL
    crop: ExportCropSettings = Field(default_factory=ExportCropSettings)
    padding_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")


class ExportOptions(BaseModel):
    """Platform-specific controls used by individual and bulk Repurpose renders."""

    resolution: ExportResolution = ExportResolution.PAID_1080P
    format: Literal["mp4"] = "mp4"
    platform: PlatformPreset = PlatformPreset.INSTAGRAM
    framing: ExportFraming = Field(default_factory=ExportFraming)
    duration_preset: DurationPreset | None = None
    include_captions: bool | None = None
    hook_text: str | None = Field(default=None, max_length=160)


class RenderCreateRequest(JsonStringBodyMixin, BaseModel):
    caption_track_id: UUID | None = None
    localized_publish_package_id: UUID | None = None
    include_captions: bool = True
    include_broll: bool = True
    edit_plan_id: UUID | None = None
    template_id: RenderTemplateId | None = None
    adjustment_action: RenderAdjustmentAction | None = None
    duration_preset: DurationPreset = DurationPreset.MEDIUM
    export_options: ExportOptions = Field(default_factory=ExportOptions)
    force: bool = False

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class RenderBulkCreateRequest(JsonStringBodyMixin, BaseModel):
    caption_track_id: UUID | None = None
    localized_publish_package_id: UUID | None = None
    include_captions: bool = True
    include_broll: bool = True
    edit_plan_id: UUID | None = None
    template_id: RenderTemplateId | None = None
    adjustment_action: RenderAdjustmentAction | None = None
    duration_preset: DurationPreset = DurationPreset.MEDIUM
    export_targets: list[ExportOptions] = Field(min_length=1, max_length=6)
    force: bool = False

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class RenderJobEventResponse(BaseModel):
    id: UUID
    render_job_id: UUID
    workspace_id: UUID
    project_id: UUID
    event_type: str
    from_status: RenderJobStatus | None = None
    to_status: RenderJobStatus
    progress_pct: int = Field(ge=0, le=100)
    message: str
    error_code: str | None = None
    latency_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RenderJobResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    uploaded_video_id: UUID
    caption_track_id: UUID | None = None
    workflow_run_id: UUID | None = None
    edit_plan_id: UUID | None = None
    source_plan_revision: int | None = Field(default=None, ge=1)
    timeline_revision_id: UUID | None = None
    source_media_revision: int | None = Field(default=None, ge=1)
    status: RenderJobStatus
    planner_version: str
    manifest: dict[str, Any]
    keep_segments: list[CutSegment] = Field(default_factory=list)
    specs: RenderSpecs = Field(default_factory=RenderSpecs)
    template_id: RenderTemplateId | None = None
    render_engine: RenderEngine | None = None
    renderer_version: str | None = None
    props_hash: str | None = None
    artifact_path: str | None = None
    benchmark_ms: int | None = None
    logs: list[str] = Field(default_factory=list)
    progress_pct: int = Field(ge=0, le=100)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    events: list[RenderJobEventResponse] = Field(default_factory=list)


class RenderCreateResponse(BaseModel):
    job: RenderJobResponse
    job_id: UUID
    queue_job_id: str
    status: RenderJobStatus


class RenderBulkCreateResponse(BaseModel):
    jobs: list[RenderJobResponse]
    job_ids: list[UUID]
    queue_job_ids: list[str]
    status: Literal["queued"] = "queued"


class RenderListResponse(BaseModel):
    jobs: list[RenderJobResponse]


class ExportResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    render_job_id: UUID
    workflow_run_id: UUID | None = None
    source_plan_revision: int | None = Field(default=None, ge=1)
    version: int
    storage_bucket: str
    storage_path: str
    format: RenderOutputFormat
    width: int
    height: int
    fps: int
    duration_ms: int | None = None
    file_size_bytes: int | None = None
    thumbnail_url: str | None = None
    settings_summary: dict[str, Any] = Field(default_factory=dict)
    restored_from_export_id: UUID | None = None
    created_at: datetime


class ExportListResponse(BaseModel):
    exports: list[ExportResponse]


class ExportDownloadUrlResponse(BaseModel):
    export_id: UUID
    signed_download_url: str
    expires_in_seconds: int
    format: Literal["mp4"] = "mp4"


class ExportRestoreResponse(BaseModel):
    export: ExportResponse
    restored_from_export_id: UUID
