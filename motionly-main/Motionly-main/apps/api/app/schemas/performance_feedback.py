from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.publishing import PublishPlatform

PERFORMANCE_FEEDBACK_ALLOWED_METRICS = (
    "views",
    "average_view_duration_seconds",
    "completion_rate",
    "engagement_rate",
)
PERFORMANCE_FEEDBACK_EXCLUDED_DATA = (
    "viewer identities and audience demographics",
    "comments, direct messages, and follower lists",
    "post copy, transcript text, prompts, and source media",
    "raw provider payloads and access tokens",
)


class DurationBucket(StrEnum):
    SHORT = "under_30_seconds"
    MEDIUM = "30_to_60_seconds"
    LONG = "over_60_seconds"
    UNKNOWN = "unknown"


class PerformanceFeedbackPolicyUpdateRequest(BaseModel):
    metrics_ingestion_enabled: bool
    suggestions_enabled: bool
    policy_version: str = Field(min_length=1, max_length=80)
    expected_revision: int | None = Field(default=None, ge=0)
    retention_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def require_explicit_policy_version(self) -> "PerformanceFeedbackPolicyUpdateRequest":
        if not self.policy_version.strip():
            raise ValueError("A policy version acknowledgement is required")
        return self


class PerformanceFeedbackPolicyResponse(BaseModel):
    workspace_id: UUID
    user_id: UUID
    metrics_ingestion_enabled: bool = False
    suggestions_enabled: bool = False
    policy_version: str = "unacknowledged"
    revision: int = 0
    retention_days: int = Field(ge=1, le=365)
    revoked_at: datetime | None = None
    updated_at: datetime | None = None


class PerformanceFeedbackStatusResponse(BaseModel):
    enabled: bool
    policy_version: str
    default_retention_days: int = Field(ge=1, le=365)
    minimum_examples: int = Field(ge=1)
    allowed_metrics: tuple[str, ...] = PERFORMANCE_FEEDBACK_ALLOWED_METRICS
    excluded_data: tuple[str, ...] = PERFORMANCE_FEEDBACK_EXCLUDED_DATA


class PerformanceFeedbackRevocationRequest(BaseModel):
    reason: str = Field(default="user_request", min_length=1, max_length=160)


class PerformanceMetricIngestRequest(BaseModel):
    """An aggregate, post-level snapshot supplied by an authorized integration or creator."""

    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    views: int | None = Field(default=None, ge=0, le=2_147_483_647)
    average_view_duration_seconds: float | None = Field(default=None, ge=0, le=86_400)
    completion_rate: float | None = Field(default=None, ge=0, le=1)
    engagement_rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_at_least_one_aggregate_metric(self) -> "PerformanceMetricIngestRequest":
        if all(
            value is None
            for value in (
                self.views,
                self.average_view_duration_seconds,
                self.completion_rate,
                self.engagement_rate,
            )
        ):
            raise ValueError("Provide at least one approved aggregate performance metric")
        return self


class PerformanceMetricResponse(BaseModel):
    id: UUID
    project_id: UUID
    publish_job_id: UUID
    platform: PublishPlatform
    observed_at: datetime
    expires_at: datetime
    suggestion_eligible: bool
    created_at: datetime


class PerformanceFeedbackSuggestionKind(StrEnum):
    CAPTIONS = "captions"
    HOOK_STYLE = "hook_style"
    DURATION = "duration"


class PerformanceFeedbackSuggestion(BaseModel):
    kind: PerformanceFeedbackSuggestionKind
    recommended_value: str
    editor_instruction: str
    sample_count: int = Field(ge=1)
    average_retention_rate: float = Field(ge=0, le=1)
    confidence: str
    is_observational: bool = True


class PerformanceFeedbackInsightsResponse(BaseModel):
    status: str
    policy_revision: int
    minimum_examples: int
    suggestions: list[PerformanceFeedbackSuggestion] = Field(default_factory=list)
    data_boundaries: tuple[str, ...] = PERFORMANCE_FEEDBACK_EXCLUDED_DATA
