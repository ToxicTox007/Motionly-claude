from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.captions import CaptionPresetKey
from app.schemas.plans import BrollDensity, EditPlanResponse
from app.schemas.request_body import JsonStringBodyMixin
from app.schemas.scale import ScaleResourceStatus


class CreativeVariantDimension(StrEnum):
    HOOK = "hook"
    CAPTION_STYLE = "caption_style"
    BROLL = "broll"
    PACING = "pacing"
    OPENING_FRAME = "opening_frame"


class CreativeVariantReviewStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    SELECTED = "selected"
    APPLIED = "applied"


class CreativeHookVariant(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=180)


class CreativeCaptionVariant(BaseModel):
    preset: CaptionPresetKey
    label: str = Field(min_length=1, max_length=80)
    rationale: str = Field(min_length=1, max_length=180)


class CreativeBrollVariant(BaseModel):
    density: BrollDensity
    direction: str = Field(min_length=1, max_length=180)
    shot_strategy: str = Field(min_length=1, max_length=180)


class CreativePacingVariant(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    cut_duration_multiplier: float = Field(ge=0.6, le=1.5)
    direction: str = Field(min_length=1, max_length=180)


class CreativeOpeningFrameVariant(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    start_ms: int = Field(ge=0)
    frame_direction: str = Field(min_length=1, max_length=180)
    thumbnail_direction: str = Field(min_length=1, max_length=180)


class CreativeVariant(BaseModel):
    id: str = Field(pattern=r"^variant-[a-z_]+-[1-6]$")
    ordinal: int = Field(ge=1, le=6)
    label: str = Field(min_length=1, max_length=120)
    primary_dimension: CreativeVariantDimension
    hook: CreativeHookVariant
    caption: CreativeCaptionVariant
    broll: CreativeBrollVariant
    pacing: CreativePacingVariant
    opening_frame: CreativeOpeningFrameVariant
    comparison_notes: list[str] = Field(min_length=2, max_length=4)


class CreativeVariantSetCreateRequest(JsonStringBodyMixin, BaseModel):
    name: str = Field(default="Creative test", min_length=1, max_length=120)
    dimensions: list[CreativeVariantDimension] = Field(
        default_factory=lambda: list(CreativeVariantDimension),
        min_length=1,
        max_length=5,
    )
    variant_count: int = Field(default=5, ge=1, le=6)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: object) -> object:
        return cls._coerce_json_string_body(data)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "CreativeVariantSetCreateRequest":
        self.dimensions = list(dict.fromkeys(self.dimensions))
        if self.variant_count < len(self.dimensions):
            raise ValueError("Variant count must cover every selected variation dimension.")
        return self


class CreativeVariantSelectionRequest(JsonStringBodyMixin, BaseModel):
    variant_id: str = Field(pattern=r"^variant-[a-z_]+-[1-6]$")
    expected_revision: int = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: object) -> object:
        return cls._coerce_json_string_body(data)


class CreativeVariantApplyRequest(JsonStringBodyMixin, BaseModel):
    """Apply a chosen variant to the active plan, creating a new plan version.

    ``expected_revision`` guards the *variant set* (so a concurrently edited set
    cannot be applied); ``expected_plan_revision`` guards the *active plan* and
    is enforced via the standard revision-conflict path. ``render`` optionally
    queues a render of the new plan version straight away.
    """

    variant_id: str = Field(pattern=r"^variant-[a-z_]+-[1-6]$")
    expected_revision: int = Field(ge=1)
    expected_plan_revision: int = Field(ge=1)
    render: bool = False

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: object) -> object:
        return cls._coerce_json_string_body(data)


class CreativeVariantSetPayload(BaseModel):
    schema_version: int = 1
    source_plan_id: UUID
    source_plan_revision: int = Field(ge=1)
    dimensions: list[CreativeVariantDimension] = Field(min_length=1, max_length=5)
    variants: list[CreativeVariant] = Field(min_length=1, max_length=6)
    selected_variant_id: str | None = None
    review_status: CreativeVariantReviewStatus = CreativeVariantReviewStatus.NEEDS_REVIEW
    review_only: bool = True
    applied_variant_id: str | None = None
    applied_plan_id: UUID | None = None
    applied_plan_revision: int | None = Field(default=None, ge=1)


class CreativeVariantSetResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    name: str = Field(min_length=1, max_length=120)
    status: ScaleResourceStatus
    revision: int = Field(ge=1)
    source_plan_id: UUID
    source_plan_revision: int = Field(ge=1)
    dimensions: list[CreativeVariantDimension] = Field(min_length=1, max_length=5)
    variants: list[CreativeVariant] = Field(min_length=1, max_length=6)
    selected_variant_id: str | None = None
    review_status: CreativeVariantReviewStatus
    review_only: bool = True
    applied_variant_id: str | None = None
    applied_plan_id: UUID | None = None
    applied_plan_revision: int | None = Field(default=None, ge=1)
    created_at: datetime
    updated_at: datetime


class CreativeVariantApplyResponse(BaseModel):
    """The variant set after applying plus the new plan version it produced."""

    variant_set: CreativeVariantSetResponse
    plan: EditPlanResponse
    plan_revision: int = Field(ge=1)
    applied_dimension: CreativeVariantDimension
    render_enqueued: bool = False
    render_job_id: UUID | None = None


class CreativeVariantSetListResponse(BaseModel):
    variant_sets: list[CreativeVariantSetResponse] = Field(default_factory=list)
