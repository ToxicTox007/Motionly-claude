"""Contracts shared by server-owned non-source media upload endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.multipart_uploads import (
    MultipartSessionStatus,
    PartCompleteRequest,
    PartCompleteResponse,
    PartUploadUrl,
)


class MediaUploadSessionResponse(BaseModel):
    session_id: UUID
    owner_id: UUID
    status: MultipartSessionStatus
    total_parts: int = Field(gt=0)
    completed_parts: int = Field(ge=0)
    uploaded_bytes: int = Field(ge=0)
    file_size_bytes: int = Field(gt=0)
    part_size_bytes: int = Field(gt=0)
    pending_part_urls: list[PartUploadUrl] = Field(max_length=5)
    expires_at: datetime
    object_reference_id: UUID | None = None
    verified_size_bytes: int | None = Field(default=None, gt=0)
    verified_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class MediaUploadCompleteRequest(BaseModel):
    sha256_hash: str | None = Field(default=None, min_length=64, max_length=64)


__all__ = [
    "MediaUploadCompleteRequest",
    "MediaUploadSessionResponse",
    "PartCompleteRequest",
    "PartCompleteResponse",
    "PartUploadUrl",
]
