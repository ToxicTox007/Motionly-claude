from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.publishing import (
    PublishConnectionResponse,
    PublishJobResponse,
    PublishPlatform,
    PublishStatus,
)


@dataclass
class PublishConnectionRecord:
    id: UUID
    workspace_id: UUID
    platform: PublishPlatform
    access_token: str
    external_account_id: str
    account_name: str
    created_at: datetime
    refresh_token: str | None = None
    token_expires_at: datetime | None = None

    def to_response(self) -> PublishConnectionResponse:
        return PublishConnectionResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            platform=self.platform,
            external_account_id=self.external_account_id,
            account_name=self.account_name,
            created_at=self.created_at,
        )


@dataclass
class PublishJobRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    export_id: UUID
    connection_id: UUID
    platform: PublishPlatform
    status: PublishStatus
    title: str
    caption: str
    privacy: str
    external_post_id: str | None
    external_url: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    def to_response(self) -> PublishJobResponse:
        return PublishJobResponse(**self.__dict__)


class InMemoryPublishingRepository:
    def __init__(self) -> None:
        self.connections: dict[UUID, PublishConnectionRecord] = {}
        self.jobs: dict[UUID, PublishJobRecord] = {}

    def create_connection(self, **values: object) -> PublishConnectionRecord:
        record = PublishConnectionRecord(id=uuid4(), created_at=datetime.now(UTC), **values)
        self.connections[record.id] = record
        return record

    def upsert_connection(self, **values: object) -> PublishConnectionRecord:
        existing = next(
            (
                item
                for item in self.connections.values()
                if item.workspace_id == values["workspace_id"]
                and item.platform == values["platform"]
                and item.external_account_id == values["external_account_id"]
            ),
            None,
        )
        if not existing:
            return self.create_connection(**values)
        for key, value in values.items():
            setattr(existing, key, value)
        return existing

    def list_connections(self, *, workspace_id: UUID) -> list[PublishConnectionRecord]:
        return sorted(
            [item for item in self.connections.values() if item.workspace_id == workspace_id],
            key=lambda item: item.created_at,
            reverse=True,
        )

    def get_connection(self, *, connection_id: UUID, workspace_id: UUID) -> PublishConnectionRecord:
        record = self.connections.get(connection_id)
        if not record:
            raise ApiError(
                404, "publish_connection_not_found", "Publishing connection was not found."
            )
        if record.workspace_id != workspace_id:
            raise ApiError(
                403, "workspace_access_denied", "Publishing connection is not in this workspace."
            )
        return record

    def delete_connection(self, *, connection_id: UUID, workspace_id: UUID) -> None:
        self.get_connection(connection_id=connection_id, workspace_id=workspace_id)
        del self.connections[connection_id]

    def create_job(self, **values: object) -> PublishJobRecord:
        now = datetime.now(UTC)
        record = PublishJobRecord(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            external_post_id=None,
            external_url=None,
            error_message=None,
            **values,
        )
        self.jobs[record.id] = record
        return record

    def get_job(self, *, job_id: UUID, workspace_id: UUID) -> PublishJobRecord:
        record = self.jobs.get(job_id)
        if not record:
            raise ApiError(404, "publish_job_not_found", "Publish job was not found.")
        if record.workspace_id != workspace_id:
            raise ApiError(403, "workspace_access_denied", "Publish job is not in this workspace.")
        return record

    def list_jobs(self, *, project_id: UUID, workspace_id: UUID) -> list[PublishJobRecord]:
        return sorted(
            [
                item
                for item in self.jobs.values()
                if item.project_id == project_id and item.workspace_id == workspace_id
            ],
            key=lambda item: item.created_at,
            reverse=True,
        )

    def update_job(self, *, job: PublishJobRecord, **values: object) -> PublishJobRecord:
        for key, value in values.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(UTC)
        return job


