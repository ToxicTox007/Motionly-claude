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
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


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
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class PublishJobListResponse(BaseModel):
    jobs: list[PublishJobResponse]
