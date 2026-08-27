from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ClipGoal(StrEnum):
    VIRAL_HOOK = "viral_hook"
    EDUCATIONAL_MOMENT = "educational_moment"
    STORYTELLING = "storytelling"
    PRODUCT_MENTION = "product_mention"


class ClipSignalAvailability(BaseModel):
    transcript: bool = True
    audio_intelligence: bool = False
    visual_analysis: bool = False


class ClipSignalEvidence(BaseModel):
    matched_keywords: list[str] = Field(default_factory=list, max_length=12)
    emotion: str | None = None
    emotion_intensity: float | None = Field(default=None, ge=0, le=1)
    pace: str | None = None
    visual_context: list[str] = Field(default_factory=list, max_length=8)


class ClipRecommendation(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    rank: int = Field(ge=1, le=10)
    duration_s: Literal[15, 30, 60]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    score: float = Field(ge=0, le=100)
    headline: str = Field(min_length=1, max_length=180)
    transcript_excerpt: str = Field(min_length=1, max_length=480)
    rationale: list[str] = Field(min_length=1, max_length=4)
    signals: ClipSignalEvidence


class ClipRecommendationGroup(BaseModel):
    duration_s: Literal[15, 30, 60]
    clips: list[ClipRecommendation] = Field(default_factory=list, max_length=5)


class ClipRecommendationsResponse(BaseModel):
    goal: ClipGoal
    source_duration_ms: int = Field(gt=0)
    signal_availability: ClipSignalAvailability
    groups: list[ClipRecommendationGroup] = Field(default_factory=list, max_length=3)
