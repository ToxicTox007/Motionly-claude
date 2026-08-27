from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.request_body import JsonStringBodyMixin


class PublishLanguage(StrEnum):
    ENGLISH = "en"
    HINDI = "hi"
    SPANISH = "es"
    PORTUGUESE = "pt"
    FRENCH = "fr"
    GERMAN = "de"
    ARABIC = "ar"


class LocalizedReviewStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class SubtitleFormat(StrEnum):
    SRT = "srt"
    VTT = "vtt"


class LocalizedOverlayTranslation(BaseModel):
    source_element_id: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=1200)


class LocalizedPublishPackageCreateRequest(JsonStringBodyMixin, BaseModel):
    language: PublishLanguage
    source_caption_track_id: UUID | None = None
    edit_plan_id: UUID | None = None
    source_title: str | None = Field(default=None, max_length=180)
    source_description: str | None = Field(default=None, max_length=2200)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class LocalizedPublishPackageReviewRequest(JsonStringBodyMixin, BaseModel):
    review_status: LocalizedReviewStatus
    selected_title: str | None = Field(default=None, max_length=180)
    selected_description: str | None = Field(default=None, max_length=2200)
    overlay_translations: list[LocalizedOverlayTranslation] | None = Field(
        default=None,
        max_length=200,
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class LocalizedPublishPackageResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    source_caption_track_id: UUID
    caption_track_id: UUID
    edit_plan_id: UUID | None = None
    language: PublishLanguage
    review_status: LocalizedReviewStatus
    overlay_translations: list[LocalizedOverlayTranslation] = Field(default_factory=list)
    title_suggestions: list[str] = Field(default_factory=list)
    description_suggestions: list[str] = Field(default_factory=list)
    selected_title: str | None = None
    selected_description: str | None = None
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class LocalizedPublishPackageListResponse(BaseModel):
    packages: list[LocalizedPublishPackageResponse]


class AssetLibraryKind(StrEnum):
    BROLL = "broll"
    MUSIC = "music"
    UPLOAD = "upload"
    EXPORT = "export"
    CLIP = "clip"
    BRAND_ASSET = "brand_asset"
    STYLE = "style"


class AssetLibraryMediaState(StrEnum):
    VERIFIED = "verified"
    QUARANTINED = "quarantined"
    NOT_APPLICABLE = "not_applicable"


class AssetLibraryStyleRecipe(BaseModel):
    """The fixed, server-derived style fields that are safe to save for reuse."""

    model_config = ConfigDict(extra="forbid")

    recipe_type: Literal["style"] = "style"
    style: str = Field(min_length=1, max_length=80)
    target_duration_s: int = Field(ge=1, le=600)
    broll_density: str = Field(min_length=1, max_length=40)
    effects: list[str] = Field(default_factory=list, max_length=32)
    color_grade: str = Field(default="", max_length=120)


class AssetLibraryClipRecipe(BaseModel):
    """A non-media source range; it cannot carry a URL or storage location."""

    model_config = ConfigDict(extra="forbid")

    recipe_type: Literal["clip"] = "clip"
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_range(self) -> "AssetLibraryClipRecipe":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


AssetLibraryRecipe = AssetLibraryStyleRecipe | AssetLibraryClipRecipe


class AssetLibraryItemCreateRequest(JsonStringBodyMixin, BaseModel):
    """Metadata-only creation for non-media library entries.

    Managed media is indexed from server-owned project records. Deliberately do
    not accept URLs, bucket/path values, object IDs, preview URLs, or payloads.
    """

    model_config = ConfigDict(extra="forbid")

    kind: AssetLibraryKind
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=600)
    source_project_id: UUID | None = None
    source_asset_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class AssetLibraryItemResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    kind: AssetLibraryKind
    name: str
    description: str = ""
    source_project_id: UUID | None = None
    source_asset_id: UUID | None = None
    object_reference_id: UUID | None = None
    media_state: AssetLibraryMediaState
    quarantine_reason: str | None = None
    media_mime_type: str | None = None
    media_width: int | None = Field(default=None, ge=1)
    media_height: int | None = Field(default=None, ge=1)
    media_duration_ms: int | None = Field(default=None, ge=0)
    recipe: AssetLibraryRecipe | None = None
    is_favorite: bool = False
    created_at: datetime
    updated_at: datetime


class AssetLibraryItemListResponse(BaseModel):
    items: list[AssetLibraryItemResponse]


class AssetLibraryMediaUrlResponse(BaseModel):
    object_reference_id: UUID
    url: str
    expires_at: datetime
    ttl_seconds: int = Field(gt=0, le=300)


class AssetLibraryItemDeleteResponse(BaseModel):
    status: Literal["removed"]
    id: UUID


class AssetFavoriteUpdateRequest(JsonStringBodyMixin, BaseModel):
    is_favorite: bool

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class AssetCollectionCreateRequest(JsonStringBodyMixin, BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class AssetCollectionItemRequest(JsonStringBodyMixin, BaseModel):
    asset_id: UUID

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class AssetCollectionResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    asset_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AssetCollectionListResponse(BaseModel):
    collections: list[AssetCollectionResponse]


class AssetReuseResponse(BaseModel):
    status: Literal["imported", "template_saved", "brand_asset_ready"]
    target_asset_id: UUID | None = None
    saved_template_id: UUID | None = None
    brand_asset: dict[str, Any] | None = None
    message: str