class SupabasePublishingRepository(InMemoryPublishingRepository):
    def __init__(self, *, supabase_url: str, service_role_key: str) -> None:
        super().__init__()
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def create_connection(self, **values: object) -> PublishConnectionRecord:
        return self._write_connection("POST", "/publish_connections", values)

    def upsert_connection(self, **values: object) -> PublishConnectionRecord:
        response = httpx.post(
            f"{self.rest_url}/publish_connections",
            headers={**self.headers, "prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "workspace_id,platform,external_account_id"},
            json=self._json(values),
            timeout=15,
        )
        if response.status_code >= 400:
            raise ApiError(
                502,
                "publishing_repository_failed",
                "Publishing account could not be saved.",
            )
        return self._connection(response.json()[0])

    def list_connections(self, *, workspace_id: UUID) -> list[PublishConnectionRecord]:
        rows = self._request(
            "GET",
            "/publish_connections",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": "*",
                "order": "created_at.desc",
            },
        )
        return [self._connection(row) for row in rows]

    def get_connection(self, *, connection_id: UUID, workspace_id: UUID) -> PublishConnectionRecord:
        rows = self._request(
            "GET",
            "/publish_connections",
            params={
                "id": f"eq.{connection_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "*",
                "limit": "1",
            },
        )
        if not rows:
            raise ApiError(
                404, "publish_connection_not_found", "Publishing connection was not found."
            )
        return self._connection(rows[0])

    def delete_connection(self, *, connection_id: UUID, workspace_id: UUID) -> None:
        self.get_connection(connection_id=connection_id, workspace_id=workspace_id)
        self._request("DELETE", "/publish_connections", params={"id": f"eq.{connection_id}"})

    def create_job(self, **values: object) -> PublishJobRecord:
        rows = self._request("POST", "/publish_jobs", json=self._json(values))
        return self._job(rows[0])

    def get_job(self, *, job_id: UUID, workspace_id: UUID) -> PublishJobRecord:
        rows = self._request(
            "GET",
            "/publish_jobs",
            params={
                "id": f"eq.{job_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "*",
                "limit": "1",
            },
        )
        if not rows:
            raise ApiError(404, "publish_job_not_found", "Publish job was not found.")
        return self._job(rows[0])

    def list_jobs(self, *, project_id: UUID, workspace_id: UUID) -> list[PublishJobRecord]:
        rows = self._request(
            "GET",
            "/publish_jobs",
            params={
                "project_id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "*",
                "order": "created_at.desc",
            },
        )
        return [self._job(row) for row in rows]

    def update_job(self, *, job: PublishJobRecord, **values: object) -> PublishJobRecord:
        rows = self._request(
            "PATCH", "/publish_jobs", params={"id": f"eq.{job.id}"}, json=self._json(values)
        )
        return self._job(rows[0])

    def _write_connection(
        self, method: str, path: str, values: dict[str, object]
    ) -> PublishConnectionRecord:
        rows = self._request(method, path, json=self._json(values))
        return self._connection(rows[0])

    def _request(self, method: str, path: str, **kwargs: object) -> list[dict[str, object]]:
        response = httpx.request(
            method, f"{self.rest_url}{path}", headers=self.headers, timeout=15, **kwargs
        )
        if response.status_code >= 400:
            raise ApiError(
                502, "publishing_repository_failed", "Publishing data could not be saved."
            )
        if response.status_code == 204 or not response.content:
            return []
        return response.json()

    @staticmethod
    def _json(values: dict[str, object]) -> dict[str, object]:
        return {
            key: (
                value.value
                if isinstance(value, StrEnum)
                else str(value)
                if isinstance(value, UUID)
                else value.isoformat()
                if isinstance(value, datetime)
                else value
            )
            for key, value in values.items()
        }

    @staticmethod
    def _connection(row: dict[str, object]) -> PublishConnectionRecord:
        return PublishConnectionRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            platform=PublishPlatform(str(row["platform"])),
            access_token=str(row["access_token"]),
            external_account_id=str(row["external_account_id"]),
            account_name=str(row["account_name"]),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            refresh_token=str(row["refresh_token"]) if row.get("refresh_token") else None,
            token_expires_at=(
                datetime.fromisoformat(str(row["token_expires_at"]).replace("Z", "+00:00"))
                if row.get("token_expires_at")
                else None
            ),
        )

    @staticmethod
    def _job(row: dict[str, object]) -> PublishJobRecord:
        return PublishJobRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            export_id=UUID(str(row["export_id"])),
            connection_id=UUID(str(row["connection_id"])),
            platform=PublishPlatform(str(row["platform"])),
            status=PublishStatus(str(row["status"])),
            title=str(row["title"]),
            caption=str(row.get("caption") or ""),
            privacy=str(row["privacy"]),
            external_post_id=str(row["external_post_id"]) if row.get("external_post_id") else None,
            external_url=str(row["external_url"]) if row.get("external_url") else None,
            error_message=str(row["error_message"]) if row.get("error_message") else None,
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )
