from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.renders import DurationPreset, ExportOptions

REGENERATION_SCHEMA_VERSION = 1


class RegenerationLayer(StrEnum):
    TRANSCRIPT = "transcript"
    CAPTIONS = "captions"
    PLAN = "plan"
    CUTS = "cuts"
    BROLL = "broll"
    MUSIC = "music"
    EFFECTS = "effects"
    RENDER = "render"


class RegenerationAction(StrEnum):
    MAKE_PUNCHIER = "make_punchier"
    MORE_CINEMATIC = "more_cinematic"
    LESS_BROLL = "less_broll"
    CHANGE_MUSIC = "change_music"


class UserControlSettings(BaseModel):
    caption_style: str | None = Field(default=None, max_length=80)
    broll_density: float | None = Field(default=None, ge=0, le=1)
    music_volume: float | None = Field(default=None, ge=0, le=1)
    sfx_intensity: float | None = Field(default=None, ge=0, le=1)
    effects_intensity: float | None = Field(default=None, ge=0, le=1)
    duration_preset: DurationPreset | None = None


class RegenerateRequest(BaseModel):
    controls: UserControlSettings = Field(default_factory=UserControlSettings)
    action: RegenerationAction | None = None
    layers: list[RegenerationLayer] = Field(default_factory=list)
    export_options: ExportOptions = Field(default_factory=ExportOptions)
    force: bool = False

    @model_validator(mode="after")
    def require_meaningful_change(self) -> "RegenerateRequest":
        if not self.action and not self.layers and not self.controls.model_dump(exclude_none=True):
            raise ValueError("Choose at least one control, action, or layer to regenerate.")
        return self


class ArtifactVersionResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    layer: RegenerationLayer
    version: int
    artifact_id: UUID | None = None
    invalidates_from_version: int | None = None
    cache_key: str
    settings_summary: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class ControlSettingsResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    version: int
    schema_version: int
    controls: UserControlSettings
    action: RegenerationAction | None = None
    export_options: ExportOptions
    settings_summary: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class RegenerateResponse(BaseModel):
    status: Literal["queued"] = "queued"
    job_id: str
    affected_layers: list[RegenerationLayer]
    preserved_layers: list[RegenerationLayer]
    controls: ControlSettingsResponse
    artifact_versions: list[ArtifactVersionResponse]
    creates_new_version: bool = True
    message: str = "This creates a new version and keeps your current export."
