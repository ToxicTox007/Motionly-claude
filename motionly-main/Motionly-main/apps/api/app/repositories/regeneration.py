from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.regeneration import (
    ArtifactVersionResponse,
    ControlSettingsResponse,
    ExportOptions,
    RegenerationAction,
    RegenerationLayer,
    UserControlSettings,
)


@dataclass
class ControlSettingsRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    version: int
    schema_version: int
    controls: UserControlSettings
    action: RegenerationAction | None
    export_options: ExportOptions
    settings_summary: dict[str, object]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> ControlSettingsResponse:
        return ControlSettingsResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            version=self.version,
            schema_version=self.schema_version,
            controls=self.controls,
            action=self.action,
            export_options=self.export_options,
            settings_summary=self.settings_summary,
            created_at=self.created_at,
        )


@dataclass
class ArtifactVersionRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    layer: RegenerationLayer
    version: int
    artifact_id: UUID | None
    invalidates_from_version: int | None
    cache_key: str
    settings_summary: dict[str, object]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> ArtifactVersionResponse:
        return ArtifactVersionResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            layer=self.layer,
            version=self.version,
            artifact_id=self.artifact_id,
            invalidates_from_version=self.invalidates_from_version,
            cache_key=self.cache_key,
            settings_summary=self.settings_summary,
            created_at=self.created_at,
        )


class InMemoryRegenerationRepository:
    def __init__(self, plan_repository: InMemoryPlanRepository) -> None:
        self.plan_repository = plan_repository
        self.control_settings: dict[UUID, ControlSettingsRecord] = {}
        self.artifact_versions: dict[UUID, ArtifactVersionRecord] = {}

    def get_project_for_workspace(self, *, project_id: UUID, workspace_id: UUID) -> None:
        self.plan_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def create_control_settings(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        schema_version: int,
        controls: UserControlSettings,
        action: RegenerationAction | None,
        export_options: ExportOptions,
        settings_summary: dict[str, object],
    ) -> ControlSettingsRecord:
        version = (
            max(
                [
                    record.version
                    for record in self.control_settings.values()
                    if record.workspace_id == workspace_id and record.project_id == project_id
                ],
                default=0,
            )
            + 1
        )
        record = ControlSettingsRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            version=version,
            schema_version=schema_version,
            controls=controls,
            action=action,
            export_options=export_options,
            settings_summary=settings_summary,
        )
        self.control_settings[record.id] = record
        return record

    def create_artifact_versions(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        layers: list[RegenerationLayer],
        cache_key: str,
        settings_summary: dict[str, object],
    ) -> list[ArtifactVersionRecord]:
        records = []
        for layer in layers:
            previous_version = self.latest_layer_version(
                workspace_id=workspace_id,
                project_id=project_id,
                layer=layer,
            )
            record = ArtifactVersionRecord(
                id=uuid4(),
                workspace_id=workspace_id,
                project_id=project_id,
                layer=layer,
                version=previous_version + 1,
                artifact_id=None,
                invalidates_from_version=previous_version or None,
                cache_key=cache_key,
                settings_summary=settings_summary,
            )
            self.artifact_versions[record.id] = record
            records.append(record)
        return records

    def latest_layer_version(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        layer: RegenerationLayer,
    ) -> int:
        versions = [
            record.version
            for record in self.artifact_versions.values()
            if record.workspace_id == workspace_id
            and record.project_id == project_id
            and record.layer == layer
        ]
        return max(versions, default=0)


class SupabaseRegenerationRepository(InMemoryRegenerationRepository):
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

    def create_control_settings(self, **values: object) -> ControlSettingsRecord:
        existing = self._count_project_rows(
            table="project_control_settings",
            workspace_id=values["workspace_id"],
            project_id=values["project_id"],
        )
        payload = {
            **self._json_safe(values),
            "version": existing + 1,
        }
        response = httpx.post(
            f"{self.rest_url}/project_control_settings",
            headers=self.headers,
            json=[payload],
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._control_from_row(response.json()[0])

    def create_artifact_versions(self, **values: object) -> list[ArtifactVersionRecord]:
        workspace_id = values["workspace_id"]
        project_id = values["project_id"]
        rows = []
        for layer in values["layers"]:
            previous_version = self.latest_layer_version(
                workspace_id=workspace_id,
                project_id=project_id,
                layer=layer,
            )
            rows.append(
                {
                    "workspace_id": str(workspace_id),
                    "project_id": str(project_id),
                    "layer": layer.value,
                    "version": previous_version + 1,
                    "invalidates_from_version": previous_version or None,
                    "cache_key": values["cache_key"],
                    "settings_summary": values["settings_summary"],
                }
            )
        response = httpx.post(
            f"{self.rest_url}/artifact_versions",
            headers=self.headers,
            json=rows,
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._artifact_from_row(row) for row in response.json()]

    def latest_layer_version(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        layer: RegenerationLayer,
    ) -> int:
        response = httpx.get(
            f"{self.rest_url}/artifact_versions",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "layer": f"eq.{layer.value}",
                "select": "version",
                "order": "version.desc",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return int(rows[0]["version"]) if rows else 0

    def _count_project_rows(self, *, table: str, workspace_id: UUID, project_id: UUID) -> int:
        response = httpx.get(
            f"{self.rest_url}/{table}",
            headers={**self.headers, "prefer": "count=exact"},
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "id",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return int(response.headers.get("content-range", "0-0/0").split("/")[-1])

    def _control_from_row(self, row: dict[str, object]) -> ControlSettingsRecord:
        return ControlSettingsRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            version=int(row["version"]),
            schema_version=int(row["schema_version"]),
            controls=UserControlSettings.model_validate(row.get("controls") or {}),
            action=RegenerationAction(str(row["action"])) if row.get("action") else None,
            export_options=ExportOptions.model_validate(row.get("export_options") or {}),
            settings_summary=(
                row.get("settings_summary") if isinstance(row.get("settings_summary"), dict) else {}
            ),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
        )

    def _artifact_from_row(self, row: dict[str, object]) -> ArtifactVersionRecord:
        return ArtifactVersionRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            layer=RegenerationLayer(str(row["layer"])),
            version=int(row["version"]),
            artifact_id=UUID(str(row["artifact_id"])) if row.get("artifact_id") else None,
            invalidates_from_version=(
                int(row["invalidates_from_version"])
                if row.get("invalidates_from_version")
                else None
            ),
            cache_key=str(row["cache_key"]),
            settings_summary=(
                row.get("settings_summary") if isinstance(row.get("settings_summary"), dict) else {}
            ),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
        )

    def _json_safe(self, values: dict[str, object]) -> dict[str, object]:
        safe = {}
        for key, value in values.items():
            if isinstance(value, UUID):
                safe[key] = str(value)
            elif hasattr(value, "value"):
                safe[key] = value.value
            elif hasattr(value, "model_dump"):
                safe[key] = value.model_dump(mode="json")
            else:
                safe[key] = value
        return safe

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        raise ApiError(502, "supabase_request_failed", "Regeneration data could not be saved.")
