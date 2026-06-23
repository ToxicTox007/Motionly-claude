from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.plans import BrollDensity, EditPlanStyle


class ScaleResourceStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELED = "canceled"


class ScaleResourceResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID | None = None
    kind: str
    name: str
    status: ScaleResourceStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    revision: int = 1
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ScaleResourceListResponse(BaseModel):
    resources: list[ScaleResourceResponse]


class StemSeparationRequest(BaseModel):
    model: str = Field(default="htdemucs", max_length=80)


class GeneratedBrollRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    prompt_image_url: str | None = Field(default=None, max_length=4000)
    duration_s: int = Field(default=5, ge=5, le=10)
    ratio: str = Field(default="768:1280", pattern="^(768:1280|1280:768)$")
    slot_id: UUID | None = None


class StyleProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_style: EditPlanStyle = EditPlanStyle.CREATOR_INFLUENCER


class StyleReferenceInitRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(pattern="^video/")
    file_size_bytes: int = Field(gt=0)


class StyleReferenceInitResponse(BaseModel):
    profile: ScaleResourceResponse
    reference_id: UUID
    signed_upload_url: str
    upload_headers: dict[str, str]
    expires_in_seconds: int


class StyleReferenceCompleteRequest(BaseModel):
    reference_id: UUID


class StyleTrainRequest(BaseModel):
    force: bool = False


class BatchCreateRequest(BaseModel):
    name: str = Field(default="Batch edit", min_length=1, max_length=120)
    project_ids: list[UUID] = Field(min_length=1, max_length=20)
    style: EditPlanStyle | None = None
    target_duration_s: int = Field(default=35, ge=10, le=90)
    broll_density: BrollDensity = BrollDensity.MEDIUM
    prompt: str = Field(default="", max_length=1200)
    style_profile_id: UUID | None = None
    saved_template_id: UUID | None = None

    @model_validator(mode="after")
    def unique_projects(self) -> "BatchCreateRequest":
        self.project_ids = list(dict.fromkeys(self.project_ids))
        return self


class SavedTemplateSettings(BaseModel):
    style: EditPlanStyle
    target_duration_s: int = Field(default=35, ge=10, le=90)
    broll_density: BrollDensity = BrollDensity.MEDIUM
    direction: str = Field(default="", max_length=1200)
    caption_settings: dict[str, Any] = Field(default_factory=dict)
    effects_settings: dict[str, Any] = Field(default_factory=dict)


class SavedTemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    settings: SavedTemplateSettings


class SavedTemplateUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    settings: SavedTemplateSettings | None = None


class CollaborationManifestUpdateRequest(BaseModel):
    base_revision: int = Field(ge=0)
    manifest: dict[str, Any]


class ModelTrainingRequest(BaseModel):
    name: str = Field(default="Workspace preference adapter", min_length=1, max_length=120)
    minimum_examples: int = Field(default=3, ge=1, le=1000)


class AudioKeyword(BaseModel):
    text: str
    score: float


class AudioStyleSignals(BaseModel):
    predicted_style: str
    tone: str
    pace: str
    style_scores: dict[str, float]


class AudioSpeechMetrics(BaseModel):
    word_count: int
    duration_ms: int | None = None
    words_per_minute: int | None = None


class AudioConfidence(BaseModel):
    average_word_confidence: float | None = None
    low_confidence_word_count: int
    word_confidence_available: bool


class SpeakerSegment(BaseModel):
    speaker: str
    start_ms: int
    end_ms: int
    segment_index: int


class SpeakerDiarization(BaseModel):
    provider: str
    model: str | None = None
    speaker_count: int
    confidence: float | None = None
    segments: list[SpeakerSegment]


class PauseMapEntry(BaseModel):
    start_ms: int
    end_ms: int
    duration_ms: int


class SpeechEnergyWindow(BaseModel):
    start_ms: int
    end_ms: int
    speech_density: float
    word_onsets: int


class SpeechRhythm(BaseModel):
    bpm_equivalent: int | None = None
    onset_count: int
    mean_interval_ms: int | None = None
    interval_variability: float | None = None
    energy_curve: list[SpeechEnergyWindow]


class EmotionWindow(BaseModel):
    start_ms: int
    end_ms: int
    emotion: str
    joy: float | None = None
    sadness: float | None = None
    urgency: float | None = None
    intensity: float
    word_count: int | None = None


class EmotionPacing(BaseModel):
    aggregate_emotion: str
    pause_count: int
    provider: str | None = None
    pause_map: list[PauseMapEntry]
    speech_rhythm: SpeechRhythm
    windows: list[EmotionWindow]


class SoundLabel(BaseModel):
    label: str
    confidence: float


class SoundClassification(BaseModel):
    provider: str
    model: str | None = None
    background_sound: str | None = None
    labels: list[SoundLabel]


class AudioIntelligence(BaseModel):
    schema_version: int
    keywords: list[AudioKeyword]
    style_signals: AudioStyleSignals
    speech_metrics: AudioSpeechMetrics
    confidence: AudioConfidence
    speaker_diarization: SpeakerDiarization
    emotion_pacing: EmotionPacing
    sound_classification: SoundClassification


class AudioIntelligenceResponse(BaseModel):
    project_id: UUID
    audio_intelligence: AudioIntelligence
