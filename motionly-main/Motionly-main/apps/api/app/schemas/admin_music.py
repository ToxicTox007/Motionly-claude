"""Task 27: admin music catalog schemas (import + listing with rights status)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.request_body import JsonStringBodyMixin


class TrackVerificationInput(BaseModel):
    """A complete, verified rights record. Every field is required."""

    source_page_url: str = Field(min_length=1, max_length=2048)
    license_archive_sha256: str = Field(min_length=64, max_length=64)
    license_id: str = Field(min_length=1, max_length=64)
    license_version: str = Field(min_length=1, max_length=32)
    rights_holder: str = Field(min_length=1, max_length=256)
    composition_rights_status: str = Field(min_length=1, max_length=32)
    recording_rights_status: str = Field(min_length=1, max_length=32)
    verified_by: str = Field(min_length=1, max_length=256)
    verified_at: datetime


class MusicCatalogImportRequest(JsonStringBodyMixin, BaseModel):
    provider_track_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    audio_bucket: str = Field(default="music-catalog", max_length=64)
    audio_path: str = Field(min_length=1, max_length=1024)
    mood_tags: list[str] = Field(default_factory=list, max_length=16)
    is_test_track: bool = False
    verification: TrackVerificationInput


class MusicCatalogTrackResponse(BaseModel):
    provider_track_id: str
    title: str
    license: str
    license_version: str | None = None
    rights_status: str
    composition_rights_status: str | None = None
    recording_rights_status: str | None = None
    rights_holder: str | None = None
    verified_by: str | None = None
    verified_at: datetime | None = None
    source_page_url: str | None = None
    duration_ms: int | None = None
    bpm: int | None = None
    energy: float | None = None
    mood_tags: list[str] = Field(default_factory=list)
    is_test_track: bool = False
    preview_url: str | None = None


class MusicCatalogListResponse(BaseModel):
    tracks: list[MusicCatalogTrackResponse]
    allow_list_approved: bool
