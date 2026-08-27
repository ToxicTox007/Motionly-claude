from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.request_body import JsonStringBodyMixin


class LanguageHint(StrEnum):
    AUTO = "auto"
    ENGLISH = "en"
    HINDI = "hi"
    SPANISH = "es"
    PORTUGUESE = "pt"
    FRENCH = "fr"
    GERMAN = "de"
    ARABIC = "ar"


class TranscriptStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    CAPTIONS_UNAVAILABLE = "captions_unavailable"


class TranscriptQualityStatus(StrEnum):
    OK = "ok"
    LOW_CONFIDENCE = "low_confidence"
    EMPTY = "empty"
    AUDIO_FAILED = "audio_failed"
    PARTIAL = "partial"


class CaptionPresetKey(StrEnum):
    LUXURY_MINIMAL = "luxury_minimal"
    BOLD_INFLUENCER = "bold_influencer"
    VOGUE_WHITE = "vogue_white"
    GOLD_PREMIUM = "gold_premium"
    FOUNDER_CLEAN = "founder_clean"
    IMPACT_STAMP = "impact_stamp"
    MINIMAL_LINE = "minimal_line"
    EDUCATIONAL_CARD = "educational_card"
    CREATOR_TICKER = "creator_ticker"


class CaptionMode(StrEnum):
    WORD_POP = "word_pop"
    IMPACT_STAMP = "impact_stamp"
    MINIMAL_LINE = "minimal_line"
    EDUCATIONAL_CARD = "educational_card"
    CREATOR_TICKER = "creator_ticker"


class CaptionAnimation(StrEnum):
    FADE_UP = "fade_up"
    POP = "pop"
    TYPE_ON = "type_on"
    GLOW = "glow"
    SLIDE_UP = "slide_up"
    IMPACT_STAMP = "impact_stamp"
    MINIMAL_FADE = "minimal_fade"
    EDU_STEP = "edu_step"
    TICKER = "ticker"


class TranscriptWord(BaseModel):
    id: UUID | None = None
    index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=120)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)


class TranscriptReadResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    uploaded_video_id: UUID
    status: TranscriptStatus
    language: str = Field(default="unknown", min_length=2, max_length=24)
    language_hint: LanguageHint = LanguageHint.AUTO
    provider: str
    model: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    quality_status: TranscriptQualityStatus = TranscriptQualityStatus.OK
    quality_reasons: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    progress_pct: int = Field(default=0, ge=0, le=100)
    words: list[TranscriptWord] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TranscriptionCreateRequest(JsonStringBodyMixin, BaseModel):
    video_id: UUID | None = None
    language_hint: LanguageHint = LanguageHint.AUTO
    force: bool = False

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class TranscriptionCreateResponse(BaseModel):
    transcript_id: UUID | None = None
    status: TranscriptStatus
    job_id: str
    message: str


class CaptionStyle(BaseModel):
    mode: CaptionMode = CaptionMode.WORD_POP
    font_family: str
    font_size_px: int = Field(gt=0)
    font_weight: int = Field(ge=100, le=900)
    text_case: Literal["normal", "uppercase", "title"]
    text_color: str
    accent_color: str
    background_color: str | None = None
    background_opacity: float = Field(default=0, ge=0, le=1)
    border_color: str | None = None
    border_radius_px: int = Field(default=0, ge=0, le=999)
    padding_x_px: int = Field(default=12, ge=0, le=96)
    padding_y_px: int = Field(default=6, ge=0, le=64)
    letter_spacing_px: float = Field(default=0, ge=-2, le=12)
    stroke_color: str | None = None
    stroke_width_px: int = Field(default=0, ge=0)
    shadow: str | None = None
    safe_area_bottom_pct: int = Field(ge=0, le=50)
    position_x_pct: int | None = Field(default=None, ge=0, le=100)
    position_y_pct: int | None = Field(default=None, ge=0, le=100)
    placement: Literal["lower", "middle", "upper"]
    animation: CaptionAnimation
    max_lines: int = Field(default=2, ge=1, le=2)
    contrast_guardrail: str


class CaptionPreset(BaseModel):
    key: CaptionPresetKey
    name: str
    style: CaptionStyle


class CaptionSegment(BaseModel):
    id: UUID | None = None
    index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    words: list[TranscriptWord] = Field(default_factory=list)
    line_count: int = Field(default=1, ge=1, le=2)
    highlight_words: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_range(self) -> "CaptionSegment":
        if self.end_ms <= self.start_ms:
            raise ValueError("caption segment end_ms must be greater than start_ms")
        return self


class CaptionTrackReviewStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class CaptionTrackReadResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    transcript_id: UUID
    preset: CaptionPresetKey
    schema_version: int
    style: CaptionStyle
    hide_filler_words: bool = False
    language: str = Field(default="original", min_length=2, max_length=24)
    source_track_id: UUID | None = None
    review_status: CaptionTrackReviewStatus = CaptionTrackReviewStatus.APPROVED
    segments: list[CaptionSegment] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CaptionGenerateRequest(JsonStringBodyMixin, BaseModel):
    transcript_id: UUID | None = None
    preset: CaptionPresetKey = CaptionPresetKey.LUXURY_MINIMAL
    hide_filler_words: bool = False
    output_language: str = Field(default="original", min_length=2, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class CaptionGenerateResponse(BaseModel):
    track: CaptionTrackReadResponse | None = None
    job_id: str
    status: Literal["ready", "queued"]
    status_url: str | None = None


class CaptionUpdateRequest(JsonStringBodyMixin, BaseModel):
    preset: CaptionPresetKey | None = None
    hide_filler_words: bool | None = None
    style: CaptionStyle | None = None
    output_language: str | None = Field(default=None, min_length=2, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class CaptionSegmentsUpdateRequest(JsonStringBodyMixin, BaseModel):
    segments: list[CaptionSegment] = Field(min_length=1, max_length=240)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class CaptionSegmentsUpdateResponse(BaseModel):
    track: CaptionTrackReadResponse
    status: Literal["updated"] = "updated"


class CaptionListResponse(BaseModel):
    presets: list[CaptionPreset]
    tracks: list[CaptionTrackReadResponse]
