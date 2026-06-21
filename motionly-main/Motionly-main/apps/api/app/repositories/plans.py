from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.captions import InMemoryCaptionRepository, TranscriptRecord
from app.repositories.uploads import UploadRecord
from app.schemas.plans import (
    BrollDensity,
    EditPlan,
    EditPlanResponse,
    EditPlanStatus,
    EditPlanStyle,
    PlanSnapshotResponse,
    PlanSummary,
)


@dataclass
class EditPlanRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    transcript_id: UUID
    version: int
    schema_version: int
    status: EditPlanStatus
    is_active: bool
    style: EditPlanStyle
    target_duration_s: int
    broll_density: BrollDensity
    prompt: str
    feedback: str | None
    provider: str
    model: str
    cache_key: str
    schema_hash: str
    summary: PlanSummary
    plan: EditPlan
    latency_ms: int | None = None
    cost_estimate_usd: float | None = None
    validation_errors: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> EditPlanResponse:
        return EditPlanResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            transcript_id=self.transcript_id,
            version=self.version,
            schema_version=self.schema_version,
            status=self.status,
            is_active=self.is_active,
            style=self.style,
            target_duration_s=self.target_duration_s,
            broll_density=self.broll_density,
            prompt=self.prompt,
            feedback=self.feedback,
            provider=self.provider,
            model=self.model,
            cache_key=self.cache_key,
            schema_hash=self.schema_hash,
            latency_ms=self.latency_ms,
            cost_estimate_usd=self.cost_estimate_usd,
            validation_errors=self.validation_errors,
            summary=self.summary,
            plan=self.plan,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class EditPlanSnapshotRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    source_plan_id: UUID
    source_plan_version: int
    transcript_id: UUID
    schema_version: int
    style: EditPlanStyle
    target_duration_s: int
    broll_density: BrollDensity
    prompt: str
    feedback: str | None
    provider: str
    model: str
    cache_key: str
    schema_hash: str
    summary: PlanSummary
    plan: EditPlan
    name: str
    notes: str | None
    created_by_user_id: UUID
    validation_errors: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    cost_estimate_usd: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> PlanSnapshotResponse:
        return PlanSnapshotResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            source_plan_id=self.source_plan_id,
            source_plan_version=self.source_plan_version,
            name=self.name,
            notes=self.notes,
            created_by_user_id=self.created_by_user_id,
            plan=self.plan,
            summary=self.summary,
            created_at=self.created_at,
        )


