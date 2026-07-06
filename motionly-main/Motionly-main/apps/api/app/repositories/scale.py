from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.scale import ScaleResourceResponse, ScaleResourceStatus
from app.services.supabase import SupabaseRestClient

SCALE_RESOURCE_COLUMNS = (
    "id,workspace_id,project_id,kind,name,status,payload,result,revision,error_message,"
    "created_at,updated_at"
)


@dataclass
class ScaleResourceRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    kind: str
    name: str
    status: ScaleResourceStatus
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    revision: int = 1
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> ScaleResourceResponse:
        return ScaleResourceResponse(**self.__dict__)


class InMemoryScaleRepository:
    def __init__(self) -> None:
        self.resources: dict[UUID, ScaleResourceRecord] = {}

    def create(self, **values: Any) -> ScaleResourceRecord:
        record = ScaleResourceRecord(id=uuid4(), **values)
        self.resources[record.id] = record
        return record

    def get(self, *, resource_id: UUID, workspace_id: UUID) -> ScaleResourceRecord:
        record = self.resources.get(resource_id)
        if not record:
            raise ApiError(404, "scale_resource_not_found", "Phase 5 resource was not found.")
        if record.workspace_id != workspace_id:
            raise ApiError(403, "workspace_access_denied", "Resource is not in this workspace.")
        return record

    def list(
        self,
        *,
        workspace_id: UUID,
        kind: str | None = None,
        project_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ScaleResourceRecord]:
        records = [
            item
            for item in self.resources.values()
            if item.workspace_id == workspace_id
            and (kind is None or item.kind == kind)
            and (project_id is None or item.project_id == project_id)
        ]
        return sorted(records, key=lambda item: item.updated_at, reverse=True)[:limit]

    def latest(
        self,
        *,
        workspace_id: UUID,
        kind: str,
        project_id: UUID | None = None,
        status: ScaleResourceStatus | None = None,
    ) -> ScaleResourceRecord | None:
        records = self.list(
            workspace_id=workspace_id,
            kind=kind,
            project_id=project_id,
            limit=100,
        )
        return next((item for item in records if status is None or item.status == status), None)

    def update(self, *, record: ScaleResourceRecord, **values: Any) -> ScaleResourceRecord:
        for key, value in values.items():
            setattr(record, key, value)
        record.updated_at = datetime.now(UTC)
        return record

    def update_if_revision(
        self, *, record: ScaleResourceRecord, expected_revision: int, **values: Any
    ) -> ScaleResourceRecord:
        if record.revision != expected_revision:
            raise ApiError(409, "collaboration_revision_conflict", "Reload the shared edit.")
        return self.update(record=record, **values)

    def delete(self, *, resource_id: UUID, workspace_id: UUID) -> None:
        self.get(resource_id=resource_id, workspace_id=workspace_id)
        del self.resources[resource_id]


