from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.request_body import JsonStringBodyMixin

EDIT_PLAN_SCHEMA_VERSION = 2


class EditPlanStyle(StrEnum):
    MOTIVATIONAL = "motivational"
    EMOTIONAL = "emotional"
    CINEMATIC = "cinematic"
    EDUCATIONAL = "educational"
    LUXURY = "luxury"
    STORYTELLING = "storytelling"
    DRAMATIC = "dramatic"
    FAST_PACED = "fast_paced"
    CLEAN_MINIMAL = "clean_minimal"
    CREATOR_INFLUENCER = "creator_influencer"
    # Legacy request styles remain accepted for older clients and records.
    FOUNDER = "founder"
    TRAVEL = "travel"
    FASHION = "fashion"
    PODCAST = "podcast"


class BrollDensity(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EditPlanStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"


class EditPlanCut(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_range(self) -> "EditPlanCut":
        if self.end_ms <= self.start_ms:
            raise ValueError("cut end_ms must be greater than start_ms")
        return self


class CaptionHighlight(BaseModel):
    timestamp_ms: int = Field(ge=0)
    word: str = Field(min_length=1, max_length=80)
    effect: Literal["pulse", "underline", "color_pop", "scale", "none"]


class BrollRequest(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    keywords: list[str] = Field(default_factory=list, min_length=1, max_length=5)
    priority: float = Field(ge=0, le=1)
    semantic_query: str | None = Field(default=None, max_length=180)
    semantic_embedding: list[float] = Field(default_factory=list, max_length=1024)
    source_scene_index: int | None = Field(default=None, ge=0)
    source_scene_type: str | None = Field(default=None, max_length=40)
    shot_type: str = Field(default="cutaway", max_length=40)
    placement_rule: str = Field(default="semantic_anchor", max_length=80)
    avoid_face_overlap: bool = True

    @model_validator(mode="after")
    def validate_range(self) -> "BrollRequest":
        if self.end_ms <= self.start_ms:
            raise ValueError("broll end_ms must be greater than start_ms")
        return self


class ZoomMoment(BaseModel):
    timestamp_ms: int = Field(ge=0)
    intensity: float = Field(ge=1.0, le=1.5)
    reason: str = Field(min_length=1, max_length=120)


class ManualElement(BaseModel):
    """A user-placed spatial or temporal editor element.

    The typed envelope stays stable while ``properties`` carries the controls
    specific to text, graphics, audio, transitions, grades, and AI actions.
    This keeps saved plans forward compatible as the palette grows.
    """

    id: str = Field(min_length=1, max_length=120)
    type: Literal[
        "text",
        "caption",
        "overlay",
        "shape",
        "graphic",
        "broll",
        "transition",
        "sfx",
        "music",
        "motion",
        "grade",
    ]
    name: str = Field(min_length=1, max_length=120)
    zone: Literal["canvas", "timeline", "clip"]
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(default=3000, ge=1)
    x_pct: float = Field(default=50, ge=0, le=100)
    y_pct: float = Field(default=50, ge=0, le=100)
    width_pct: float = Field(default=40, ge=1, le=100)
    height_pct: float = Field(default=12, ge=1, le=100)
    rotation_deg: float = Field(default=0, ge=-360, le=360)
    opacity: float = Field(default=1, ge=0, le=1)
    clip_id: str | None = Field(default=None, max_length=120)
    properties: dict[str, Any] = Field(default_factory=dict)
    ai_reason: str | None = Field(default=None, max_length=240)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_range(self) -> "ManualElement":
        if self.end_ms <= self.start_ms:
            raise ValueError("manual element end_ms must be greater than start_ms")
        return self


class TimelineSourceClip(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    source_video_id: UUID | None = None
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    output_start_ms: int = Field(ge=0)
    output_end_ms: int = Field(gt=0)
    reason: str = Field(default="timeline clip", min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_ranges(self) -> "TimelineSourceClip":
        if self.source_end_ms <= self.source_start_ms:
            raise ValueError("source clip end must be after its start")
        if self.output_end_ms <= self.output_start_ms:
            raise ValueError("output clip end must be after its start")
        if self.source_end_ms - self.source_start_ms != self.output_end_ms - self.output_start_ms:
            raise ValueError("source and output clip durations must match")
        return self


class TimelineCaptionLayer(BaseModel):
    id: str
    index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str
    words: list[dict[str, Any]] = Field(default_factory=list)
    line_count: int = Field(default=1, ge=1)
    highlight_words: list[str] = Field(default_factory=list)
    style: dict[str, Any] = Field(default_factory=dict)


class TimelineBrollLayer(BaseModel):
    id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    src: str = ""
    source_start_ms: int | None = None
    source_end_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimelineAudioLayer(BaseModel):
    source_stems: dict[str, Any] | None = None
    music: dict[str, Any] | None = None
    sfx: list[dict[str, Any]] = Field(default_factory=list)


class TimelineEffectsLayer(BaseModel):
    color_grade: str = "clean_founder"
    effects: list[str] = Field(default_factory=list)
    zoom_moments: list[ZoomMoment] = Field(default_factory=list)
    keyframes: list[dict[str, Any]] = Field(default_factory=list)
    config: dict[str, Any] | None = None


class TimelineDocument(BaseModel):
    schema_version: Literal[1] = 1
    duration_ms: int = Field(ge=1)
    clips: list[TimelineSourceClip] = Field(default_factory=list, min_length=1, max_length=200)
    captions: list[TimelineCaptionLayer] = Field(default_factory=list, max_length=1000)
    broll: list[TimelineBrollLayer] = Field(default_factory=list, max_length=200)
    audio: TimelineAudioLayer = Field(default_factory=TimelineAudioLayer)
    manual_layers: list[ManualElement] = Field(default_factory=list, max_length=200)
    transitions: list[ManualElement] = Field(default_factory=list, max_length=100)
    effects: TimelineEffectsLayer = Field(default_factory=TimelineEffectsLayer)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EditPlan(BaseModel):
    schema_version: int = EDIT_PLAN_SCHEMA_VERSION
    style: EditPlanStyle
    target_duration_s: int = Field(ge=10, le=90)
    cuts: list[EditPlanCut] = Field(default_factory=list, min_length=1, max_length=24)
    caption_highlights: list[CaptionHighlight] = Field(default_factory=list, max_length=12)
    broll_requests: list[BrollRequest] = Field(default_factory=list, max_length=12)
    zoom_moments: list[ZoomMoment] = Field(default_factory=list, max_length=12)
    music_mood: str = Field(min_length=1, max_length=160)
    effects: list[str] = Field(default_factory=list, max_length=8)
    color_grade: str = Field(min_length=1, max_length=80)
    risk_flags: list[str] = Field(default_factory=list, max_length=10)
    manual_elements: list[ManualElement] = Field(default_factory=list, max_length=200)
    timeline: TimelineDocument | None = None


class PlanCreateRequest(JsonStringBodyMixin, BaseModel):
    prompt: str = Field(default="", max_length=1200)
    style: EditPlanStyle | None = None
    target_duration_s: int = Field(default=35, ge=10, le=90)
    broll_density: BrollDensity = BrollDensity.MEDIUM
    feedback: str | None = Field(default=None, max_length=800)
    force: bool = False
    style_profile_id: UUID | None = None
    saved_template_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class StyleClassification(BaseModel):
    predicted_style: EditPlanStyle
    effective_style: EditPlanStyle
    requested_style: EditPlanStyle | None = None
    confidence: float = Field(ge=0, le=1)
    scores: dict[str, float] = Field(default_factory=dict)
    signals: list[str] = Field(default_factory=list, max_length=12)
    source: Literal["audio_visual_classifier"] = "audio_visual_classifier"


class PlanSummary(BaseModel):
    duration_s: int
    cut_count: int
    broll_count: int
    zoom_count: int
    music_mood: str
    effects: list[str]
    color_grade: str
    risk_flags: list[str] = Field(default_factory=list)
    source_topic: str = "Transcript-driven edit"
    direction_notes: list[str] = Field(default_factory=list, max_length=6)
    style_classification: StyleClassification | None = None


class EditPlanResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    transcript_id: UUID
    version: int
    schema_version: int
    status: EditPlanStatus
    is_active: bool
    style: EditPlanStyle
    target_duration_s: int
    broll_density: BrollDensity
    prompt: str
    feedback: str | None = None
    provider: str
    model: str
    cache_key: str
    schema_hash: str
    latency_ms: int | None = None
    cost_estimate_usd: float | None = None
    validation_errors: list[str] = Field(default_factory=list)
    summary: PlanSummary
    plan: EditPlan
    created_at: datetime
    updated_at: datetime


class PlanCreateResponse(BaseModel):
    plan: EditPlanResponse | None = None
    job_id: str
    status: str = "ready"
    cached: bool = False
    project_id: UUID | None = None
    status_url: str | None = None


class PlanJobStatusResponse(BaseModel):
    status: str
    progress: int
    plan_id: UUID | None = None
    failure_reason: str | None = None


class PlanListResponse(BaseModel):
    plans: list[EditPlanResponse]
    active_plan_id: UUID | None = None


class PlanActivateResponse(BaseModel):
    plan: EditPlanResponse
    status: Literal["active"] = "active"


class PlanCutsUpdateRequest(JsonStringBodyMixin, BaseModel):
    cuts: list[EditPlanCut] = Field(min_length=1, max_length=24)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class PlanCutsUpdateResponse(BaseModel):
    plan: EditPlanResponse
    status: Literal["updated"] = "updated"


class PlanCaptionHighlightsUpdateRequest(JsonStringBodyMixin, BaseModel):
    caption_highlights: list[CaptionHighlight] = Field(default_factory=list, max_length=12)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class PlanCaptionHighlightsUpdateResponse(BaseModel):
    plan: EditPlanResponse
    status: Literal["updated"] = "updated"


class PlanManualElementsUpdateRequest(JsonStringBodyMixin, BaseModel):
    manual_elements: list[ManualElement] = Field(default_factory=list, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class PlanManualElementsUpdateResponse(BaseModel):
    plan: EditPlanResponse


class TimelineUpdateRequest(JsonStringBodyMixin, BaseModel):
    timeline: TimelineDocument

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class TimelineUpdateResponse(BaseModel):
    plan: EditPlanResponse
    status: Literal["updated"] = "updated"


class PlanSnapshotCreateRequest(JsonStringBodyMixin, BaseModel):
    plan_id: UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class PlanSnapshotResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    source_plan_id: UUID
    source_plan_version: int
    name: str
    notes: str | None = None
    created_by_user_id: UUID
    plan: EditPlan
    summary: PlanSummary
    created_at: datetime


class PlanSnapshotCreateResponse(BaseModel):
    snapshot: PlanSnapshotResponse
    status: Literal["created"] = "created"


class PlanSnapshotListResponse(BaseModel):
    snapshots: list[PlanSnapshotResponse]


class PlanSnapshotRestoreResponse(BaseModel):
    plan: EditPlanResponse
    snapshot_id: UUID
    status: Literal["restored"] = "restored"


class PlannerProviderResult(BaseModel):
    plan: EditPlan
    provider: str
    model: str
    latency_ms: int
    cost_estimate_usd: float | None = None
    validation_errors: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
