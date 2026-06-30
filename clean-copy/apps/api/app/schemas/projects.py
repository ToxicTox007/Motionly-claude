from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    FAILED = "failed"


class ProjectCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        return value.strip()


class ProjectUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    status: ProjectStatus | None = None

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value


class ProjectResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    created_by: UUID
    title: str
    status: ProjectStatus
    active_video_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]


class ProjectDeleteResponse(BaseModel):
    id: UUID
    deleted: bool = True