class InMemoryPlanRepository:
    def __init__(self, caption_repository: InMemoryCaptionRepository) -> None:
        self.caption_repository = caption_repository
        self.plans: dict[UUID, EditPlanRecord] = {}
        self.snapshots: dict[UUID, EditPlanSnapshotRecord] = {}

    def get_project_for_workspace(self, *, project_id: UUID, workspace_id: UUID) -> None:
        self.caption_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def get_active_upload(self, *, project_id: UUID, workspace_id: UUID) -> UploadRecord:
        return self.caption_repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def latest_transcript(self, *, project_id: UUID, workspace_id: UUID) -> TranscriptRecord | None:
        return self.caption_repository.latest_transcript(
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def list_plans(self, *, project_id: UUID, workspace_id: UUID) -> list[EditPlanRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        return sorted(
            [
                plan
                for plan in self.plans.values()
                if plan.project_id == project_id and plan.workspace_id == workspace_id
            ],
            key=lambda plan: plan.version,
            reverse=True,
        )

    def active_plan(self, *, project_id: UUID, workspace_id: UUID) -> EditPlanRecord:
        plans = self.list_plans(project_id=project_id, workspace_id=workspace_id)
        active = next((plan for plan in plans if plan.is_active), None)
        if not active:
            raise ApiError(
                404,
                "edit_plan_not_found",
                "Create an edit plan before selecting music.",
            )
        return active

    def get_plan(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        plan_id: UUID,
    ) -> EditPlanRecord:
        plan = self.plans.get(plan_id)
        if plan is None or plan.project_id != project_id:
            raise ApiError(404, "edit_plan_not_found", "Edit plan was not found.")
        if plan.workspace_id != workspace_id:
            raise ApiError(403, "workspace_access_denied", "You do not have access to this plan.")
        return plan

    def find_cached_plan(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        cache_key: str,
    ) -> EditPlanRecord | None:
        matches = [
            plan
            for plan in self.plans.values()
            if plan.project_id == project_id
            and plan.workspace_id == workspace_id
            and plan.cache_key == cache_key
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda plan: plan.updated_at, reverse=True)[0]

    def create_plan(self, **values: object) -> EditPlanRecord:
        version = len(
            [
                plan
                for plan in self.plans.values()
                if plan.project_id == values["project_id"]
                and plan.workspace_id == values["workspace_id"]
            ]
        ) + 1
        for plan in self.plans.values():
            if (
                plan.project_id == values["project_id"]
                and plan.workspace_id == values["workspace_id"]
            ):
                plan.is_active = False
        record = EditPlanRecord(id=uuid4(), version=version, is_active=True, **values)
        self.plans[record.id] = record
        return record

    def activate_plan(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        plan_id: UUID,
    ) -> EditPlanRecord:
        target = self.get_plan(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)
        for plan in self.plans.values():
            if plan.project_id == project_id and plan.workspace_id == workspace_id:
                plan.is_active = plan.id == target.id
                plan.updated_at = datetime.now(UTC)
        return target

    def update_plan_cuts(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        plan_id: UUID,
        plan: EditPlan,
        summary: PlanSummary,
        target_duration_s: int,
    ) -> EditPlanRecord:
        record = self.get_plan(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)
        record.plan = plan
        record.summary = summary
        record.target_duration_s = target_duration_s
        record.updated_at = datetime.now(UTC)
        return record

    def create_plan_snapshot(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        source_plan: EditPlanRecord,
        name: str,
        notes: str | None,
        created_by_user_id: UUID,
    ) -> EditPlanSnapshotRecord:
        self.get_plan(
            project_id=project_id,
            workspace_id=workspace_id,
            plan_id=source_plan.id,
        )
        record = EditPlanSnapshotRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            source_plan_id=source_plan.id,
            source_plan_version=source_plan.version,
            transcript_id=source_plan.transcript_id,
            schema_version=source_plan.schema_version,
            style=source_plan.style,
            target_duration_s=source_plan.target_duration_s,
            broll_density=source_plan.broll_density,
            prompt=source_plan.prompt,
            feedback=source_plan.feedback,
            provider=source_plan.provider,
            model=source_plan.model,
            cache_key=source_plan.cache_key,
            schema_hash=source_plan.schema_hash,
            summary=source_plan.summary,
            plan=source_plan.plan,
            name=name.strip(),
            notes=notes.strip() if notes else None,
            created_by_user_id=created_by_user_id,
            validation_errors=list(source_plan.validation_errors),
            latency_ms=source_plan.latency_ms,
            cost_estimate_usd=source_plan.cost_estimate_usd,
        )
        self.snapshots[record.id] = record
        return record

    def list_plan_snapshots(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> list[EditPlanSnapshotRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        return sorted(
            [
                snapshot
                for snapshot in self.snapshots.values()
                if snapshot.project_id == project_id and snapshot.workspace_id == workspace_id
            ],
            key=lambda snapshot: snapshot.created_at,
            reverse=True,
        )

    def get_plan_snapshot(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        snapshot_id: UUID,
    ) -> EditPlanSnapshotRecord:
        snapshot = self.snapshots.get(snapshot_id)
        if snapshot is None or snapshot.project_id != project_id:
            raise ApiError(404, "plan_snapshot_not_found", "Plan snapshot was not found.")
        if snapshot.workspace_id != workspace_id:
            raise ApiError(
                403,
                "workspace_access_denied",
                "You do not have access to this snapshot.",
            )
        return snapshot

    def restore_plan_snapshot(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        snapshot_id: UUID,
    ) -> EditPlanRecord:
        snapshot = self.get_plan_snapshot(
            project_id=project_id,
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
        )
        return self.create_plan(
            workspace_id=workspace_id,
            project_id=project_id,
            transcript_id=snapshot.transcript_id,
            schema_version=snapshot.schema_version,
            status=EditPlanStatus.READY,
            style=snapshot.style,
            target_duration_s=snapshot.target_duration_s,
            broll_density=snapshot.broll_density,
            prompt=snapshot.prompt,
            feedback=snapshot.feedback,
            provider="motionly_snapshot",
            model=f"snapshot:{snapshot.id}",
            cache_key=f"snapshot:{snapshot.id}:{snapshot.cache_key}",
            schema_hash=snapshot.schema_hash,
            latency_ms=None,
            cost_estimate_usd=None,
            validation_errors=[
                *snapshot.validation_errors,
                f"restored_from_snapshot:{snapshot.id}",
            ],
            summary=snapshot.summary,
            plan=snapshot.plan,
        )


class SupabasePlanRepository(InMemoryPlanRepository):
    def __init__(
        self,
        *,
        caption_repository: InMemoryCaptionRepository,
        supabase_url: str,
        service_role_key: str,
    ) -> None:
        super().__init__(caption_repository)
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def list_plans(self, *, project_id: UUID, workspace_id: UUID) -> list[EditPlanRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        response = httpx.get(
            f"{self.rest_url}/edit_plans",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "version.desc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._plan_from_row(row) for row in response.json()]

    def get_plan(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        plan_id: UUID,
    ) -> EditPlanRecord:
        response = httpx.get(
            f"{self.rest_url}/edit_plans",
            headers=self.headers,
            params={
                "id": f"eq.{plan_id}",
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
            raise ApiError(404, "edit_plan_not_found", "Edit plan was not found.")
        return self._plan_from_row(rows[0])

    def find_cached_plan(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        cache_key: str,
    ) -> EditPlanRecord | None:
        response = httpx.get(
            f"{self.rest_url}/edit_plans",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "cache_key": f"eq.{cache_key}",
                "select": "*",
                "order": "updated_at.desc",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._plan_from_row(rows[0]) if rows else None

    def create_plan(self, **values: object) -> EditPlanRecord:
        existing = self.list_plans(
            project_id=values["project_id"],
            workspace_id=values["workspace_id"],
        )
        self._deactivate_project_plans(
            project_id=values["project_id"],
            workspace_id=values["workspace_id"],
        )
        response = httpx.post(
            f"{self.rest_url}/edit_plans",
            headers=self.headers,
            json={
                **self._json_safe(values),
                "version": len(existing) + 1,
                "is_active": True,
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._plan_from_row(response.json()[0])

    def create_plan_snapshot(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        source_plan: EditPlanRecord,
        name: str,
        notes: str | None,
        created_by_user_id: UUID,
    ) -> EditPlanSnapshotRecord:
        self.get_plan(
            project_id=project_id,
            workspace_id=workspace_id,
            plan_id=source_plan.id,
        )
        payload = self._snapshot_payload(
            source_plan=source_plan,
            name=name,
            notes=notes,
            created_by_user_id=created_by_user_id,
        )
        response = httpx.post(
            f"{self.rest_url}/edit_plan_snapshots",
            headers=self.headers,
            json=payload,
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._snapshot_from_row(response.json()[0])

    def list_plan_snapshots(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> list[EditPlanSnapshotRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        response = httpx.get(
            f"{self.rest_url}/edit_plan_snapshots",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "created_at.desc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._snapshot_from_row(row) for row in response.json()]

    def get_plan_snapshot(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        snapshot_id: UUID,
    ) -> EditPlanSnapshotRecord:
        response = httpx.get(
            f"{self.rest_url}/edit_plan_snapshots",
            headers=self.headers,
            params={
                "id": f"eq.{snapshot_id}",
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
            raise ApiError(404, "plan_snapshot_not_found", "Plan snapshot was not found.")
        return self._snapshot_from_row(rows[0])

    def restore_plan_snapshot(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        snapshot_id: UUID,
    ) -> EditPlanRecord:
        snapshot = self.get_plan_snapshot(
            project_id=project_id,
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
        )
        return self.create_plan(
            workspace_id=workspace_id,
            project_id=project_id,
            transcript_id=snapshot.transcript_id,
            schema_version=snapshot.schema_version,
            status=EditPlanStatus.READY,
            style=snapshot.style,
            target_duration_s=snapshot.target_duration_s,
            broll_density=snapshot.broll_density,
            prompt=snapshot.prompt,
            feedback=snapshot.feedback,
            provider="motionly_snapshot",
            model=f"snapshot:{snapshot.id}",
            cache_key=f"snapshot:{snapshot.id}:{snapshot.cache_key}",
            schema_hash=snapshot.schema_hash,
            latency_ms=None,
            cost_estimate_usd=None,
            validation_errors=[
                *snapshot.validation_errors,
                f"restored_from_snapshot:{snapshot.id}",
            ],
            summary=snapshot.summary,
            plan=snapshot.plan,
        )

    def activate_plan(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        plan_id: UUID,
    ) -> EditPlanRecord:
        self.get_plan(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)
        self._deactivate_project_plans(project_id=project_id, workspace_id=workspace_id)
        response = httpx.patch(
            f"{self.rest_url}/edit_plans",
            headers=self.headers,
            params={"id": f"eq.{plan_id}", "select": "*"},
            json={"is_active": True, "updated_at": datetime.now(UTC).isoformat()},
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._plan_from_row(response.json()[0])

    def update_plan_cuts(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        plan_id: UUID,
        plan: EditPlan,
        summary: PlanSummary,
        target_duration_s: int,
    ) -> EditPlanRecord:
        self.get_plan(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)
        response = httpx.patch(
            f"{self.rest_url}/edit_plans",
            headers=self.headers,
            params={"id": f"eq.{plan_id}", "select": "*"},
            json=self._json_safe(
                {
                    "plan": plan,
                    "summary": summary,
                    "target_duration_s": target_duration_s,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ),
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._plan_from_row(response.json()[0])

    def _deactivate_project_plans(self, *, project_id: UUID, workspace_id: UUID) -> None:
        response = httpx.patch(
            f"{self.rest_url}/edit_plans",
            headers=self.headers,
            params={"workspace_id": f"eq.{workspace_id}", "project_id": f"eq.{project_id}"},
            json={"is_active": False, "updated_at": datetime.now(UTC).isoformat()},
            timeout=10,
        )
        self._raise_for_supabase(response)

    def _plan_from_row(self, row: dict[str, object]) -> EditPlanRecord:
        return EditPlanRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            transcript_id=UUID(str(row["transcript_id"])),
            version=int(row["version"]),
            schema_version=int(row["schema_version"]),
            status=EditPlanStatus(str(row["status"])),
            is_active=bool(row["is_active"]),
            style=EditPlanStyle(str(row["style"])),
            target_duration_s=int(row["target_duration_s"]),
            broll_density=BrollDensity(str(row["broll_density"])),
            prompt=str(row.get("prompt") or ""),
            feedback=str(row["feedback"]) if row.get("feedback") else None,
            provider=str(row["provider"]),
            model=str(row["model"]),
            cache_key=str(row["cache_key"]),
            schema_hash=str(row["schema_hash"]),
            latency_ms=row.get("latency_ms"),
            cost_estimate_usd=self._cost_estimate_from_row(row),
            validation_errors=[
                str(error) for error in row.get("validation_errors", []) if isinstance(error, str)
            ],
            summary=PlanSummary.model_validate(row.get("summary") or {}),
            plan=EditPlan.model_validate(row.get("plan") or {}),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    def _snapshot_payload(
        self,
        *,
        source_plan: EditPlanRecord,
        name: str,
        notes: str | None,
        created_by_user_id: UUID,
    ) -> dict[str, object]:
        return self._json_safe(
            {
                "workspace_id": source_plan.workspace_id,
                "project_id": source_plan.project_id,
                "source_plan_id": source_plan.id,
                "source_plan_version": source_plan.version,
                "transcript_id": source_plan.transcript_id,
                "schema_version": source_plan.schema_version,
                "style": source_plan.style,
                "target_duration_s": source_plan.target_duration_s,
                "broll_density": source_plan.broll_density,
                "prompt": source_plan.prompt,
                "feedback": source_plan.feedback,
                "provider": source_plan.provider,
                "model": source_plan.model,
                "cache_key": source_plan.cache_key,
                "schema_hash": source_plan.schema_hash,
                "summary": source_plan.summary,
                "plan": source_plan.plan,
                "name": name.strip(),
                "notes": notes.strip() if notes else None,
                "created_by_user_id": created_by_user_id,
                "validation_errors": source_plan.validation_errors,
                "latency_ms": source_plan.latency_ms,
                "cost_estimate_usd": source_plan.cost_estimate_usd,
            }
        )

    def _snapshot_from_row(self, row: dict[str, object]) -> EditPlanSnapshotRecord:
        return EditPlanSnapshotRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            source_plan_id=UUID(str(row["source_plan_id"])),
            source_plan_version=int(row["source_plan_version"]),
            transcript_id=UUID(str(row["transcript_id"])),
            schema_version=int(row["schema_version"]),
            style=EditPlanStyle(str(row["style"])),
            target_duration_s=int(row["target_duration_s"]),
            broll_density=BrollDensity(str(row["broll_density"])),
            prompt=str(row.get("prompt") or ""),
            feedback=str(row["feedback"]) if row.get("feedback") else None,
            provider=str(row["provider"]),
            model=str(row["model"]),
            cache_key=str(row["cache_key"]),
            schema_hash=str(row["schema_hash"]),
            summary=PlanSummary.model_validate(row.get("summary") or {}),
            plan=EditPlan.model_validate(row.get("plan") or {}),
            name=str(row["name"]),
            notes=str(row["notes"]) if row.get("notes") else None,
            created_by_user_id=UUID(str(row["created_by_user_id"])),
            validation_errors=[
                str(error) for error in row.get("validation_errors", []) if isinstance(error, str)
            ],
            latency_ms=row.get("latency_ms"),
            cost_estimate_usd=self._cost_estimate_from_row(row),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
        )

    def _json_safe(self, values: dict[str, object]) -> dict[str, object]:
        safe: dict[str, object] = {}
        for key, value in values.items():
            if isinstance(value, UUID):
                safe[key] = str(value)
            elif isinstance(value, list):
                safe[key] = [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in value
                ]
            elif hasattr(value, "value"):
                safe[key] = value.value
            elif hasattr(value, "model_dump"):
                safe[key] = value.model_dump(mode="json")
            else:
                safe[key] = value
        return safe

    def _cost_estimate_from_row(self, row: dict[str, object]) -> float | None:
        if row.get("cost_estimate_usd") is None:
            return None
        return float(row["cost_estimate_usd"])

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "supabase_request_failed", "Edit plan data could not be saved.")
