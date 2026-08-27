from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.request_body import JsonStringBodyMixin

MUSIC_SCHEMA_VERSION = 1


class MusicProviderKey(StrEnum):
    FIXTURE = "fixture"
    LICENSED_CATALOG = "licensed_catalog"


class MusicRegion(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    disabled: bool = False

    @model_validator(mode="after")
    def validate_range(self) -> "MusicRegion":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        return self


class MusicTrackResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    provider: MusicProviderKey
    provider_track_id: str
    title: str
    license: str
    duration_ms: int = Field(ge=0)
    mood_tags: list[str] = Field(default_factory=list)
    bpm: int | None = Field(default=None, ge=0)
    energy: float | None = Field(default=None, ge=0, le=1)
    vocals: bool = False
    loopable: bool = False
    beat_map: list[float] = Field(default_factory=list)
    raw_metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class MusicSelectionStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"


class MusicRightsStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    EXPIRED = "expired"
    REVOKED = "revoked"


class MusicRightsResponse(BaseModel):
    status: MusicRightsStatus
    verified: bool = False
    provider_track_id: str | None = None
    grant_id: str | None = None
    grant_version: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    permitted_uses: list[str] = Field(default_factory=list)
    evidence_fingerprint: str | None = None
    verified_at: datetime | None = None


class MusicSelectRequest(JsonStringBodyMixin, BaseModel):
    music_track_id: UUID | None = None
    mood: str | None = None
    include_music: bool = True
    music_volume: float = Field(default=0.25, ge=0, le=1)
    sfx_intensity: float = Field(default=0.25, ge=0, le=1)
    # A user can acknowledge the catalog license, but this never establishes
    # rights; the server derives that from a current catalog grant.
    rights_decision: bool = Field(default=False)
    rights_confirmed: bool | None = Field(
        default=None,
        description="Deprecated compatibility input. It is ignored by the server.",
        json_schema_extra={"deprecated": True},
    )
    force: bool = False

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: object) -> object:
        return cls._coerce_json_string_body(data)


class MusicSelectionResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    edit_plan_id: UUID
    music_track_id: UUID | None = None
    music_volume: float = Field(ge=0, le=1)
    sfx_intensity: float = Field(ge=0, le=1)
    rights_confirmed: bool
    rights: MusicRightsResponse
    status: MusicSelectionStatus
    cache_key: str | None = None
    created_at: datetime
    updated_at: datetime
    music_track: MusicTrackResponse | None = None
    regions: list[MusicRegion] = Field(default_factory=list)


class MusicSelectResponse(BaseModel):
    selection: MusicSelectionResponse | None = None
    job_id: str
    status: Literal["ready", "queued"]
    cached: bool = False
    status_url: str | None = None


class MusicRegionsUpdateRequest(JsonStringBodyMixin, BaseModel):
    regions: list[MusicRegion] = Field(default_factory=list, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: object) -> object:
        return cls._coerce_json_string_body(data)


class MusicTrackListResponse(BaseModel):
    tracks: list[MusicTrackResponse]
    mood: str
