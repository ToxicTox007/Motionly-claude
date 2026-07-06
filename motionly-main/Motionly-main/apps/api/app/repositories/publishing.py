from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from app.api.errors import ApiError
from app.schemas.publishing import (
    PublishConnectionResponse,
    PublishConnectionStatus,
    PublishJobResponse,
    PublishPlatform,
    PublishStatus,
)
from app.services.supabase import SupabaseRestClient
from app.services.token_cipher import TokenCipher

PUBLISH_CONNECTION_COLUMNS = (
    "id,workspace_id,platform,access_token,refresh_token,token_expires_at,"
    "refresh_token_expires_at,scopes,connection_status,last_refresh_at,"
    "last_refresh_failure_reason,external_account_id,account_name,created_at"
)
PUBLISH_JOB_COLUMNS = (
    "id,workspace_id,project_id,export_id,connection_id,platform,status,title,caption,privacy,"
    "external_post_id,external_url,provider_operation_id,error_message,error_code,"
    "error_category,user_message,retryable,reconnect_required,retry_after_seconds,retry_count,"
    "duplicate_publish_risk,created_at,updated_at"
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
    refresh_token_expires_at: datetime | None = None
    scopes: list[str] | None = None
    connection_status: PublishConnectionStatus = PublishConnectionStatus.ACTIVE
    last_refresh_at: datetime | None = None
    last_refresh_failure_reason: str | None = None

    def to_response(self) -> PublishConnectionResponse:
        return PublishConnectionResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            platform=self.platform,
            external_account_id=self.external_account_id,
            account_name=self.account_name,
            connection_status=self.connection_status,
            access_token_expires_at=self.token_expires_at,
            refresh_token_expires_at=self.refresh_token_expires_at,
            scopes=list(self.scopes or []),
            last_refresh_at=self.last_refresh_at,
            last_refresh_failure_reason=self.last_refresh_failure_reason,
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
    provider_operation_id: str | None
    error_message: str | None
    error_code: str | None
    error_category: str | None
    user_message: str | None
    retryable: bool | None
    reconnect_required: bool
    retry_after_seconds: int | None
    retry_count: int
    duplicate_publish_risk: bool
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

    def list_connections(
        self,
        *,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[PublishConnectionRecord]:
        return sorted(
            [item for item in self.connections.values() if item.workspace_id == workspace_id],
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def list_all_connections(self) -> list[PublishConnectionRecord]:
        return sorted(
            self.connections.values(),
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

    def update_connection_tokens(
        self,
        *,
        connection: PublishConnectionRecord,
        access_token: str,
        refresh_token: str | None,
        token_expires_at: datetime | None,
        refresh_token_expires_at: datetime | None = None,
        scopes: list[str] | None = None,
        connection_status: PublishConnectionStatus | None = None,
        last_refresh_at: datetime | None = None,
        last_refresh_failure_reason: str | None = None,
    ) -> PublishConnectionRecord:
        connection.access_token = access_token
        connection.refresh_token = refresh_token
        connection.token_expires_at = token_expires_at
        connection.refresh_token_expires_at = refresh_token_expires_at
        if scopes is not None:
            connection.scopes = list(scopes)
        if connection_status is not None:
            connection.connection_status = connection_status
        connection.last_refresh_at = last_refresh_at
        connection.last_refresh_failure_reason = last_refresh_failure_reason
        return connection

    def update_connection(
        self, *, connection: PublishConnectionRecord, **values: object
    ) -> PublishConnectionRecord:
        for key, value in values.items():
            setattr(connection, key, value)
        return connection

    def create_job(self, **values: object) -> PublishJobRecord:
        now = datetime.now(UTC)
        record = PublishJobRecord(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            external_post_id=None,
            external_url=None,
            provider_operation_id=None,
            error_message=None,
            error_code=None,
            error_category=None,
            user_message=None,
            retryable=None,
            reconnect_required=False,
            retry_after_seconds=None,
            retry_count=0,
            duplicate_publish_risk=False,
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

    def list_jobs(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[PublishJobRecord]:
        return sorted(
            [
                item
                for item in self.jobs.values()
                if item.project_id == project_id and item.workspace_id == workspace_id
            ],
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def update_job(self, *, job: PublishJobRecord, **values: object) -> PublishJobRecord:
        for key, value in values.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(UTC)
        return job

    def find_active_job(
        self,
        *,
        workspace_id: UUID,
        export_id: UUID,
        connection_id: UUID,
    ) -> PublishJobRecord | None:
        active_statuses = {
            PublishStatus.QUEUED,
            PublishStatus.REFRESHING_TOKEN,
            PublishStatus.PUBLISHING,
            PublishStatus.UPLOADING,
            PublishStatus.PROCESSING,
            PublishStatus.RETRY_SCHEDULED,
        }
        matches = [
            item
            for item in self.jobs.values()
            if item.workspace_id == workspace_id
            and item.export_id == export_id
            and item.connection_id == connection_id
            and item.status in active_statuses
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.created_at, reverse=True)[0]


class SupabasePublishingRepository(InMemoryPublishingRepository):
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        token_cipher: TokenCipher,
        metrics_registry: object | None = None,
    ) -> None:
        super().__init__()
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="publishing",
            metrics_registry=metrics_registry,
            timeout_seconds=15,
        )
        self.token_cipher = token_cipher

    def create_connection(self, **values: object) -> PublishConnectionRecord:
        return self._write_connection("POST", "/publish_connections", self._encrypt_tokens(values))

    async def create_connection_async(self, **values: object) -> PublishConnectionRecord:
        return await self._write_connection_async(
            "POST",
            "/publish_connections",
            self._encrypt_tokens(values),
        )

    def upsert_connection(self, **values: object) -> PublishConnectionRecord:
        response = self.client.request(
            "POST",
            "/publish_connections",
            headers={"prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "workspace_id,platform,external_account_id"},
            json=self._json(self._encrypt_tokens(values)),
        )
        if response.status_code >= 400:
            raise ApiError(
                502,
                "publishing_repository_failed",
                "Publishing account could not be saved.",
            )
        return self._connection(response.json()[0])

    async def upsert_connection_async(self, **values: object) -> PublishConnectionRecord:
        response = await self.client.async_request(
            "POST",
            "/publish_connections",
            headers={"prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "workspace_id,platform,external_account_id"},
            json=self._json(self._encrypt_tokens(values)),
        )
        if response.status_code >= 400:
            raise ApiError(
                502,
                "publishing_repository_failed",
                "Publishing account could not be saved.",
            )
        return self._connection(response.json()[0])

    def list_connections(
        self,
        *,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[PublishConnectionRecord]:
        rows = self._request(
            "GET",
            "/publish_connections",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": PUBLISH_CONNECTION_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        return [self._connection(row) for row in rows]

    async def list_connections_async(
        self,
        *,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[PublishConnectionRecord]:
        rows = await self._request_async(
            "GET",
            "/publish_connections",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": PUBLISH_CONNECTION_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        return [self._connection(row) for row in rows]

    def list_all_connections(self) -> list[PublishConnectionRecord]:
        rows = self._request(
            "GET",
            "/publish_connections",
            params={
                "select": PUBLISH_CONNECTION_COLUMNS,
                "order": "created_at.desc",
                "limit": "1000",
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
                "select": PUBLISH_CONNECTION_COLUMNS,
                "limit": "1",
            },
        )
        if not rows:
            raise ApiError(
                404, "publish_connection_not_found", "Publishing connection was not found."
            )
        return self._connection(rows[0])

    async def get_connection_async(
        self,
        *,
        connection_id: UUID,
        workspace_id: UUID,
    ) -> PublishConnectionRecord:
        rows = await self._request_async(
            "GET",
            "/publish_connections",
            params={
                "id": f"eq.{connection_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": PUBLISH_CONNECTION_COLUMNS,
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

    async def delete_connection_async(self, *, connection_id: UUID, workspace_id: UUID) -> None:
        await self.get_connection_async(connection_id=connection_id, workspace_id=workspace_id)
        await self._request_async(
            "DELETE",
            "/publish_connections",
            params={"id": f"eq.{connection_id}"},
        )

    def create_job(self, **values: object) -> PublishJobRecord:
        rows = self._request("POST", "/publish_jobs", json=self._json(values))
        return self._job(rows[0])

    async def create_job_async(self, **values: object) -> PublishJobRecord:
        rows = await self._request_async("POST", "/publish_jobs", json=self._json(values))
        return self._job(rows[0])

    def get_job(self, *, job_id: UUID, workspace_id: UUID) -> PublishJobRecord:
        rows = self._request(
            "GET",
            "/publish_jobs",
            params={
                "id": f"eq.{job_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": PUBLISH_JOB_COLUMNS,
                "limit": "1",
            },
        )
        if not rows:
            raise ApiError(404, "publish_job_not_found", "Publish job was not found.")
        return self._job(rows[0])

    def list_jobs(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[PublishJobRecord]:
        rows = self._request(
            "GET",
            "/publish_jobs",
            params={
                "project_id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": PUBLISH_JOB_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        return [self._job(row) for row in rows]

    async def list_jobs_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[PublishJobRecord]:
        rows = await self._request_async(
            "GET",
            "/publish_jobs",
            params={
                "project_id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": PUBLISH_JOB_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        return [self._job(row) for row in rows]

    def update_job(self, *, job: PublishJobRecord, **values: object) -> PublishJobRecord:
        rows = self._request(
            "PATCH", "/publish_jobs", params={"id": f"eq.{job.id}"}, json=self._json(values)
        )
        return self._job(rows[0])

    def find_active_job(
        self,
        *,
        workspace_id: UUID,
        export_id: UUID,
        connection_id: UUID,
    ) -> PublishJobRecord | None:
        active_values = ",".join(
            status.value
            for status in (
                PublishStatus.QUEUED,
                PublishStatus.REFRESHING_TOKEN,
                PublishStatus.PUBLISHING,
                PublishStatus.UPLOADING,
                PublishStatus.PROCESSING,
                PublishStatus.RETRY_SCHEDULED,
            )
        )
        rows = self._request(
            "GET",
            "/publish_jobs",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "export_id": f"eq.{export_id}",
                "connection_id": f"eq.{connection_id}",
                "status": f"in.({active_values})",
                "select": PUBLISH_JOB_COLUMNS,
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        return self._job(rows[0]) if rows else None

    async def find_active_job_async(
        self,
        *,
        workspace_id: UUID,
        export_id: UUID,
        connection_id: UUID,
    ) -> PublishJobRecord | None:
        active_values = ",".join(
            status.value
            for status in (
                PublishStatus.QUEUED,
                PublishStatus.REFRESHING_TOKEN,
                PublishStatus.PUBLISHING,
                PublishStatus.UPLOADING,
                PublishStatus.PROCESSING,
                PublishStatus.RETRY_SCHEDULED,
            )
        )
        rows = await self._request_async(
            "GET",
            "/publish_jobs",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "export_id": f"eq.{export_id}",
                "connection_id": f"eq.{connection_id}",
                "status": f"in.({active_values})",
                "select": PUBLISH_JOB_COLUMNS,
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        return self._job(rows[0]) if rows else None

    def update_connection_tokens(
        self,
        *,
        connection: PublishConnectionRecord,
        access_token: str,
        refresh_token: str | None,
        token_expires_at: datetime | None,
        refresh_token_expires_at: datetime | None = None,
        scopes: list[str] | None = None,
        connection_status: PublishConnectionStatus | None = None,
        last_refresh_at: datetime | None = None,
        last_refresh_failure_reason: str | None = None,
    ) -> PublishConnectionRecord:
        rows = self._request(
            "PATCH",
            "/publish_connections",
            params={"id": f"eq.{connection.id}"},
            json=self._json(
                self._encrypt_tokens(
                    {
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "token_expires_at": token_expires_at,
                        "refresh_token_expires_at": refresh_token_expires_at,
                        "scopes": scopes,
                        "connection_status": connection_status,
                        "last_refresh_at": last_refresh_at,
                        "last_refresh_failure_reason": last_refresh_failure_reason,
                    }
                )
            ),
        )
        return self._connection(rows[0])

    def update_connection(
        self, *, connection: PublishConnectionRecord, **values: object
    ) -> PublishConnectionRecord:
        rows = self._request(
            "PATCH",
            "/publish_connections",
            params={"id": f"eq.{connection.id}"},
            json=self._json(self._encrypt_tokens(values)),
        )
        return self._connection(rows[0])

    def encrypt_legacy_tokens(self) -> int:
        """One-way startup migration for rows written before encrypted envelopes existed."""
        rows = self._request(
            "GET",
            "/publish_connections",
            params={"select": PUBLISH_CONNECTION_COLUMNS, "limit": "1000"},
        )
        migrated = 0
        for row in rows:
            access_token = str(row.get("access_token") or "")
            refresh_token = str(row["refresh_token"]) if row.get("refresh_token") else None
            if self.token_cipher.is_encrypted(access_token) and (
                not refresh_token or self.token_cipher.is_encrypted(refresh_token)
            ):
                continue
            self._request(
                "PATCH",
                "/publish_connections",
                params={"id": f"eq.{row['id']}"},
                json=self._encrypt_tokens(
                    {"access_token": access_token, "refresh_token": refresh_token}
                ),
            )
            migrated += 1
        return migrated

    def _write_connection(
        self, method: str, path: str, values: dict[str, object]
    ) -> PublishConnectionRecord:
        rows = self._request(method, path, json=self._json(values))
        return self._connection(rows[0])

    async def _write_connection_async(
        self,
        method: str,
        path: str,
        values: dict[str, object],
    ) -> PublishConnectionRecord:
        rows = await self._request_async(method, path, json=self._json(values))
        return self._connection(rows[0])

    def _request(self, method: str, path: str, **kwargs: object) -> list[dict[str, object]]:
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ApiError(
                502, "publishing_repository_failed", "Publishing data could not be saved."
            )
        if response.status_code == 204 or not response.content:
            return []
        return response.json()

    async def _request_async(
        self,
        method: str,
        path: str,
        **kwargs: object,
    ) -> list[dict[str, object]]:
        response = await self.client.async_request(method, path, **kwargs)
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
                else list(value)
                if isinstance(value, (tuple, list))
                else value
            )
            for key, value in values.items()
        }

    def _connection(self, row: dict[str, object]) -> PublishConnectionRecord:
        return PublishConnectionRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            platform=PublishPlatform(str(row["platform"])),
            access_token=self.token_cipher.decrypt(str(row["access_token"])) or "",
            external_account_id=str(row["external_account_id"]),
            account_name=str(row["account_name"]),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            refresh_token=self.token_cipher.decrypt(str(row["refresh_token"]))
            if row.get("refresh_token")
            else None,
            token_expires_at=(
                datetime.fromisoformat(str(row["token_expires_at"]).replace("Z", "+00:00"))
                if row.get("token_expires_at")
                else None
            ),
            refresh_token_expires_at=(
                datetime.fromisoformat(str(row["refresh_token_expires_at"]).replace("Z", "+00:00"))
                if row.get("refresh_token_expires_at")
                else None
            ),
            scopes=[str(item) for item in (row.get("scopes") or [])],
            connection_status=PublishConnectionStatus(
                str(row.get("connection_status") or PublishConnectionStatus.ACTIVE.value)
            ),
            last_refresh_at=(
                datetime.fromisoformat(str(row["last_refresh_at"]).replace("Z", "+00:00"))
                if row.get("last_refresh_at")
                else None
            ),
            last_refresh_failure_reason=str(row["last_refresh_failure_reason"])
            if row.get("last_refresh_failure_reason")
            else None,
        )

    def _encrypt_tokens(self, values: dict[str, object]) -> dict[str, object]:
        encrypted = dict(values)
        if "access_token" in encrypted:
            encrypted["access_token"] = self.token_cipher.encrypt(
                str(encrypted["access_token"])
            )
        if encrypted.get("refresh_token"):
            encrypted["refresh_token"] = self.token_cipher.encrypt(
                str(encrypted["refresh_token"])
            )
        return encrypted

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
            provider_operation_id=(
                str(row["provider_operation_id"]) if row.get("provider_operation_id") else None
            ),
            error_message=str(row["error_message"]) if row.get("error_message") else None,
            error_code=str(row["error_code"]) if row.get("error_code") else None,
            error_category=str(row["error_category"]) if row.get("error_category") else None,
            user_message=str(row["user_message"]) if row.get("user_message") else None,
            retryable=bool(row["retryable"]) if row.get("retryable") is not None else None,
            reconnect_required=bool(row.get("reconnect_required") or False),
            retry_after_seconds=(
                int(row["retry_after_seconds"])
                if row.get("retry_after_seconds") is not None
                else None
            ),
            retry_count=int(row.get("retry_count") or 0),
            duplicate_publish_risk=bool(row.get("duplicate_publish_risk") or False),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )
