from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.request_body import JsonStringBodyMixin

BROLL_SCHEMA_VERSION = 1


class BrollSlotStatus(StrEnum):
    READY = "ready"
    DISABLED = "disabled"
    FAILED = "failed"


class StockProviderKey(StrEnum):
    FIXTURE = "fixture"
    PEXELS = "pexels"
    PIXABAY = "pixabay"


class StockOrientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SQUARE = "square"


class StockVideoAssetResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    provider: StockProviderKey
    provider_asset_id: str
    title: str
    thumbnail_url: str | None = None
    preview_video_url: str | None = None
    download_url: str | None = None
    license: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    duration_ms: int = Field(ge=0)
    orientation: StockOrientation
    score: float = Field(ge=0, le=1)
    raw_metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class BrollSlotResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    edit_plan_id: UUID
    version: int
    schema_version: int
    status: BrollSlotStatus
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    keywords: list[str] = Field(min_length=1, max_length=8)
    intent: str = Field(min_length=1, max_length=160)
    density: str = Field(min_length=1, max_length=24)
    priority: float = Field(ge=0, le=1)
    selected_asset_id: UUID | None = None
    candidate_asset_ids: list[UUID] = Field(default_factory=list)
    crossfade_ms: int = Field(ge=0, le=800)
    provider: StockProviderKey
    score: float = Field(ge=0, le=1)
    error_code: str | None = None
    error_message: str | None = None
    selected_asset: StockVideoAssetResponse | None = None
    candidates: list[StockVideoAssetResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_range(self) -> "BrollSlotResponse":
        if self.end_ms <= self.start_ms:
            raise ValueError("broll slot end_ms must be greater than start_ms")
        return self


class BrollGenerateRequest(JsonStringBodyMixin, BaseModel):
    density: Literal["from_plan", "low", "medium", "high"] = "from_plan"
    force: bool = False

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class BrollGenerateResponse(BaseModel):
    slots: list[BrollSlotResponse]
    job_id: str
    status: Literal["ready"] = "ready"
    cached: bool = False


class BrollListResponse(BaseModel):
    slots: list[BrollSlotResponse]
    active_version: int | None = None


class BrollSwapRequest(JsonStringBodyMixin, BaseModel):
    asset_id: UUID | None = None
    disabled: bool = False

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class BrollSwapResponse(BaseModel):
    slot: BrollSlotResponse
    status: Literal["updated"] = "updated"


class BrollRegionUpdateRequest(JsonStringBodyMixin, BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    disabled: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)

    @model_validator(mode="after")
    def validate_range(self) -> "BrollRegionUpdateRequest":
        if self.end_ms <= self.start_ms:
            raise ValueError("broll slot end_ms must be greater than start_ms")
        return self


class BrollRegionUpdateResponse(BaseModel):
    slot: BrollSlotResponse
    status: Literal["updated"] = "updated"


class BrollSearchResponse(BaseModel):
    slot: BrollSlotResponse
    candidates: list[StockVideoAssetResponse]
    query: str
