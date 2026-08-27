from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class UploadStatus(StrEnum):
    CREATED = "created"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class UploadInitRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str
    file_size_bytes: int = Field(gt=0, le=536_870_912)

    @field_validator("filename")
    @classmethod
    def trim_filename(cls, value: str) -> str:
        return value.strip()


class UploadInitResponse(BaseModel):
    video_id: UUID
    status: UploadStatus
    storage_bucket: str
    storage_path: str
    signed_upload_url: str
    upload_headers: dict[str, str]
    expires_in_seconds: int
    warnings: list[str] = Field(default_factory=list)


class UploadCompleteRequest(BaseModel):
    observed_file_size_bytes: int | None = Field(default=None, gt=0)


class UploadCompleteResponse(BaseModel):
    video_id: UUID
    status: UploadStatus
    job_id: str | None = None
    workflow_run_id: UUID | None = None
    workflow_version: str | None = None
    workflow_state: str | None = None
    workflow_status_url: str | None = None


class UploadAssetUrlsResponse(BaseModel):
    video_id: UUID
    source_video_url: str
    thumbnail_url: str | None = None
    expires_in_seconds: int
    expires_at: datetime | None = None
    source_reference_id: UUID | None = None
    thumbnail_reference_id: UUID | None = None


class VideoMetadata(BaseModel):
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None
    bitrate: int | None = None
    frame_count: int | None = None
    has_audio: bool | None = None
    orientation: Literal["vertical", "landscape", "square", "unknown"] = "unknown"
    rotation_degrees: int | None = None
    warnings: list[str] = Field(default_factory=list)


class UploadReadResponse(BaseModel):
    id: UUID
    project_id: UUID
    workspace_id: UUID
    status: UploadStatus
    storage_bucket: str
    storage_path: str
    original_filename: str
    mime_type: str
    file_size_bytes: int
    media_revision: int = Field(default=1, ge=1)
    source_object_reference_id: UUID | None = None
    verified_size_bytes: int | None = None
    verified_sha256: str | None = None
    workflow_version: str | None = None
    thumbnail_path: str | None = None
    metadata: VideoMetadata = Field(default_factory=VideoMetadata)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
