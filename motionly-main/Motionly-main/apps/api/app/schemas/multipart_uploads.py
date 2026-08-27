"""Typed request and response contracts for session-safe multipart uploads."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class MultipartSessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETING = "completing"
    COMPLETED = "completed"
    ABORTED = "aborted"
    EXPIRED = "expired"
    FAILED = "failed"


class MultipartUploadInitRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    file_size_bytes: int = Field(gt=0, le=536_870_912)
    sha256_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("filename")
    @classmethod
    def trim_filename(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("filename must not be blank")
        return value

    @field_validator("mime_type")
    @classmethod
    def normalize_mime_type(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("mime_type must not be blank")
        return value

    @field_validator("sha256_hash")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("sha256_hash must be 64 lowercase hexadecimal characters")
        return value


class PartUploadUrl(BaseModel):
    part_number: int = Field(ge=1)
    upload_url: str = Field(min_length=1)
    upload_headers: dict[str, str]
    expires_in_seconds: int = Field(gt=0, le=7200)


class MultipartUploadInitResponse(BaseModel):
    video_id: UUID
    session_id: UUID
    status: MultipartSessionStatus
    total_parts: int = Field(gt=0)
    part_size_bytes: int = Field(gt=0)
    part_urls: list[PartUploadUrl] = Field(max_length=5)
    expires_at: datetime
    warnings: list[str] = Field(default_factory=list)


class PartCompleteRequest(BaseModel):
    etag: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("etag")
    @classmethod
    def validate_etag(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("etag contains invalid characters")
        return value


class PartCompleteResponse(BaseModel):
    session_id: UUID
    part_number: int = Field(ge=1)
    status: str
    verified_size_bytes: int = Field(gt=0)
    completed_parts: int = Field(ge=0)
    uploaded_bytes: int = Field(ge=0)


class MultipartCompleteRequest(BaseModel):
    sha256_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("sha256_hash")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("sha256_hash must be 64 lowercase hexadecimal characters")
        return value


class MultipartCompleteResponse(BaseModel):
    session_id: UUID
    video_id: UUID
    status: MultipartSessionStatus
    source_reference_id: UUID
    verified_size_bytes: int = Field(gt=0)
    verified_sha256: str = Field(min_length=64, max_length=64)
    job_id: str | None = None
    workflow_run_id: UUID
    workflow_version: str
    workflow_state: str
    workflow_status_url: str


class UploadSessionResponse(BaseModel):
    session_id: UUID
    video_id: UUID
    status: MultipartSessionStatus
    total_parts: int = Field(gt=0)
    completed_parts: int = Field(ge=0)
    uploaded_bytes: int = Field(ge=0)
    file_size_bytes: int = Field(gt=0)
    part_size_bytes: int = Field(gt=0)
    pending_part_urls: list[PartUploadUrl] = Field(max_length=5)
    expires_at: datetime
    verified_size_bytes: int | None = Field(default=None, gt=0)
    verified_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    source_reference_id: UUID | None = None