class SupabaseScaleRepository(InMemoryScaleRepository):
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
            repository="scale",
            metrics_registry=metrics_registry,
            timeout_seconds=15,
        )

    def create(self, **values: Any) -> ScaleResourceRecord:
        response = self.client.request(
            "POST",
            "/phase5_resources",
            params={"select": SCALE_RESOURCE_COLUMNS},
            json=self._json(values),
        )
        self._raise(response)
        return self._from_row(response.json()[0])

    async def create_async(self, **values: Any) -> ScaleResourceRecord:
        response = await self.client.async_request(
            "POST",
            "/phase5_resources",
            params={"select": SCALE_RESOURCE_COLUMNS},
            json=self._json(values),
        )
        self._raise(response)
        return self._from_row(response.json()[0])

    def get(self, *, resource_id: UUID, workspace_id: UUID) -> ScaleResourceRecord:
        response = self.client.request(
            "GET",
            "/phase5_resources",
            params={
                "id": f"eq.{resource_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": SCALE_RESOURCE_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "scale_resource_not_found", "Phase 5 resource was not found.")
        return self._from_row(rows[0])

    async def get_async(self, *, resource_id: UUID, workspace_id: UUID) -> ScaleResourceRecord:
        response = await self.client.async_request(
            "GET",
            "/phase5_resources",
            params={
                "id": f"eq.{resource_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": SCALE_RESOURCE_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "scale_resource_not_found", "Phase 5 resource was not found.")
        return self._from_row(rows[0])

    def list(
        self,
        *,
        workspace_id: UUID,
        kind: str | None = None,
        project_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ScaleResourceRecord]:
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "select": SCALE_RESOURCE_COLUMNS,
            "order": "updated_at.desc",
            "limit": str(limit),
        }
        if kind:
            params["kind"] = f"eq.{kind}"
        if project_id:
            params["project_id"] = f"eq.{project_id}"
        response = self.client.request("GET", "/phase5_resources", params=params)
        self._raise(response)
        return [self._from_row(row) for row in response.json()]

    async def list_async(
        self,
        *,
        workspace_id: UUID,
        kind: str | None = None,
        project_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ScaleResourceRecord]:
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "select": SCALE_RESOURCE_COLUMNS,
            "order": "updated_at.desc",
            "limit": str(limit),
        }
        if kind:
            params["kind"] = f"eq.{kind}"
        if project_id:
            params["project_id"] = f"eq.{project_id}"
        response = await self.client.async_request("GET", "/phase5_resources", params=params)
        self._raise(response)
        return [self._from_row(row) for row in response.json()]

    async def latest_async(
        self,
        *,
        workspace_id: UUID,
        kind: str,
        project_id: UUID | None = None,
        status: ScaleResourceStatus | None = None,
    ) -> ScaleResourceRecord | None:
        records = await self.list_async(
            workspace_id=workspace_id,
            kind=kind,
            project_id=project_id,
            limit=100,
        )
        return next((item for item in records if status is None or item.status == status), None)

    def update(self, *, record: ScaleResourceRecord, **values: Any) -> ScaleResourceRecord:
        response = self.client.request(
            "PATCH",
            "/phase5_resources",
            params={"id": f"eq.{record.id}", "select": SCALE_RESOURCE_COLUMNS},
            json=self._json({**values, "updated_at": datetime.now(UTC)}),
        )
        self._raise(response)
        return self._from_row(response.json()[0])

    async def update_async(
        self,
        *,
        record: ScaleResourceRecord,
        **values: Any,
    ) -> ScaleResourceRecord:
        response = await self.client.async_request(
            "PATCH",
            "/phase5_resources",
            params={"id": f"eq.{record.id}", "select": SCALE_RESOURCE_COLUMNS},
            json=self._json({**values, "updated_at": datetime.now(UTC)}),
        )
        self._raise(response)
        return self._from_row(response.json()[0])

    def update_if_revision(
        self, *, record: ScaleResourceRecord, expected_revision: int, **values: Any
    ) -> ScaleResourceRecord:
        response = self.client.request(
            "PATCH",
            "/phase5_resources",
            params={
                "id": f"eq.{record.id}",
                "revision": f"eq.{expected_revision}",
                "select": SCALE_RESOURCE_COLUMNS,
            },
            json=self._json({**values, "updated_at": datetime.now(UTC)}),
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(409, "collaboration_revision_conflict", "Reload the shared edit.")
        return self._from_row(rows[0])

    async def update_if_revision_async(
        self, *, record: ScaleResourceRecord, expected_revision: int, **values: Any
    ) -> ScaleResourceRecord:
        response = await self.client.async_request(
            "PATCH",
            "/phase5_resources",
            params={
                "id": f"eq.{record.id}",
                "revision": f"eq.{expected_revision}",
                "select": SCALE_RESOURCE_COLUMNS,
            },
            json=self._json({**values, "updated_at": datetime.now(UTC)}),
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(409, "collaboration_revision_conflict", "Reload the shared edit.")
        return self._from_row(rows[0])

    def delete(self, *, resource_id: UUID, workspace_id: UUID) -> None:
        self.get(resource_id=resource_id, workspace_id=workspace_id)
        response = self.client.request(
            "DELETE",
            "/phase5_resources",
            headers={"prefer": "return=minimal"},
            params={"id": f"eq.{resource_id}"},
        )
        self._raise(response)

    async def delete_async(self, *, resource_id: UUID, workspace_id: UUID) -> None:
        await self.get_async(resource_id=resource_id, workspace_id=workspace_id)
        response = await self.client.async_request(
            "DELETE",
            "/phase5_resources",
            headers={"prefer": "return=minimal"},
            params={"id": f"eq.{resource_id}"},
        )
        self._raise(response)

    @staticmethod
    def _json(values: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value.value
            if isinstance(value, ScaleResourceStatus)
            else str(value)
            if isinstance(value, UUID)
            else value.isoformat()
            if isinstance(value, datetime)
            else value
            for key, value in values.items()
        }

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ScaleResourceRecord:
        return ScaleResourceRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])) if row.get("project_id") else None,
            kind=str(row["kind"]),
            name=str(row["name"]),
            status=ScaleResourceStatus(str(row["status"])),
            payload=row.get("payload") or {},
            result=row.get("result") or {},
            revision=int(row.get("revision") or 1),
            error_message=str(row["error_message"]) if row.get("error_message") else None,
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise ApiError(502, "scale_repository_failed", "Phase 5 data could not be saved.")
