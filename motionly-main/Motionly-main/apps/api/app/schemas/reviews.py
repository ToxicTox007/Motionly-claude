from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.plans import PlanCreateResponse
from app.schemas.request_body import JsonStringBodyMixin


class ReviewStatus(StrEnum):
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewCreateRequest(JsonStringBodyMixin, BaseModel):
    model_config = ConfigDict(extra="forbid")

    edit_plan_id: UUID
    expected_plan_revision: int = Field(ge=1)
    expected_media_revision: int = Field(ge=1)
    expected_uploaded_video_id: UUID
    expected_source_object_reference_id: UUID
    expected_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data):
        return cls._coerce_json_string_body(data)


class ReviewCommentCreateRequest(JsonStringBodyMixin, BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=2000)
    timecode_ms: int | None = Field(default=None, ge=0)
    parent_comment_id: UUID | None = None
    assignee_user_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data):
        return cls._coerce_json_string_body(data)

    @model_validator(mode="after")
    def require_unique_assignees(self):
        if len(self.assignee_user_ids) != len(set(self.assignee_user_ids)):
            raise ValueError("assignee_user_ids must be unique")
        self.body = self.body.strip()
        return self


class ReviewCommentResponse(BaseModel):
    id: UUID
    review_id: UUID
    parent_comment_id: UUID | None = None
    timecode_ms: int | None = None
    body: str
    author_user_id: UUID | None = None
    author_name: str | None = None
    assignee_user_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ReviewResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    edit_plan_id: UUID
    edit_plan_revision: int
    uploaded_video_id: UUID
    source_media_revision: int
    source_object_reference_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str
    status: ReviewStatus
    status_revision: int
    decision_reason: str | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class ReviewDetailResponse(ReviewResponse):
    comments: list[ReviewCommentResponse] = Field(default_factory=list)


class ReviewListResponse(BaseModel):
    reviews: list[ReviewResponse] = Field(default_factory=list)


class ReviewDecisionRequest(JsonStringBodyMixin, BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReviewStatus
    expected_status_revision: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data):
        return cls._coerce_json_string_body(data)

    @model_validator(mode="after")
    def require_terminal_decision(self):
        if self.decision == ReviewStatus.OPEN:
            raise ValueError("Review decisions must approve or reject")
        self.reason = self.reason.strip() if self.reason else None
        return self


class ReviewShareLinkCreateRequest(JsonStringBodyMixin, BaseModel):
    model_config = ConfigDict(extra="forbid")

    expires_in_hours: int = Field(default=168, ge=1, le=24 * 30)
    can_comment: bool = True
    can_decide: bool = False

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data):
        return cls._coerce_json_string_body(data)


class ReviewShareLinkCreateResponse(BaseModel):
    id: UUID
    review_id: UUID
    url: str
    expires_at: datetime
    can_comment: bool
    can_decide: bool


class ReviewFeedbackSuggestionRequest(JsonStringBodyMixin, BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data):
        return cls._coerce_json_string_body(data)

    @model_validator(mode="after")
    def require_unique_comments(self):
        if len(self.comment_ids) != len(set(self.comment_ids)):
            raise ValueError("comment_ids must be unique")
        return self


class ReviewFeedbackSuggestionResponse(BaseModel):
    review_id: UUID
    feedback_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal: PlanCreateResponse


class PublicReviewResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=24, max_length=256)


class PublicReviewCommentCreateRequest(PublicReviewResolveRequest):
    body: str = Field(min_length=1, max_length=2000)
    author_name: str | None = Field(default=None, max_length=120)
    timecode_ms: int | None = Field(default=None, ge=0)
    parent_comment_id: UUID | None = None

    @model_validator(mode="after")
    def trim_public_comment(self):
        self.body = self.body.strip()
        self.author_name = self.author_name.strip() if self.author_name else None
        return self


class PublicReviewDecisionRequest(PublicReviewResolveRequest):
    decision: ReviewStatus
    expected_status_revision: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    decider_name: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_public_decision(self):
        if self.decision == ReviewStatus.OPEN:
            raise ValueError("Review decisions must approve or reject")
        self.reason = self.reason.strip() if self.reason else None
        self.decider_name = self.decider_name.strip() if self.decider_name else None
        return self


class PublicReviewResponse(BaseModel):
    id: UUID
    edit_plan_revision: int
    source_media_revision: int
    plan_hash: str
    status: ReviewStatus
    status_revision: int
    decision_reason: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    comments: list["PublicReviewCommentResponse"] = Field(default_factory=list)
    can_comment: bool
    can_decide: bool
    expires_at: datetime
    media_url: str | None = None
    media_expires_at: datetime | None = None


class PublicReviewCommentResponse(BaseModel):
    """Capability-link-safe projection of review feedback."""

    id: UUID
    parent_comment_id: UUID | None = None
    timecode_ms: int | None = None
    body: str
    author_name: str
    created_at: datetime
    updated_at: datetime
