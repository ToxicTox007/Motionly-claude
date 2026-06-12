from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic import model_validator

from app.schemas.request_body import JsonStringBodyMixin

MUSIC_SCHEMA_VERSION = 1


class MusicProviderKey(StrEnum):
    FIXTURE = "fixture"
    LICENSED_CATALOG = "licensed_catalog"


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


class MusicSelectRequest(JsonStringBodyMixin, BaseModel):
    music_track_id: UUID | None = None
    mood: str | None = None
    include_music: bool = True
    music_volume: float = Field(default=0.25, ge=0, le=1)
    sfx_intensity: float = Field(default=0.25, ge=0, le=1)
    rights_confirmed: bool = Field(default=False)
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
    status: MusicSelectionStatus
    cache_key: str | None = None
    created_at: datetime
    updated_at: datetime
    music_track: MusicTrackResponse | None = None


class MusicSelectResponse(BaseModel):
    selection: MusicSelectionResponse
    job_id: str
    status: Literal["ready"] = "ready"
    cached: bool = False


class MusicTrackListResponse(BaseModel):
    tracks: list[MusicTrackResponse]
    mood: str
