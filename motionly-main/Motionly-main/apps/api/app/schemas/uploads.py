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
    file_size_bytes: int = Field(gt=0)

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
    job_id: str


class UploadAssetUrlsResponse(BaseModel):
    video_id: UUID
    source_video_url: str
    thumbnail_url: str | None = None
    expires_in_seconds: int


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
    thumbnail_path: str | None = None
    metadata: VideoMetadata = Field(default_factory=VideoMetadata)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
