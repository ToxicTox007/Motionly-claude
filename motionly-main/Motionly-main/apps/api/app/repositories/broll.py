from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.broll import (
    BROLL_SCHEMA_VERSION,
    BrollSlotResponse,
    BrollSlotStatus,
    StockOrientation,
    StockProviderKey,
    StockVideoAssetResponse,
)


@dataclass
class StockAssetRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    provider: StockProviderKey
    provider_asset_id: str
    title: str
    thumbnail_url: str | None
    preview_video_url: str | None
    download_url: str | None
    license: str
    width: int
    height: int
    duration_ms: int
    orientation: StockOrientation
    score: float
    raw_metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> StockVideoAssetResponse:
        return StockVideoAssetResponse(**self.__dict__)


@dataclass
class BrollSlotRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    edit_plan_id: UUID
    version: int
    schema_version: int
    status: BrollSlotStatus
    start_ms: int
    end_ms: int
    keywords: list[str]
    intent: str
    density: str
    priority: float
    selected_asset_id: UUID | None
    candidate_asset_ids: list[UUID]
    crossfade_ms: int
    provider: StockProviderKey
    score: float
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self, assets: dict[UUID, StockAssetRecord]) -> BrollSlotResponse:
        candidates = [
            assets[asset_id].to_response()
            for asset_id in self.candidate_asset_ids
            if asset_id in assets
        ]
        selected = (
            assets.get(self.selected_asset_id).to_response()
            if self.selected_asset_id in assets
            else None
        )
        return BrollSlotResponse(
            **self.__dict__,
            selected_asset=selected,
            candidates=candidates,
        )


