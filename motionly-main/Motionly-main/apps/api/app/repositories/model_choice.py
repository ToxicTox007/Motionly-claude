"""Storage for user model choice settings (plan Section 2.11, Task 30).

Two tables back the Section 2.11 settings (migration M6
``202610250201_model_choice_settings.sql``):

* ``workspace_model_settings`` — the workspace default, the members' allow-list
  and the lock, one row per ``(workspace_id, capability)``;
* ``project_model_settings`` — a per-project override, one row per
  ``(project_id, capability)``.

Both an in-memory implementation (used in tests and the dev/no-Supabase boot)
and a PostgREST implementation (``SupabaseModelChoiceRepository``, following
``app/repositories/inference_operations.py``) are provided. The option ids these
rows store are opaque registry route ids; validation against the live runtime is
the resolver's job (``app/services/model_choice.py``), never the repository's.

Nothing here stores or returns a base URL, a secret or a model id: a stored
``option_id`` is a route id, and the resolver surfaces only the operator's
display data to callers.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

import httpx

from app.services.supabase import SupabaseRestClient


@dataclass(frozen=True)
class WorkspaceModelSetting:
    """One workspace's choice for one capability (plan Section 2.11)."""

    workspace_id: UUID
    capability: str
    default_option_id: str | None = None
    # ``None`` means "every option the plan allows"; an empty tuple means "none".
    allowed_option_ids: tuple[str, ...] | None = None
    locked: bool = False
    updated_by: UUID | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ProjectModelSetting:
    """One project's override for one capability (plan Section 2.11)."""

    workspace_id: UUID
    project_id: UUID
    capability: str
    option_id: str
    updated_by: UUID | None = None
    updated_at: datetime | None = None


class ModelChoiceRepository(Protocol):
    """Read/write the Section 2.11 workspace and project model choice settings."""

    def get_workspace_settings(self, workspace_id: UUID) -> list[WorkspaceModelSetting]: ...

    def get_workspace_setting(
        self, workspace_id: UUID, capability: str
    ) -> WorkspaceModelSetting | None: ...

    def upsert_workspace_setting(
        self,
        *,
        workspace_id: UUID,
        capability: str,
        default_option_id: str | None,
        allowed_option_ids: Sequence[str] | None,
        locked: bool,
        updated_by: UUID | None,
    ) -> WorkspaceModelSetting: ...

    def get_project_settings(
        self, workspace_id: UUID, project_id: UUID
    ) -> list[ProjectModelSetting]: ...

    def get_project_setting(
        self, workspace_id: UUID, project_id: UUID, capability: str
    ) -> ProjectModelSetting | None: ...

    def upsert_project_setting(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        capability: str,
        option_id: str,
        updated_by: UUID | None,
    ) -> ProjectModelSetting: ...

    def delete_project_setting(
        self, *, workspace_id: UUID, project_id: UUID, capability: str
    ) -> bool: ...


class InMemoryModelChoiceRepository:
    """In-memory model choice store (tests and the no-Supabase boot)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workspace: dict[tuple[UUID, str], WorkspaceModelSetting] = {}
        self._project: dict[tuple[UUID, str], ProjectModelSetting] = {}

    def get_workspace_settings(self, workspace_id: UUID) -> list[WorkspaceModelSetting]:
        with self._lock:
            return [
                setting
                for (ws_id, _capability), setting in self._workspace.items()
                if ws_id == workspace_id
            ]

    def get_workspace_setting(
        self, workspace_id: UUID, capability: str
    ) -> WorkspaceModelSetting | None:
        with self._lock:
            return self._workspace.get((workspace_id, capability))

    def upsert_workspace_setting(
        self,
        *,
        workspace_id: UUID,
        capability: str,
        default_option_id: str | None,
        allowed_option_ids: Sequence[str] | None,
        locked: bool,
        updated_by: UUID | None,
    ) -> WorkspaceModelSetting:
        setting = WorkspaceModelSetting(
            workspace_id=workspace_id,
            capability=capability,
            default_option_id=default_option_id,
            allowed_option_ids=(
                tuple(allowed_option_ids) if allowed_option_ids is not None else None
            ),
            locked=locked,
            updated_by=updated_by,
            updated_at=datetime.now(UTC),
        )
        with self._lock:
            self._workspace[(workspace_id, capability)] = setting
        return setting

    def get_project_settings(
        self, workspace_id: UUID, project_id: UUID
    ) -> list[ProjectModelSetting]:
        with self._lock:
            return [
                setting
                for (proj_id, _capability), setting in self._project.items()
                if proj_id == project_id and setting.workspace_id == workspace_id
            ]

    def get_project_setting(
        self, workspace_id: UUID, project_id: UUID, capability: str
    ) -> ProjectModelSetting | None:
        with self._lock:
            setting = self._project.get((project_id, capability))
        if setting is None or setting.workspace_id != workspace_id:
            return None
        return setting

    def upsert_project_setting(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        capability: str,
        option_id: str,
        updated_by: UUID | None,
    ) -> ProjectModelSetting:
        setting = ProjectModelSetting(
            workspace_id=workspace_id,
            project_id=project_id,
            capability=capability,
            option_id=option_id,
            updated_by=updated_by,
            updated_at=datetime.now(UTC),
        )
        with self._lock:
            self._project[(project_id, capability)] = setting
        return setting

    def delete_project_setting(
        self, *, workspace_id: UUID, project_id: UUID, capability: str
    ) -> bool:
        with self._lock:
            existing = self._project.get((project_id, capability))
            if existing is None or existing.workspace_id != workspace_id:
                return False
            del self._project[(project_id, capability)]
            return True

    def clear(self) -> None:
        with self._lock:
            self._workspace.clear()
            self._project.clear()


class ModelChoiceRepositoryError(RuntimeError):
    """The model choice store could not satisfy a request."""


_WORKSPACE_PATH = "/workspace_model_settings"
_PROJECT_PATH = "/project_model_settings"
_WORKSPACE_COLUMNS = (
    "workspace_id,capability,default_option_id,allowed_option_ids,locked,updated_by,updated_at"
)
_PROJECT_COLUMNS = "workspace_id,project_id,capability,option_id,updated_by,updated_at"


class SupabaseModelChoiceRepository:
    """PostgREST implementation of :class:`ModelChoiceRepository`."""

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
            repository="model_choice",
            metrics_registry=metrics_registry,
            timeout_seconds=10,
        )

    def get_workspace_settings(self, workspace_id: UUID) -> list[WorkspaceModelSetting]:
        response = self.client.request(
            "GET",
            _WORKSPACE_PATH,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": _WORKSPACE_COLUMNS,
            },
        )
        self._raise(response)
        return [self._workspace_from_row(row) for row in response.json()]

    def get_workspace_setting(
        self, workspace_id: UUID, capability: str
    ) -> WorkspaceModelSetting | None:
        response = self.client.request(
            "GET",
            _WORKSPACE_PATH,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "capability": f"eq.{capability}",
                "select": _WORKSPACE_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        return self._workspace_from_row(rows[0]) if rows else None

    def upsert_workspace_setting(
        self,
        *,
        workspace_id: UUID,
        capability: str,
        default_option_id: str | None,
        allowed_option_ids: Sequence[str] | None,
        locked: bool,
        updated_by: UUID | None,
    ) -> WorkspaceModelSetting:
        now = datetime.now(UTC)
        body = {
            "workspace_id": str(workspace_id),
            "capability": capability,
            "default_option_id": default_option_id,
            "allowed_option_ids": (
                list(allowed_option_ids) if allowed_option_ids is not None else None
            ),
            "locked": locked,
            "updated_by": str(updated_by) if updated_by is not None else None,
            "updated_at": now.isoformat(),
        }
        response = self.client.request(
            "POST",
            _WORKSPACE_PATH,
            params={"select": _WORKSPACE_COLUMNS},
            json=body,
            headers={"prefer": "return=representation,resolution=merge-duplicates"},
        )
        self._raise(response)
        return self._workspace_from_row(response.json()[0])

    def get_project_settings(
        self, workspace_id: UUID, project_id: UUID
    ) -> list[ProjectModelSetting]:
        response = self.client.request(
            "GET",
            _PROJECT_PATH,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": _PROJECT_COLUMNS,
            },
        )
        self._raise(response)
        return [self._project_from_row(row) for row in response.json()]

    def get_project_setting(
        self, workspace_id: UUID, project_id: UUID, capability: str
    ) -> ProjectModelSetting | None:
        response = self.client.request(
            "GET",
            _PROJECT_PATH,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "capability": f"eq.{capability}",
                "select": _PROJECT_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        return self._project_from_row(rows[0]) if rows else None

    def upsert_project_setting(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        capability: str,
        option_id: str,
        updated_by: UUID | None,
    ) -> ProjectModelSetting:
        now = datetime.now(UTC)
        body = {
            "workspace_id": str(workspace_id),
            "project_id": str(project_id),
            "capability": capability,
            "option_id": option_id,
            "updated_by": str(updated_by) if updated_by is not None else None,
            "updated_at": now.isoformat(),
        }
        response = self.client.request(
            "POST",
            _PROJECT_PATH,
            params={"select": _PROJECT_COLUMNS},
            json=body,
            headers={"prefer": "return=representation,resolution=merge-duplicates"},
        )
        self._raise(response)
        return self._project_from_row(response.json()[0])

    def delete_project_setting(
        self, *, workspace_id: UUID, project_id: UUID, capability: str
    ) -> bool:
        response = self.client.request(
            "DELETE",
            _PROJECT_PATH,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "capability": f"eq.{capability}",
                "select": _PROJECT_COLUMNS,
            },
        )
        self._raise(response)
        return bool(response.json())

    @staticmethod
    def _workspace_from_row(row: dict[str, Any]) -> WorkspaceModelSetting:
        allowed = row.get("allowed_option_ids")
        return WorkspaceModelSetting(
            workspace_id=UUID(str(row["workspace_id"])),
            capability=str(row["capability"]),
            default_option_id=(
                str(row["default_option_id"]) if row.get("default_option_id") else None
            ),
            allowed_option_ids=(
                tuple(str(item) for item in allowed) if isinstance(allowed, list) else None
            ),
            locked=bool(row.get("locked") or False),
            updated_by=UUID(str(row["updated_by"])) if row.get("updated_by") else None,
            updated_at=_parse_ts(row.get("updated_at")),
        )

    @staticmethod
    def _project_from_row(row: dict[str, Any]) -> ProjectModelSetting:
        return ProjectModelSetting(
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            capability=str(row["capability"]),
            option_id=str(row["option_id"]),
            updated_by=UUID(str(row["updated_by"])) if row.get("updated_by") else None,
            updated_at=_parse_ts(row.get("updated_at")),
        )

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise ModelChoiceRepositoryError(
                f"model choice store returned {response.status_code}"
            )


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def with_updated(setting: WorkspaceModelSetting, **changes: Any) -> WorkspaceModelSetting:
    """Return a copy of ``setting`` with fields replaced (small helper for tests)."""
    return replace(setting, **changes)


__all__ = [
    "InMemoryModelChoiceRepository",
    "ModelChoiceRepository",
    "ModelChoiceRepositoryError",
    "ProjectModelSetting",
    "SupabaseModelChoiceRepository",
    "WorkspaceModelSetting",
    "with_updated",
]
