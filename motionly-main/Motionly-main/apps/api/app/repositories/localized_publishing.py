from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.localized_publishing import (
    AssetCollectionResponse,
    AssetLibraryClipRecipe,
    AssetLibraryItemResponse,
    AssetLibraryKind,
    AssetLibraryMediaState,
    AssetLibraryRecipe,
    AssetLibraryStyleRecipe,
    LocalizedOverlayTranslation,
    LocalizedPublishPackageResponse,
    LocalizedReviewStatus,
    PublishLanguage,
)
from app.services.supabase import SupabaseRestClient

PACKAGE_COLUMNS = (
    "id,workspace_id,project_id,source_caption_track_id,caption_track_id,edit_plan_id,language,"
    "review_status,overlay_translations,title_suggestions,description_suggestions,selected_title,"
    "selected_description,revision,created_at,updated_at"
)
ASSET_ITEM_COLUMNS = (
    "id,workspace_id,kind,name,description,source_project_id,source_asset_id,object_reference_id,"
    "media_state,quarantine_reason,media_mime_type,media_width,media_height,media_duration_ms,"
    "recipe,source_key,is_favorite,created_at,updated_at"
)
ASSET_COLLECTION_COLUMNS = "id,workspace_id,name,created_at,updated_at"


@dataclass
class LocalizedPublishPackageRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    source_caption_track_id: UUID
    caption_track_id: UUID
    language: PublishLanguage
    review_status: LocalizedReviewStatus
    edit_plan_id: UUID | None = None
    overlay_translations: list[LocalizedOverlayTranslation] = field(default_factory=list)
    title_suggestions: list[str] = field(default_factory=list)
    description_suggestions: list[str] = field(default_factory=list)
    selected_title: str | None = None
    selected_description: str | None = None
    revision: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def response(self) -> LocalizedPublishPackageResponse:
        return LocalizedPublishPackageResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            source_caption_track_id=self.source_caption_track_id,
            caption_track_id=self.caption_track_id,
            edit_plan_id=self.edit_plan_id,
            language=self.language,
            review_status=self.review_status,
            overlay_translations=self.overlay_translations,
            title_suggestions=self.title_suggestions,
            description_suggestions=self.description_suggestions,
            selected_title=self.selected_title,
            selected_description=self.selected_description,
            revision=self.revision,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class AssetLibraryItemRecord:
    id: UUID
    workspace_id: UUID
    kind: AssetLibraryKind
    name: str
    description: str = ""
    source_project_id: UUID | None = None
    source_asset_id: UUID | None = None
    object_reference_id: UUID | None = None
    media_state: AssetLibraryMediaState = AssetLibraryMediaState.NOT_APPLICABLE
    quarantine_reason: str | None = None
    media_mime_type: str | None = None
    media_width: int | None = None
    media_height: int | None = None
    media_duration_ms: int | None = None
    recipe: AssetLibraryRecipe | None = None
    source_key: str | None = None
    is_favorite: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def response(self) -> AssetLibraryItemResponse:
        return AssetLibraryItemResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            kind=self.kind,
            name=self.name,
            description=self.description,
            source_project_id=self.source_project_id,
            source_asset_id=self.source_asset_id,
            object_reference_id=self.object_reference_id,
            media_state=self.media_state,
            quarantine_reason=self.quarantine_reason,
            media_mime_type=self.media_mime_type,
            media_width=self.media_width,
            media_height=self.media_height,
            media_duration_ms=self.media_duration_ms,
            recipe=self.recipe,
            is_favorite=self.is_favorite,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class AssetCollectionRecord:
    id: UUID
    workspace_id: UUID
    name: str
    asset_ids: set[UUID] = field(default_factory=set)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def response(self) -> AssetCollectionResponse:
        return AssetCollectionResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            name=self.name,
            asset_ids=sorted(self.asset_ids, key=str),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class InMemoryLocalizedPublishingRepository:
    def __init__(self) -> None:
        self.packages: dict[UUID, LocalizedPublishPackageRecord] = {}
        self.asset_items: dict[UUID, AssetLibraryItemRecord] = {}
        self.collections: dict[UUID, AssetCollectionRecord] = {}
        self._lock = RLock()

    def create_package(self, **values: Any) -> LocalizedPublishPackageRecord:
        record = LocalizedPublishPackageRecord(id=uuid4(), **values)
        self.packages[record.id] = record
        return record

    def get_package(
        self, *, package_id: UUID, workspace_id: UUID, project_id: UUID | None = None
    ) -> LocalizedPublishPackageRecord:
        record = self.packages.get(package_id)
        if (
            record is None
            or record.workspace_id != workspace_id
            or (project_id is not None and record.project_id != project_id)
        ):
            raise ApiError(
                404,
                "localized_publish_package_not_found",
                "Localized publish package was not found.",
            )
        return record

    def list_packages(
        self, *, workspace_id: UUID, project_id: UUID, limit: int = 100
    ) -> list[LocalizedPublishPackageRecord]:
        return sorted(
            [
                package
                for package in self.packages.values()
                if package.workspace_id == workspace_id and package.project_id == project_id
            ],
            key=lambda package: package.updated_at,
            reverse=True,
        )[:limit]

    def update_package(
        self, *, package: LocalizedPublishPackageRecord, **values: Any
    ) -> LocalizedPublishPackageRecord:
        for key, value in values.items():
            if value is not None:
                setattr(package, key, value)
        package.revision += 1
        package.updated_at = datetime.now(UTC)
        return package

    def upsert_asset_item(
        self,
        *,
        workspace_id: UUID,
        kind: AssetLibraryKind,
        name: str,
        description: str = "",
        source_project_id: UUID | None = None,
        source_asset_id: UUID | None = None,
        object_reference_id: UUID | None = None,
        media_state: AssetLibraryMediaState = AssetLibraryMediaState.NOT_APPLICABLE,
        quarantine_reason: str | None = None,
        media_mime_type: str | None = None,
        media_width: int | None = None,
        media_height: int | None = None,
        media_duration_ms: int | None = None,
        recipe: AssetLibraryRecipe | None = None,
        source_key: str | None = None,
    ) -> AssetLibraryItemRecord:
        with self._lock:
            if source_key:
                existing = next(
                    (
                        item
                        for item in self.asset_items.values()
                        if item.workspace_id == workspace_id and item.source_key == source_key
                    ),
                    None,
                )
                if existing is not None:
                    existing.name = name
                    existing.description = description
                    existing.source_project_id = source_project_id
                    existing.source_asset_id = source_asset_id
                    existing.object_reference_id = object_reference_id
                    existing.media_state = media_state
                    existing.quarantine_reason = quarantine_reason
                    existing.media_mime_type = media_mime_type
                    existing.media_width = media_width
                    existing.media_height = media_height
                    existing.media_duration_ms = media_duration_ms
                    existing.recipe = recipe
                    existing.updated_at = datetime.now(UTC)
                    return existing
            record = AssetLibraryItemRecord(
                id=uuid4(),
                workspace_id=workspace_id,
                kind=kind,
                name=name,
                description=description,
                source_project_id=source_project_id,
                source_asset_id=source_asset_id,
                object_reference_id=object_reference_id,
                media_state=media_state,
                quarantine_reason=quarantine_reason,
                media_mime_type=media_mime_type,
                media_width=media_width,
                media_height=media_height,
                media_duration_ms=media_duration_ms,
                recipe=recipe,
                source_key=source_key,
            )
            self.asset_items[record.id] = record
            return record

    def get_asset_item(self, *, item_id: UUID, workspace_id: UUID) -> AssetLibraryItemRecord:
        item = self.asset_items.get(item_id)
        if item is None or item.workspace_id != workspace_id:
            raise ApiError(404, "asset_library_item_not_found", "Asset library item was not found.")
        return item

    def list_asset_items(
        self, *, workspace_id: UUID, query: str | None = None, limit: int = 100
    ) -> list[AssetLibraryItemRecord]:
        normalized = (query or "").strip().lower()
        records = [item for item in self.asset_items.values() if item.workspace_id == workspace_id]
        if normalized:
            records = [
                item
                for item in records
                if normalized in f"{item.name} {item.description} {item.kind.value}".lower()
            ]
        return sorted(
            records,
            key=lambda item: (not item.is_favorite, -item.updated_at.timestamp()),
        )[:limit]

    def set_favorite(
        self, *, item: AssetLibraryItemRecord, is_favorite: bool
    ) -> AssetLibraryItemRecord:
        item.is_favorite = is_favorite
        item.updated_at = datetime.now(UTC)
        return item

    def quarantine_asset_item(
        self, *, item: AssetLibraryItemRecord, reason: str
    ) -> AssetLibraryItemRecord:
        item.object_reference_id = None
        item.media_state = AssetLibraryMediaState.QUARANTINED
        item.quarantine_reason = reason[:160]
        item.updated_at = datetime.now(UTC)
        return item

    def delete_asset_item(self, *, item: AssetLibraryItemRecord) -> None:
        self.asset_items.pop(item.id, None)

    def create_collection(self, *, workspace_id: UUID, name: str) -> AssetCollectionRecord:
        record = AssetCollectionRecord(id=uuid4(), workspace_id=workspace_id, name=name)
        self.collections[record.id] = record
        return record

    def list_collections(
        self, *, workspace_id: UUID, limit: int = 100
    ) -> list[AssetCollectionRecord]:
        return sorted(
            [
                collection
                for collection in self.collections.values()
                if collection.workspace_id == workspace_id
            ],
            key=lambda collection: collection.updated_at,
            reverse=True,
        )[:limit]

    def add_collection_item(
        self, *, collection_id: UUID, item_id: UUID, workspace_id: UUID
    ) -> AssetCollectionRecord:
        collection = self.collections.get(collection_id)
        if collection is None or collection.workspace_id != workspace_id:
            raise ApiError(404, "asset_collection_not_found", "Asset collection was not found.")
        self.get_asset_item(item_id=item_id, workspace_id=workspace_id)
        collection.asset_ids.add(item_id)
        collection.updated_at = datetime.now(UTC)
        return collection


