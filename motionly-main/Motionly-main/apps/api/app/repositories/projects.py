from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.projects import ProjectLatestUpload, ProjectResponse, ProjectStatus
from app.services.supabase import SupabaseRestClient

PROJECT_RESPONSE_COLUMNS = (
    "id,workspace_id,created_by,title,status,active_video_id,created_at,updated_at"
)

# PostgREST embed of the project's active upload via the
# projects_active_video_id_fkey foreign key. This lets list_projects return each
# project's latest upload in a single query (B29) instead of one request per
# project on the web side.
PROJECT_LIST_COLUMNS = (
    "id,workspace_id,created_by,title,status,active_video_id,created_at,updated_at,"
    "latest_upload:uploaded_videos!projects_active_video_id_fkey"
    "(id,status,original_filename,metadata)"
)


@dataclass
class ProjectApiRecord:
    id: UUID
    workspace_id: UUID
    created_by: UUID
    title: str
    status: ProjectStatus = ProjectStatus.DRAFT
    active_video_id: UUID | None = None
    latest_upload: ProjectLatestUpload | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> ProjectResponse:
        return ProjectResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            created_by=self.created_by,
            title=self.title,
            status=self.status,
            active_video_id=self.active_video_id,
            latest_upload=self.latest_upload,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class InMemoryProjectRepository:
    def __init__(self) -> None:
        self.projects: dict[UUID, ProjectApiRecord] = {}

    def create_project(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        title: str,
    ) -> ProjectApiRecord:
        project = ProjectApiRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            created_by=created_by,
            title=title,
        )
        self.projects[project.id] = project
        return project

    def list_projects(self, *, workspace_id: UUID, limit: int = 100) -> list[ProjectApiRecord]:
        return sorted(
            [project for project in self.projects.values() if project.workspace_id == workspace_id],
            key=lambda project: project.updated_at,
            reverse=True,
        )[:limit]

    def get_project(self, *, project_id: UUID, workspace_id: UUID) -> ProjectApiRecord:
        project = self.projects.get(project_id)
        if project is None or project.workspace_id != workspace_id:
            raise ApiError(404, "project_not_found", "Project was not found.")
        return project

    def update_project(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        title: str | None = None,
        status: ProjectStatus | None = None,
    ) -> ProjectApiRecord:
        project = self.get_project(project_id=project_id, workspace_id=workspace_id)
        if title is not None:
            project.title = title
        if status is not None:
            project.status = status
        project.updated_at = datetime.now(UTC)
        return project

    def delete_project(self, *, project_id: UUID, workspace_id: UUID) -> None:
        self.get_project(project_id=project_id, workspace_id=workspace_id)
        del self.projects[project_id]


class SupabaseProjectRepository:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: object | None = None,
    ) -> None:
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="projects",
            metrics_registry=metrics_registry,
        )

    def create_project(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        title: str,
    ) -> ProjectApiRecord:
        response = self.client.request(
            "POST",
            "/projects",
            json={
                "workspace_id": str(workspace_id),
                "created_by": str(created_by),
                "title": title,
                "status": ProjectStatus.DRAFT,
            },
        )
        self._raise_for_supabase(response)
        return self._project_from_row(response.json()[0])

    async def create_project_async(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        title: str,
    ) -> ProjectApiRecord:
        response = await self.client.async_request(
            "POST",
            "/projects",
            json={
                "workspace_id": str(workspace_id),
                "created_by": str(created_by),
                "title": title,
                "status": ProjectStatus.DRAFT,
            },
        )
        self._raise_for_supabase(response)
        return self._project_from_row(response.json()[0])

    def list_projects(self, *, workspace_id: UUID, limit: int = 100) -> list[ProjectApiRecord]:
        response = self.client.request(
            "GET",
            "/projects",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": PROJECT_LIST_COLUMNS,
                "order": "updated_at.desc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._project_from_row(row) for row in response.json()]

    async def list_projects_async(
        self,
        *,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[ProjectApiRecord]:
        response = await self.client.async_request(
            "GET",
            "/projects",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": PROJECT_LIST_COLUMNS,
                "order": "updated_at.desc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._project_from_row(row) for row in response.json()]

    def get_project(self, *, project_id: UUID, workspace_id: UUID) -> ProjectApiRecord:
        response = self.client.request(
            "GET",
            "/projects",
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": PROJECT_RESPONSE_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "project_not_found", "Project was not found.")
        return self._project_from_row(rows[0])

    async def get_project_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> ProjectApiRecord:
        response = await self.client.async_request(
            "GET",
            "/projects",
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": PROJECT_RESPONSE_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "project_not_found", "Project was not found.")
        return self._project_from_row(rows[0])

    def update_project(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        title: str | None = None,
        status: ProjectStatus | None = None,
    ) -> ProjectApiRecord:
        payload: dict[str, object] = {"updated_at": datetime.now(UTC).isoformat()}
        if title is not None:
            payload["title"] = title
        if status is not None:
            payload["status"] = status
        response = self.client.request(
            "PATCH",
            "/projects",
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": PROJECT_RESPONSE_COLUMNS,
            },
            json=payload,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "project_not_found", "Project was not found.")
        return self._project_from_row(rows[0])

    async def update_project_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        title: str | None = None,
        status: ProjectStatus | None = None,
    ) -> ProjectApiRecord:
        payload: dict[str, object] = {"updated_at": datetime.now(UTC).isoformat()}
        if title is not None:
            payload["title"] = title
        if status is not None:
            payload["status"] = status
        response = await self.client.async_request(
            "PATCH",
            "/projects",
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": PROJECT_RESPONSE_COLUMNS,
            },
            json=payload,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "project_not_found", "Project was not found.")
        return self._project_from_row(rows[0])

    def delete_project(self, *, project_id: UUID, workspace_id: UUID) -> None:
        self.get_project(project_id=project_id, workspace_id=workspace_id)
        response = self.client.request(
            "DELETE",
            "/projects",
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
            },
        )
        self._raise_for_supabase(response)

    async def delete_project_async(self, *, project_id: UUID, workspace_id: UUID) -> None:
        await self.get_project_async(project_id=project_id, workspace_id=workspace_id)
        response = await self.client.async_request(
            "DELETE",
            "/projects",
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
            },
        )
        self._raise_for_supabase(response)

    def _project_from_row(self, row: dict[str, object]) -> ProjectApiRecord:
        return ProjectApiRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            created_by=UUID(str(row["created_by"])),
            title=str(row["title"]),
            status=ProjectStatus(str(row.get("status", ProjectStatus.DRAFT))),
            active_video_id=(
                UUID(str(row["active_video_id"])) if row.get("active_video_id") else None
            ),
            latest_upload=self._latest_upload_from_embed(row.get("latest_upload")),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    @staticmethod
    def _latest_upload_from_embed(embed: object) -> ProjectLatestUpload | None:
        # PostgREST returns a to-one embed as an object (or null); tolerate a
        # single-element list too in case the relationship is treated as to-many.
        if isinstance(embed, list):
            embed = embed[0] if embed else None
        if not isinstance(embed, dict) or not embed.get("id"):
            return None
        metadata = embed.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        duration = metadata.get("duration_ms")
        warnings = metadata.get("warnings")
        return ProjectLatestUpload(
            id=UUID(str(embed["id"])),
            status=str(embed.get("status", "")),
            original_filename=str(embed.get("original_filename", "")),
            duration_ms=int(duration) if isinstance(duration, (int, float)) else None,
            warnings=list(warnings) if isinstance(warnings, list) else [],
        )

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "supabase_request_failed", "The project service could not reach data.")
