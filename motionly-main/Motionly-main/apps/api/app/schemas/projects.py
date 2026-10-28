from datetime import datetime
from enum import StrEnum
from typing import Literal
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


class ProjectLatestUpload(BaseModel):
    """The project's active/latest upload, embedded in list responses.

    Lets the dashboard and projects page render each project's upload state in
    ONE API call instead of one extra /uploads/{id} request per project.
    """

    id: UUID
    status: str
    original_filename: str
    duration_ms: int | None = None
    warnings: list[str] = Field(default_factory=list)


class ProjectResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    created_by: UUID
    title: str
    status: ProjectStatus
    active_video_id: UUID | None = None
    latest_upload: ProjectLatestUpload | None = None
    created_at: datetime
    updated_at: datetime


ProjectListSort = Literal["updated_desc", "updated_asc", "title_asc"]
PROJECT_SEARCH_MAX_LENGTH = 120


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
    # Every project matching the search in the workspace, not just this page.
    total: int = Field(ge=0)


class ProjectDeleteResponse(BaseModel):
    id: UUID
    deleted: bool = True