class InMemoryBrollRepository:
    def __init__(self, plan_repository: InMemoryPlanRepository) -> None:
        self.plan_repository = plan_repository
        self.assets: dict[UUID, StockAssetRecord] = {}
        self.slots: dict[UUID, BrollSlotRecord] = {}
        self.cache_keys: dict[tuple[UUID, UUID, str], int] = {}

    def get_project_for_workspace(self, *, project_id: UUID, workspace_id: UUID) -> None:
        self.plan_repository.get_project_for_workspace(
            project_id=project_id, workspace_id=workspace_id
        )

    def active_plan(self, *, project_id: UUID, workspace_id: UUID):
        plans = self.plan_repository.list_plans(project_id=project_id, workspace_id=workspace_id)
        plan = next((item for item in plans if item.is_active), None)
        if not plan:
            raise ApiError(
                404, "edit_plan_not_found", "Create an edit plan before generating B-roll."
            )
        return plan

    def find_cached_slots(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        cache_key: str,
    ) -> list[BrollSlotRecord]:
        version = self.cache_keys.get((workspace_id, project_id, cache_key))
        if version is None:
            return []
        return self.list_slots(project_id=project_id, workspace_id=workspace_id, version=version)

    def upsert_asset(self, **values: object) -> StockAssetRecord:
        existing = next(
            (
                asset
                for asset in self.assets.values()
                if asset.workspace_id == values["workspace_id"]
                and asset.project_id == values["project_id"]
                and asset.provider == values["provider"]
                and asset.provider_asset_id == values["provider_asset_id"]
            ),
            None,
        )
        if existing:
            existing.score = float(values["score"])
            existing.raw_metadata = values.get("raw_metadata", {})  # type: ignore[assignment]
            return existing
        asset = StockAssetRecord(id=uuid4(), **values)
        self.assets[asset.id] = asset
        return asset

    def create_slot_set(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        cache_key: str,
        slots: list[dict[str, object]],
    ) -> list[BrollSlotRecord]:
        version = self._next_version(project_id=project_id, workspace_id=workspace_id)
        records = [
            BrollSlotRecord(
                id=uuid4(), version=version, schema_version=BROLL_SCHEMA_VERSION, **slot
            )
            for slot in slots
        ]
        for record in records:
            self.slots[record.id] = record
        self.cache_keys[(workspace_id, project_id, cache_key)] = version
        return sorted(records, key=lambda slot: slot.start_ms)

    def list_slots(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        version: int | None = None,
    ) -> list[BrollSlotRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        records = [
            slot
            for slot in self.slots.values()
            if slot.project_id == project_id and slot.workspace_id == workspace_id
        ]
        if version is None and records:
            version = max(slot.version for slot in records)
        if version is not None:
            records = [slot for slot in records if slot.version == version]
        return sorted(records, key=lambda slot: slot.start_ms)

    def get_slot(self, *, project_id: UUID, workspace_id: UUID, slot_id: UUID) -> BrollSlotRecord:
        slot = self.slots.get(slot_id)
        if slot is None or slot.project_id != project_id:
            raise ApiError(404, "broll_slot_not_found", "B-roll slot was not found.")
        if slot.workspace_id != workspace_id:
            raise ApiError(
                403, "workspace_access_denied", "You do not have access to this B-roll slot."
            )
        return slot

    def get_asset(
        self, *, project_id: UUID, workspace_id: UUID, asset_id: UUID
    ) -> StockAssetRecord:
        asset = self.assets.get(asset_id)
        if asset is None or asset.project_id != project_id:
            raise ApiError(404, "stock_asset_not_found", "Stock asset was not found.")
        if asset.workspace_id != workspace_id:
            raise ApiError(403, "workspace_access_denied", "You do not have access to this asset.")
        return asset

    def list_library_assets(
        self, *, project_id: UUID, workspace_id: UUID
    ) -> list[StockAssetRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        return sorted(
            [
                asset
                for asset in self.assets.values()
                if asset.project_id == project_id
                and asset.workspace_id == workspace_id
                and asset.provider == StockProviderKey.USER_LIBRARY
            ],
            key=lambda asset: asset.created_at,
            reverse=True,
        )

    def mark_library_asset_ready(
        self, *, asset: StockAssetRecord, download_url: str
    ) -> StockAssetRecord:
        asset.download_url = download_url
        asset.preview_video_url = download_url
        asset.raw_metadata = {**asset.raw_metadata, "status": "ready"}
        return asset

    def delete_library_asset(
        self, *, project_id: UUID, workspace_id: UUID, asset_id: UUID
    ) -> None:
        asset = self.get_asset(
            project_id=project_id, workspace_id=workspace_id, asset_id=asset_id
        )
        if asset.provider != StockProviderKey.USER_LIBRARY:
            raise ApiError(409, "library_asset_required", "Only library assets can be deleted.")
        self.assets.pop(asset_id, None)

    def update_slot_selection(
        self,
        *,
        slot: BrollSlotRecord,
        asset_id: UUID | None,
        disabled: bool,
    ) -> BrollSlotRecord:
        if disabled:
            slot.status = BrollSlotStatus.DISABLED
            slot.selected_asset_id = None
        else:
            if asset_id is None:
                raise ApiError(422, "broll_asset_required", "Choose an asset or disable this slot.")
            if asset_id not in slot.candidate_asset_ids:
                raise ApiError(
                    409, "broll_asset_not_candidate", "Choose one of this slot's candidate assets."
                )
            slot.status = BrollSlotStatus.READY
            slot.selected_asset_id = asset_id
            slot.score = max(slot.score, 1)
        slot.updated_at = datetime.now(UTC)
        return slot

    def update_slot_region(
        self,
        *,
        slot: BrollSlotRecord,
        start_ms: int,
        end_ms: int,
        disabled: bool | None = None,
    ) -> BrollSlotRecord:
        slot.start_ms = start_ms
        slot.end_ms = end_ms
        if disabled is not None:
            slot.status = BrollSlotStatus.DISABLED if disabled else BrollSlotStatus.READY
        slot.updated_at = datetime.now(UTC)
        return slot

    def add_slot_candidates(
        self,
        *,
        slot: BrollSlotRecord,
        asset_ids: list[UUID],
    ) -> BrollSlotRecord:
        for asset_id in asset_ids:
            if asset_id not in slot.candidate_asset_ids:
                slot.candidate_asset_ids.append(asset_id)
        slot.updated_at = datetime.now(UTC)
        return slot

    def response_for_slot(self, slot: BrollSlotRecord) -> BrollSlotResponse:
        return slot.to_response(self.assets)

    def _next_version(self, *, project_id: UUID, workspace_id: UUID) -> int:
        versions = [
            slot.version
            for slot in self.slots.values()
            if slot.project_id == project_id and slot.workspace_id == workspace_id
        ]
        return (max(versions) if versions else 0) + 1


class SupabaseBrollRepository(InMemoryBrollRepository):
    def __init__(
        self,
        *,
        plan_repository: InMemoryPlanRepository,
        supabase_url: str,
        service_role_key: str,
    ) -> None:
        super().__init__(plan_repository)
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def upsert_asset(self, **values: object) -> StockAssetRecord:
        response = httpx.post(
            f"{self.rest_url}/stock_assets",
            headers={**self.headers, "prefer": "resolution=merge-duplicates,return=representation"},
            json=self._json_safe(values),
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._asset_from_row(response.json()[0])

    def create_slot_set(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        cache_key: str,
        slots: list[dict[str, object]],
    ) -> list[BrollSlotRecord]:
        existing = self.list_slots(project_id=project_id, workspace_id=workspace_id, version=None)
        version = (max([slot.version for slot in existing], default=0)) + 1
        response = httpx.post(
            f"{self.rest_url}/broll_slots",
            headers=self.headers,
            json=[
                self._json_safe(
                    {
                        **slot,
                        "version": version,
                        "schema_version": BROLL_SCHEMA_VERSION,
                        "cache_key": cache_key,
                    }
                )
                for slot in slots
            ],
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._slot_from_row(row) for row in response.json()]

    def find_cached_slots(
        self, *, project_id: UUID, workspace_id: UUID, cache_key: str
    ) -> list[BrollSlotRecord]:
        response = httpx.get(
            f"{self.rest_url}/broll_slots",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "cache_key": f"eq.{cache_key}",
                "select": "*",
                "order": "version.desc,start_ms.asc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            return []
        latest = max(int(row["version"]) for row in rows)
        return [self._slot_from_row(row) for row in rows if int(row["version"]) == latest]

    def list_slots(
        self, *, project_id: UUID, workspace_id: UUID, version: int | None = None
    ) -> list[BrollSlotRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "project_id": f"eq.{project_id}",
            "select": "*",
            "order": "version.desc,start_ms.asc",
        }
        if version is not None:
            params["version"] = f"eq.{version}"
        response = httpx.get(
            f"{self.rest_url}/broll_slots", headers=self.headers, params=params, timeout=10
        )
        self._raise_for_supabase(response)
        records = [self._slot_from_row(row) for row in response.json()]
        if version is None and records:
            latest = max(slot.version for slot in records)
            records = [slot for slot in records if slot.version == latest]
        return records

    def get_slot(self, *, project_id: UUID, workspace_id: UUID, slot_id: UUID) -> BrollSlotRecord:
        response = httpx.get(
            f"{self.rest_url}/broll_slots",
            headers=self.headers,
            params={
                "id": f"eq.{slot_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "broll_slot_not_found", "B-roll slot was not found.")
        return self._slot_from_row(rows[0])

    def get_asset(
        self, *, project_id: UUID, workspace_id: UUID, asset_id: UUID
    ) -> StockAssetRecord:
        response = httpx.get(
            f"{self.rest_url}/stock_assets",
            headers=self.headers,
            params={
                "id": f"eq.{asset_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "stock_asset_not_found", "Stock asset was not found.")
        return self._asset_from_row(rows[0])

    def list_library_assets(
        self, *, project_id: UUID, workspace_id: UUID
    ) -> list[StockAssetRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        response = httpx.get(
            f"{self.rest_url}/stock_assets",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "provider": f"eq.{StockProviderKey.USER_LIBRARY.value}",
                "select": "*",
                "order": "created_at.desc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._asset_from_row(row) for row in response.json()]

    def mark_library_asset_ready(
        self, *, asset: StockAssetRecord, download_url: str
    ) -> StockAssetRecord:
        response = httpx.patch(
            f"{self.rest_url}/stock_assets",
            headers=self.headers,
            params={"id": f"eq.{asset.id}", "select": "*"},
            json={
                "download_url": download_url,
                "preview_video_url": download_url,
                "raw_metadata": {**asset.raw_metadata, "status": "ready"},
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._asset_from_row(response.json()[0])

    def delete_library_asset(
        self, *, project_id: UUID, workspace_id: UUID, asset_id: UUID
    ) -> None:
        asset = self.get_asset(project_id=project_id, workspace_id=workspace_id, asset_id=asset_id)
        if asset.provider != StockProviderKey.USER_LIBRARY:
            raise ApiError(409, "library_asset_required", "Only library assets can be deleted.")
        response = httpx.delete(
            f"{self.rest_url}/stock_assets",
            headers=self.headers,
            params={"id": f"eq.{asset_id}", "workspace_id": f"eq.{workspace_id}"},
            timeout=10,
        )
        self._raise_for_supabase(response)

    def update_slot_selection(
        self, *, slot: BrollSlotRecord, asset_id: UUID | None, disabled: bool
    ) -> BrollSlotRecord:
        status = BrollSlotStatus.DISABLED if disabled else BrollSlotStatus.READY
        if not disabled and asset_id not in slot.candidate_asset_ids:
            raise ApiError(
                409, "broll_asset_not_candidate", "Choose one of this slot's candidate assets."
            )
        response = httpx.patch(
            f"{self.rest_url}/broll_slots",
            headers=self.headers,
            params={"id": f"eq.{slot.id}", "select": "*"},
            json={
                "status": status.value,
                "selected_asset_id": str(asset_id) if asset_id else None,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._slot_from_row(response.json()[0])

    def update_slot_region(
        self,
        *,
        slot: BrollSlotRecord,
        start_ms: int,
        end_ms: int,
        disabled: bool | None = None,
    ) -> BrollSlotRecord:
        payload: dict[str, object] = {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if disabled is not None:
            payload["status"] = (
                BrollSlotStatus.DISABLED if disabled else BrollSlotStatus.READY
            ).value
        response = httpx.patch(
            f"{self.rest_url}/broll_slots",
            headers=self.headers,
            params={"id": f"eq.{slot.id}", "select": "*"},
            json=payload,
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._slot_from_row(response.json()[0])

    def add_slot_candidates(
        self,
        *,
        slot: BrollSlotRecord,
        asset_ids: list[UUID],
    ) -> BrollSlotRecord:
        candidate_asset_ids = [*slot.candidate_asset_ids]
        for asset_id in asset_ids:
            if asset_id not in candidate_asset_ids:
                candidate_asset_ids.append(asset_id)
        response = httpx.patch(
            f"{self.rest_url}/broll_slots",
            headers=self.headers,
            params={"id": f"eq.{slot.id}", "select": "*"},
            json={
                "candidate_asset_ids": [str(asset_id) for asset_id in candidate_asset_ids],
                "updated_at": datetime.now(UTC).isoformat(),
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._slot_from_row(response.json()[0])

    def response_for_slot(self, slot: BrollSlotRecord) -> BrollSlotResponse:
        asset_ids = [*slot.candidate_asset_ids]
        if slot.selected_asset_id and slot.selected_asset_id not in asset_ids:
            asset_ids.append(slot.selected_asset_id)
        for asset_id in asset_ids:
            if asset_id not in self.assets:
                self.assets[asset_id] = self.get_asset(
                    project_id=slot.project_id, workspace_id=slot.workspace_id, asset_id=asset_id
                )
        return slot.to_response(self.assets)

    def _slot_from_row(self, row: dict[str, object]) -> BrollSlotRecord:
        return BrollSlotRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            edit_plan_id=UUID(str(row["edit_plan_id"])),
            version=int(row["version"]),
            schema_version=int(row["schema_version"]),
            status=BrollSlotStatus(str(row["status"])),
            start_ms=int(row["start_ms"]),
            end_ms=int(row["end_ms"]),
            keywords=[str(item) for item in row.get("keywords", [])],
            intent=str(row["intent"]),
            density=str(row["density"]),
            priority=float(row["priority"]),
            selected_asset_id=UUID(str(row["selected_asset_id"]))
            if row.get("selected_asset_id")
            else None,
            candidate_asset_ids=[UUID(str(item)) for item in row.get("candidate_asset_ids", [])],
            crossfade_ms=int(row["crossfade_ms"]),
            provider=StockProviderKey(str(row["provider"])),
            score=float(row["score"]),
            error_code=str(row["error_code"]) if row.get("error_code") else None,
            error_message=str(row["error_message"]) if row.get("error_message") else None,
            metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    def _asset_from_row(self, row: dict[str, object]) -> StockAssetRecord:
        return StockAssetRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            provider=StockProviderKey(str(row["provider"])),
            provider_asset_id=str(row["provider_asset_id"]),
            title=str(row["title"]),
            thumbnail_url=str(row["thumbnail_url"]) if row.get("thumbnail_url") else None,
            preview_video_url=str(row["preview_video_url"])
            if row.get("preview_video_url")
            else None,
            download_url=str(row["download_url"]) if row.get("download_url") else None,
            license=str(row["license"]),
            width=int(row["width"]),
            height=int(row["height"]),
            duration_ms=int(row["duration_ms"]),
            orientation=StockOrientation(str(row["orientation"])),
            score=float(row["score"]),
            raw_metadata=row.get("raw_metadata")
            if isinstance(row.get("raw_metadata"), dict)
            else {},
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
        )

    def _json_safe(self, values: dict[str, object]) -> dict[str, object]:
        return {key: self._json_safe_value(value) for key, value in values.items()}

    def _json_safe_value(self, value: object) -> object:
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, list):
            return [self._json_safe_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._json_safe_value(item) for key, item in value.items()}
        if hasattr(value, "value"):
            return value.value
        return value

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "supabase_request_failed", "B-roll data could not be saved.")
