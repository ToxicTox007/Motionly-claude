from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.projects import ProjectResponse, ProjectStatus


@dataclass
class ProjectApiRecord:
    id: UUID
    workspace_id: UUID
    created_by: UUID
    title: str
    status: ProjectStatus = ProjectStatus.DRAFT
    active_video_id: UUID | None = None
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

    def list_projects(self, *, workspace_id: UUID) -> list[ProjectApiRecord]:
        return sorted(
            [project for project in self.projects.values() if project.workspace_id == workspace_id],
            key=lambda project: project.updated_at,
            reverse=True,
        )

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


class SupabaseProjectRepository:
    def __init__(self, *, supabase_url: str, service_role_key: str) -> None:
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def create_project(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        title: str,
    ) -> ProjectApiRecord:
        response = httpx.post(
            f"{self.rest_url}/projects",
            headers=self.headers,
            json={
                "workspace_id": str(workspace_id),
                "created_by": str(created_by),
                "title": title,
                "status": ProjectStatus.DRAFT,
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._project_from_row(response.json()[0])

    def list_projects(self, *, workspace_id: UUID) -> list[ProjectApiRecord]:
        response = httpx.get(
            f"{self.rest_url}/projects",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": "*",
                "order": "updated_at.desc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._project_from_row(row) for row in response.json()]

    def get_project(self, *, project_id: UUID, workspace_id: UUID) -> ProjectApiRecord:
        response = httpx.get(
            f"{self.rest_url}/projects",
            headers=self.headers,
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "*",
                "limit": "1",
            },
            timeout=10,
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
        response = httpx.patch(
            f"{self.rest_url}/projects",
            headers=self.headers,
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "*",
            },
            json=payload,
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "project_not_found", "Project was not found.")
        return self._project_from_row(rows[0])

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
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "supabase_request_failed", "The project service could not reach data.")
