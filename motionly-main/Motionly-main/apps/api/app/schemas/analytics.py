from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AIEditLayer(StrEnum):
    CUTS = "cuts"
    CAPTIONS = "captions"
    BROLL = "broll"
    MUSIC = "music"
    EFFECTS = "effects"
    RENDER = "render"


class AIEditFeedbackEventType(StrEnum):
    AI_SUGGESTED = "ai_suggested"
    KEPT = "kept"
    CHANGED = "changed"
    REJECTED = "rejected"


class AIEditFeedbackEventCreateRequest(BaseModel):
    edit_plan_id: UUID | None = None
    event_type: AIEditFeedbackEventType
    layer: AIEditLayer
    decision_id: str | None = Field(default=None, max_length=160)
    action: str | None = Field(default=None, max_length=120)
    source: str = Field(default="manual", min_length=1, max_length=80)
    ai_payload: dict[str, Any] = Field(default_factory=dict)
    user_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AIEditFeedbackEventResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    edit_plan_id: UUID | None = None
    user_id: UUID
    event_type: AIEditFeedbackEventType
    layer: AIEditLayer
    decision_id: str | None = None
    action: str | None = None
    source: str
    ai_payload: dict[str, Any]
    user_payload: dict[str, Any]
    metadata: dict[str, Any]
    created_at: datetime


class AIEditLayerSummary(BaseModel):
    layer: AIEditLayer
    suggested: int = 0
    kept: int = 0
    changed: int = 0
    rejected: int = 0
    keep_rate: float = 0


class AIEditAnalyticsSummary(BaseModel):
    total_events: int
    training_example_count: int
    layers: list[AIEditLayerSummary]


class AIEditAnalyticsResponse(BaseModel):
    summary: AIEditAnalyticsSummary
    events: list[AIEditFeedbackEventResponse]


class FineTuningSignal(StrEnum):
    POSITIVE = "positive"
    CORRECTION = "correction"
    REJECTION = "rejection"


class FineTuningExample(BaseModel):
    event_id: UUID
    project_id: UUID
    edit_plan_id: UUID | None = None
    layer: AIEditLayer
    signal: FineTuningSignal
    weight: float = Field(ge=0, le=1)
    decision_id: str | None = None
    input: dict[str, Any]
    ideal_output: dict[str, Any]
    rejected_output: dict[str, Any] | None = None
    created_at: datetime


class FineTuningDatasetResponse(BaseModel):
    project_id: UUID
    example_count: int
    examples: list[FineTuningExample]
