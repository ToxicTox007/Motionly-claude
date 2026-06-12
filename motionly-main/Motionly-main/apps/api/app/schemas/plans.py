from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

EDIT_PLAN_SCHEMA_VERSION = 1


class EditPlanStyle(StrEnum):
    LUXURY = "luxury"
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

    @model_validator(mode="after")
    def validate_range(self) -> "BrollRequest":
        if self.end_ms <= self.start_ms:
            raise ValueError("broll end_ms must be greater than start_ms")
        return self


class ZoomMoment(BaseModel):
    timestamp_ms: int = Field(ge=0)
    intensity: float = Field(ge=1.0, le=1.5)
    reason: str = Field(min_length=1, max_length=120)


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


class PlanCreateRequest(BaseModel):
    prompt: str = Field(default="", max_length=1200)
    style: EditPlanStyle = EditPlanStyle.LUXURY
    target_duration_s: int = Field(default=35, ge=10, le=90)
    broll_density: BrollDensity = BrollDensity.MEDIUM
    feedback: str | None = Field(default=None, max_length=800)
    force: bool = False


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
    plan: EditPlanResponse
    job_id: str
    status: Literal["ready"] = "ready"
    cached: bool = False


class PlanListResponse(BaseModel):
    plans: list[EditPlanResponse]
    active_plan_id: UUID | None = None


class PlanActivateResponse(BaseModel):
    plan: EditPlanResponse
    status: Literal["active"] = "active"


class PlanCutsUpdateRequest(BaseModel):
    cuts: list[EditPlanCut] = Field(min_length=1, max_length=24)


class PlanCutsUpdateResponse(BaseModel):
    plan: EditPlanResponse
    status: Literal["updated"] = "updated"


class PlanCaptionHighlightsUpdateRequest(BaseModel):
    caption_highlights: list[CaptionHighlight] = Field(default_factory=list, max_length=12)


class PlanCaptionHighlightsUpdateResponse(BaseModel):
    plan: EditPlanResponse
    status: Literal["updated"] = "updated"


class PlannerProviderResult(BaseModel):
    plan: EditPlan
    provider: str
    model: str
    latency_ms: int
    cost_estimate_usd: float | None = None
    validation_errors: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