class SupabaseLocalizedPublishingRepository(InMemoryLocalizedPublishingRepository):
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: object | None = None,
    ) -> None:
        super().__init__()
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="localized_publishing",
            metrics_registry=metrics_registry,
        )

    def create_package(self, **values: Any) -> LocalizedPublishPackageRecord:
        response = self.client.request(
            "POST",
            "/localized_publish_packages",
            params={"select": PACKAGE_COLUMNS},
            json=self._json_safe(values),
        )
        self._raise(response)
        return self._package_from_row(response.json()[0])

    def get_package(
        self, *, package_id: UUID, workspace_id: UUID, project_id: UUID | None = None
    ) -> LocalizedPublishPackageRecord:
        params = {
            "id": f"eq.{package_id}",
            "workspace_id": f"eq.{workspace_id}",
            "select": PACKAGE_COLUMNS,
            "limit": "1",
        }
        if project_id is not None:
            params["project_id"] = f"eq.{project_id}"
        response = self.client.request("GET", "/localized_publish_packages", params=params)
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(
                404,
                "localized_publish_package_not_found",
                "Localized publish package was not found.",
            )
        return self._package_from_row(rows[0])

    def list_packages(
        self, *, workspace_id: UUID, project_id: UUID, limit: int = 100
    ) -> list[LocalizedPublishPackageRecord]:
        response = self.client.request(
            "GET",
            "/localized_publish_packages",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": PACKAGE_COLUMNS,
                "order": "updated_at.desc",
                "limit": str(limit),
            },
        )
        self._raise(response)
        return [self._package_from_row(row) for row in response.json()]

    def update_package(
        self, *, package: LocalizedPublishPackageRecord, **values: Any
    ) -> LocalizedPublishPackageRecord:
        payload = {key: value for key, value in values.items() if value is not None}
        payload.update(
            {"revision": package.revision + 1, "updated_at": datetime.now(UTC).isoformat()}
        )
        response = self.client.request(
            "PATCH",
            "/localized_publish_packages",
            params={
                "id": f"eq.{package.id}",
                "workspace_id": f"eq.{package.workspace_id}",
                "select": PACKAGE_COLUMNS,
            },
            json=self._json_safe(payload),
        )
        self._raise(response)
        return self._package_from_row(response.json()[0])

    def upsert_asset_item(self, **values: Any) -> AssetLibraryItemRecord:
        source_key = values.get("source_key")
        existing = None
        if source_key:
            response = self.client.request(
                "GET",
                "/asset_library_items",
                params={
                    "workspace_id": f"eq.{values['workspace_id']}",
                    "source_key": f"eq.{source_key}",
                    "select": ASSET_ITEM_COLUMNS,
                    "limit": "1",
                },
            )
            self._raise(response)
            rows = response.json()
            existing = self._asset_from_row(rows[0]) if rows else None
        data = {**values, "updated_at": datetime.now(UTC).isoformat()}
        if existing is not None:
            response = self.client.request(
                "PATCH",
                "/asset_library_items",
                params={"id": f"eq.{existing.id}", "select": ASSET_ITEM_COLUMNS},
                json=self._json_safe(data),
            )
        else:
            response = self.client.request(
                "POST",
                "/asset_library_items",
                params={"select": ASSET_ITEM_COLUMNS},
                json=self._json_safe(data),
            )
        self._raise(response)
        return self._asset_from_row(response.json()[0])

    def get_asset_item(self, *, item_id: UUID, workspace_id: UUID) -> AssetLibraryItemRecord:
        response = self.client.request(
            "GET",
            "/asset_library_items",
            params={
                "id": f"eq.{item_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": ASSET_ITEM_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "asset_library_item_not_found", "Asset library item was not found.")
        return self._asset_from_row(rows[0])

    def list_asset_items(
        self, *, workspace_id: UUID, query: str | None = None, limit: int = 100
    ) -> list[AssetLibraryItemRecord]:
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "select": ASSET_ITEM_COLUMNS,
            "order": "is_favorite.desc,updated_at.desc",
            "limit": str(limit),
        }
        if query and query.strip():
            params["search_text"] = f"ilike.*{query.strip()}*"
        response = self.client.request("GET", "/asset_library_items", params=params)
        self._raise(response)
        return [self._asset_from_row(row) for row in response.json()]

    def set_favorite(
        self, *, item: AssetLibraryItemRecord, is_favorite: bool
    ) -> AssetLibraryItemRecord:
        response = self.client.request(
            "PATCH",
            "/asset_library_items",
            params={
                "id": f"eq.{item.id}",
                "workspace_id": f"eq.{item.workspace_id}",
                "select": ASSET_ITEM_COLUMNS,
            },
            json={"is_favorite": is_favorite, "updated_at": datetime.now(UTC).isoformat()},
        )
        self._raise(response)
        return self._asset_from_row(response.json()[0])

    def quarantine_asset_item(
        self, *, item: AssetLibraryItemRecord, reason: str
    ) -> AssetLibraryItemRecord:
        response = self.client.request(
            "PATCH",
            "/asset_library_items",
            params={
                "id": f"eq.{item.id}",
                "workspace_id": f"eq.{item.workspace_id}",
                "select": ASSET_ITEM_COLUMNS,
            },
            json={
                "object_reference_id": None,
                "media_state": AssetLibraryMediaState.QUARANTINED.value,
                "quarantine_reason": reason[:160],
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._raise(response)
        return self._asset_from_row(response.json()[0])

    def delete_asset_item(self, *, item: AssetLibraryItemRecord) -> None:
        response = self.client.request(
            "DELETE",
            "/asset_library_items",
            params={
                "id": f"eq.{item.id}",
                "workspace_id": f"eq.{item.workspace_id}",
            },
            headers={"prefer": "return=minimal"},
        )
        self._raise(response)

    def create_collection(self, *, workspace_id: UUID, name: str) -> AssetCollectionRecord:
        response = self.client.request(
            "POST",
            "/asset_collections",
            params={"select": ASSET_COLLECTION_COLUMNS},
            json={"workspace_id": str(workspace_id), "name": name},
        )
        self._raise(response)
        return self._collection_from_row(response.json()[0], asset_ids=[])

    def list_collections(
        self, *, workspace_id: UUID, limit: int = 100
    ) -> list[AssetCollectionRecord]:
        response = self.client.request(
            "GET",
            "/asset_collections",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": ASSET_COLLECTION_COLUMNS,
                "order": "updated_at.desc",
                "limit": str(limit),
            },
        )
        self._raise(response)
        return [
            self._collection_from_row(
                row, asset_ids=self._collection_item_ids(UUID(row["id"]), workspace_id)
            )
            for row in response.json()
        ]

    def add_collection_item(
        self, *, collection_id: UUID, item_id: UUID, workspace_id: UUID
    ) -> AssetCollectionRecord:
        self.get_asset_item(item_id=item_id, workspace_id=workspace_id)
        collection = next(
            (
                item
                for item in self.list_collections(workspace_id=workspace_id)
                if item.id == collection_id
            ),
            None,
        )
        if collection is None:
            raise ApiError(404, "asset_collection_not_found", "Asset collection was not found.")
        response = self.client.request(
            "POST",
            "/asset_collection_items",
            headers={"prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "workspace_id": str(workspace_id),
                "collection_id": str(collection_id),
                "asset_id": str(item_id),
            },
        )
        self._raise(response)
        touch = self.client.request(
            "PATCH",
            "/asset_collections",
            params={"id": f"eq.{collection_id}"},
            json={"updated_at": datetime.now(UTC).isoformat()},
        )
        self._raise(touch)
        collection.asset_ids.add(item_id)
        collection.updated_at = datetime.now(UTC)
        return collection

    def _collection_item_ids(self, collection_id: UUID, workspace_id: UUID) -> set[UUID]:
        response = self.client.request(
            "GET",
            "/asset_collection_items",
            params={
                "collection_id": f"eq.{collection_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "asset_id",
                "limit": "500",
            },
        )
        self._raise(response)
        return {UUID(row["asset_id"]) for row in response.json()}

    @staticmethod
    def _package_from_row(row: dict[str, Any]) -> LocalizedPublishPackageRecord:
        return LocalizedPublishPackageRecord(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            source_caption_track_id=UUID(row["source_caption_track_id"]),
            caption_track_id=UUID(row["caption_track_id"]),
            edit_plan_id=UUID(row["edit_plan_id"]) if row.get("edit_plan_id") else None,
            language=PublishLanguage(row["language"]),
            review_status=LocalizedReviewStatus(row["review_status"]),
            overlay_translations=[
                LocalizedOverlayTranslation.model_validate(item)
                for item in row.get("overlay_translations") or []
            ],
            title_suggestions=list(row.get("title_suggestions") or []),
            description_suggestions=list(row.get("description_suggestions") or []),
            selected_title=row.get("selected_title"),
            selected_description=row.get("selected_description"),
            revision=int(row.get("revision") or 1),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _asset_from_row(row: dict[str, Any]) -> AssetLibraryItemRecord:
        kind = AssetLibraryKind(row["kind"])
        recipe = row.get("recipe")
        parsed_recipe: AssetLibraryRecipe | None = None
        if isinstance(recipe, dict):
            if recipe.get("recipe_type") == "style":
                parsed_recipe = AssetLibraryStyleRecipe.model_validate(recipe)
            elif recipe.get("recipe_type") == "clip":
                parsed_recipe = AssetLibraryClipRecipe.model_validate(recipe)
        return AssetLibraryItemRecord(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            kind=kind,
            name=row["name"],
            description=row.get("description") or "",
            source_project_id=UUID(row["source_project_id"])
            if row.get("source_project_id")
            else None,
            source_asset_id=UUID(row["source_asset_id"]) if row.get("source_asset_id") else None,
            object_reference_id=(
                UUID(row["object_reference_id"]) if row.get("object_reference_id") else None
            ),
            media_state=AssetLibraryMediaState(
                row.get("media_state") or AssetLibraryMediaState.QUARANTINED.value
            ),
            quarantine_reason=row.get("quarantine_reason"),
            media_mime_type=row.get("media_mime_type"),
            media_width=int(row["media_width"]) if row.get("media_width") is not None else None,
            media_height=int(row["media_height"]) if row.get("media_height") is not None else None,
            media_duration_ms=(
                int(row["media_duration_ms"])
                if row.get("media_duration_ms") is not None
                else None
            ),
            recipe=parsed_recipe,
            source_key=row.get("source_key"),
            is_favorite=bool(row.get("is_favorite")),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _collection_from_row(row: dict[str, Any], *, asset_ids: set[UUID]) -> AssetCollectionRecord:
        return AssetCollectionRecord(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            name=row["name"],
            asset_ids=asset_ids,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, UUID):
            return str(value)
        if hasattr(value, "value"):
            return value.value
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {
                key: SupabaseLocalizedPublishingRepository._json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, list | tuple | set):
            return [SupabaseLocalizedPublishingRepository._json_safe(item) for item in value]
        return value

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise ApiError(
                502,
                "localized_publishing_storage_error",
                "Publishing workflow storage is unavailable.",
            )
