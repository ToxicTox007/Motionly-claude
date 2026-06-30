from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field


class PublishPlatform(StrEnum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    YOUTUBE = "youtube"


class PublishStatus(StrEnum):
    QUEUED = "queued"
    REFRESHING_TOKEN = "refreshing_token"
    PUBLISHING = "publishing"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    PUBLISHED = "published"
    RETRY_SCHEDULED = "retry_scheduled"
    RECONNECT_REQUIRED = "reconnect_required"
    DUPLICATE_RISK_NEEDS_REVIEW = "duplicate_risk_needs_review"
    FAILED = "failed"
    CANCELED = "cancelled"


class PublishConnectionStatus(StrEnum):
    ACTIVE = "active"
    RECONNECT_REQUIRED = "reconnect_required"


class PublishConnectionCreateRequest(BaseModel):
    platform: PublishPlatform
    access_token: str = Field(min_length=8, max_length=4096)
    external_account_id: str = Field(min_length=1, max_length=255)
    account_name: str = Field(min_length=1, max_length=255)


class PublishConnectionResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    platform: PublishPlatform
    external_account_id: str
    account_name: str
    connection_status: PublishConnectionStatus
    access_token_expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    last_refresh_at: datetime | None = None
    last_refresh_failure_reason: str | None = None
    created_at: datetime


class PublishConnectionListResponse(BaseModel):
    connections: list[PublishConnectionResponse]


class PublishOAuthStartRequest(BaseModel):
    return_url: AnyHttpUrl


class PublishOAuthStartResponse(BaseModel):
    authorization_url: str


class PublishCreateRequest(BaseModel):
    export_id: UUID
    connection_id: UUID
    title: str = Field(default="Motionly export", min_length=1, max_length=100)
    caption: str = Field(default="", max_length=2200)
    privacy: str = Field(default="private", pattern="^(private|public|unlisted)$")


class PublishJobResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    export_id: UUID
    connection_id: UUID
    platform: PublishPlatform
    status: PublishStatus
    title: str
    caption: str
    privacy: str
    external_post_id: str | None = None
    external_url: str | None = None
    provider_operation_id: str | None = None
    error_message: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    user_message: str | None = None
    retryable: bool | None = None
    reconnect_required: bool = False
    retry_after_seconds: int | None = None
    retry_count: int = 0
    duplicate_publish_risk: bool = False
    created_at: datetime
    updated_at: datetime


class PublishJobListResponse(BaseModel):
    jobs: list[PublishJobResponse]
