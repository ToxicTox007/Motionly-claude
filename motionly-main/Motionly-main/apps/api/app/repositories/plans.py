from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.api.revision_errors import RevisionConflictError
from app.repositories.captions import InMemoryCaptionRepository, TranscriptRecord
from app.repositories.uploads import UploadRecord
from app.schemas.plans import (
    EDIT_PLAN_PROMPT_VERSION,
    BrollDensity,
    EditPlan,
    EditPlanResponse,
    EditPlanStatus,
    EditPlanStyle,
    PlanSnapshotResponse,
    PlanSummary,
)
from app.services.edit_plan_validation import EditPlanValidationContext, validate_edit_plan
from app.services.supabase import SupabaseRestClient

EDIT_PLAN_COLUMNS = (
    "id,workspace_id,project_id,transcript_id,version,schema_version,status,is_active,style,"
    "target_duration_s,broll_density,prompt,feedback,provider,model,planner_source,model_name,"
    "prompt_version,retry_count,fallback_reason,cache_key,schema_hash,latency_ms,"
    "cost_estimate_usd,validation_errors,summary,plan,revision,created_at,updated_at"
)
EDIT_PLAN_SNAPSHOT_COLUMNS = (
    "id,workspace_id,project_id,source_plan_id,source_plan_version,transcript_id,"
    "schema_version,style,target_duration_s,broll_density,prompt,feedback,provider,model,"
    "planner_source,model_name,prompt_version,retry_count,fallback_reason,cache_key,"
    "schema_hash,summary,plan,name,notes,created_by_user_id,validation_errors,latency_ms,"
    "cost_estimate_usd,created_at"
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
    planner_source: str = "unknown"
    model_name: str = ""
    prompt_version: str = EDIT_PLAN_PROMPT_VERSION
    retry_count: int = 0
    fallback_reason: str | None = None
    latency_ms: int | None = None
    cost_estimate_usd: float | None = None
    validation_errors: list[str] = field(default_factory=list)
    revision: int = 1
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
            planner_source=self.planner_source,
            model_name=self.model_name or self.model,
            prompt_version=self.prompt_version,
            retry_count=self.retry_count,
            fallback_reason=self.fallback_reason,
            cache_key=self.cache_key,
            schema_hash=self.schema_hash,
            latency_ms=self.latency_ms,
            cost_estimate_usd=self.cost_estimate_usd,
            validation_errors=self.validation_errors,
            summary=self.summary,
            plan=self.plan,
            revision=self.revision,
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
    planner_source: str = "unknown"
    model_name: str = ""
    prompt_version: str = EDIT_PLAN_PROMPT_VERSION
    retry_count: int = 0
    fallback_reason: str | None = None
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

    def list_plans(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[EditPlanRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        return sorted(
            [
                plan
                for plan in self.plans.values()
                if plan.project_id == project_id and plan.workspace_id == workspace_id
            ],
            key=lambda plan: plan.version,
            reverse=True,
        )[:limit]

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
            and plan.status == EditPlanStatus.READY
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda plan: plan.updated_at, reverse=True)[0]

    def find_latest_plan_for_transcript(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        transcript_id: UUID,
    ) -> EditPlanRecord | None:
        matches = [
            plan
            for plan in self.plans.values()
            if plan.project_id == project_id
            and plan.workspace_id == workspace_id
            and plan.transcript_id == transcript_id
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda plan: plan.updated_at, reverse=True)[0]

    def create_plan(self, **values: object) -> EditPlanRecord:
        values = self._prepare_plan_values_for_write(values)
        version = (
            len(
                [
                    plan
                    for plan in self.plans.values()
                    if plan.project_id == values["project_id"]
                    and plan.workspace_id == values["workspace_id"]
                ]
            )
            + 1
        )
        activate = values["status"] == EditPlanStatus.READY
        if activate:
            for plan in self.plans.values():
                if (
                    plan.project_id == values["project_id"]
                    and plan.workspace_id == values["workspace_id"]
                ):
                    plan.is_active = False
        record = EditPlanRecord(id=uuid4(), version=version, is_active=activate, **values)
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
        record.plan = self._validate_plan_for_write(
            plan=plan,
            project_id=project_id,
            workspace_id=workspace_id,
            transcript_id=record.transcript_id,
            plan_id=plan_id,
        )
        record.summary = summary
        record.schema_version = record.plan.schema_version
        record.target_duration_s = record.plan.target_duration_s
        record.prompt_version = record.plan.prompt_version
        record.updated_at = datetime.now(UTC)
        return record

    def update_plan_with_revision(
        self,
        *,
        plan_id: UUID,
        expected_revision: int,
        new_plan: EditPlan,
        project_id: UUID,
        workspace_id: UUID,
        summary: PlanSummary,
        target_duration_s: int,
        change_type: str = "manual_edit",
    ) -> EditPlanRecord:
        """Atomic CAS update: only succeeds if current revision matches expected_revision."""
        record = self.get_plan(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)
        if record.revision != expected_revision:
            raise RevisionConflictError(
                plan_id=str(plan_id),
                expected=expected_revision,
                actual=record.revision,
            )
        record.plan = self._validate_plan_for_write(
            plan=new_plan,
            project_id=project_id,
            workspace_id=workspace_id,
            transcript_id=record.transcript_id,
            plan_id=plan_id,
        )
        record.summary = summary
        record.schema_version = record.plan.schema_version
        record.target_duration_s = record.plan.target_duration_s
        record.prompt_version = record.plan.prompt_version
        record.revision += 1
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
            planner_source=source_plan.planner_source,
            model_name=source_plan.model_name,
            prompt_version=source_plan.prompt_version,
            retry_count=source_plan.retry_count,
            fallback_reason=source_plan.fallback_reason,
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
        limit: int = 100,
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
        )[:limit]

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
            planner_source=snapshot.planner_source,
            model_name=snapshot.model_name,
            prompt_version=snapshot.prompt_version,
            retry_count=snapshot.retry_count,
            fallback_reason=snapshot.fallback_reason,
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

    def _prepare_plan_values_for_write(self, values: dict[str, object]) -> dict[str, object]:
        prepared = dict(values)
        plan = self._validate_plan_for_write(
            plan=prepared["plan"],
            project_id=prepared["project_id"],
            workspace_id=prepared["workspace_id"],
            transcript_id=prepared["transcript_id"],
        )
        prepared["plan"] = plan
        prepared["schema_version"] = plan.schema_version
        prepared["target_duration_s"] = plan.target_duration_s
        prepared.setdefault("planner_source", "unknown")
        prepared.setdefault("model_name", str(prepared.get("model") or ""))
        prepared.setdefault("prompt_version", plan.prompt_version)
        prepared.setdefault("retry_count", 0)
        prepared.setdefault("fallback_reason", None)
        return prepared

    def _validate_plan_for_write(
        self,
        *,
        plan: EditPlan | dict[str, object] | object,
        project_id: object,
        workspace_id: object,
        transcript_id: object,
        plan_id: UUID | None = None,
    ) -> EditPlan:
        context = self._validation_context_for_plan_write(
            project_id=UUID(str(project_id)),
            workspace_id=UUID(str(workspace_id)),
            transcript_id=UUID(str(transcript_id)),
            plan_id=plan_id,
        )
        return validate_edit_plan(plan=plan, context=context)  # type: ignore[arg-type]

    def _validation_context_for_plan_write(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        transcript_id: UUID,
        plan_id: UUID | None = None,
    ) -> EditPlanValidationContext:
        upload = self.get_active_upload(project_id=project_id, workspace_id=workspace_id)
        transcript = self.caption_repository.get_transcript(
            project_id=project_id,
            workspace_id=workspace_id,
            transcript_id=transcript_id,
        )
        transcript_end_ms = max((word.end_ms for word in transcript.words), default=0)
        source_duration_ms = (
            upload.metadata.duration_ms
            or transcript.duration_ms
            or transcript_end_ms
            or 1
        )
        return EditPlanValidationContext(
            source_duration_ms=source_duration_ms,
            transcript_words=transcript.words,
            project_id=project_id,
            plan_id=plan_id,
        )


class SupabasePlanRepository(InMemoryPlanRepository):
    def __init__(
        self,
        *,
        caption_repository: InMemoryCaptionRepository,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: object | None = None,
    ) -> None:
        super().__init__(caption_repository)
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="plans",
            metrics_registry=metrics_registry,
        )

    def list_plans(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[EditPlanRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        response = self.client.request(
            "GET",
            "/edit_plans",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": EDIT_PLAN_COLUMNS,
                "order": "version.desc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._plan_from_row(row) for row in response.json()]

    async def list_plans_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[EditPlanRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        response = await self.client.async_request(
            "GET",
            "/edit_plans",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": EDIT_PLAN_COLUMNS,
                "order": "version.desc",
                "limit": str(limit),
            },
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
        response = self.client.request(
            "GET",
            "/edit_plans",
            params={
                "id": f"eq.{plan_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": EDIT_PLAN_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "edit_plan_not_found", "Edit plan was not found.")
        return self._plan_from_row(rows[0])

    async def get_plan_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        plan_id: UUID,
    ) -> EditPlanRecord:
        response = await self.client.async_request(
            "GET",
            "/edit_plans",
            params={
                "id": f"eq.{plan_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": EDIT_PLAN_COLUMNS,
                "limit": "1",
            },
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
        response = self.client.request(
            "GET",
            "/edit_plans",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "cache_key": f"eq.{cache_key}",
                "status": f"eq.{EditPlanStatus.READY.value}",
                "select": EDIT_PLAN_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._plan_from_row(rows[0]) if rows else None

    async def find_cached_plan_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        cache_key: str,
    ) -> EditPlanRecord | None:
        response = await self.client.async_request(
            "GET",
            "/edit_plans",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "cache_key": f"eq.{cache_key}",
                "status": f"eq.{EditPlanStatus.READY.value}",
                "select": EDIT_PLAN_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._plan_from_row(rows[0]) if rows else None

    def find_latest_plan_for_transcript(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        transcript_id: UUID,
    ) -> EditPlanRecord | None:
        response = self.client.request(
            "GET",
            "/edit_plans",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "transcript_id": f"eq.{transcript_id}",
                "select": EDIT_PLAN_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._plan_from_row(rows[0]) if rows else None

    async def find_latest_plan_for_transcript_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        transcript_id: UUID,
    ) -> EditPlanRecord | None:
        response = await self.client.async_request(
            "GET",
            "/edit_plans",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "transcript_id": f"eq.{transcript_id}",
                "select": EDIT_PLAN_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._plan_from_row(rows[0]) if rows else None

    def create_plan(self, **values: object) -> EditPlanRecord:
        values = self._prepare_plan_values_for_write(values)
        response = self.client.request(
            "POST",
            "/rpc/motionly_create_active_edit_plan",
            params={"select": EDIT_PLAN_COLUMNS},
            json=self._create_active_plan_rpc_payload(values),
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(502, "edit_plan_create_failed", "Edit plan could not be saved.")
        return self._plan_from_row(rows[0])

    async def create_plan_async(self, **values: object) -> EditPlanRecord:
        values = self._prepare_plan_values_for_write(values)
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_create_active_edit_plan",
            params={"select": EDIT_PLAN_COLUMNS},
            json=self._create_active_plan_rpc_payload(values),
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(502, "edit_plan_create_failed", "Edit plan could not be saved.")
        return self._plan_from_row(rows[0])

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
        response = self.client.request(
            "POST",
            "/edit_plan_snapshots",
            json=payload,
        )
        self._raise_for_supabase(response)
        return self._snapshot_from_row(response.json()[0])

    async def create_plan_snapshot_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        source_plan: EditPlanRecord,
        name: str,
        notes: str | None,
        created_by_user_id: UUID,
    ) -> EditPlanSnapshotRecord:
        await self.get_plan_async(
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
        response = await self.client.async_request(
            "POST",
            "/edit_plan_snapshots",
            json=payload,
        )
        self._raise_for_supabase(response)
        return self._snapshot_from_row(response.json()[0])

    def list_plan_snapshots(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[EditPlanSnapshotRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        response = self.client.request(
            "GET",
            "/edit_plan_snapshots",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": EDIT_PLAN_SNAPSHOT_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._snapshot_from_row(row) for row in response.json()]

    async def list_plan_snapshots_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[EditPlanSnapshotRecord]:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        response = await self.client.async_request(
            "GET",
            "/edit_plan_snapshots",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": EDIT_PLAN_SNAPSHOT_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
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
        response = self.client.request(
            "GET",
            "/edit_plan_snapshots",
            params={
                "id": f"eq.{snapshot_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": EDIT_PLAN_SNAPSHOT_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "plan_snapshot_not_found", "Plan snapshot was not found.")
        return self._snapshot_from_row(rows[0])

    async def get_plan_snapshot_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        snapshot_id: UUID,
    ) -> EditPlanSnapshotRecord:
        response = await self.client.async_request(
            "GET",
            "/edit_plan_snapshots",
            params={
                "id": f"eq.{snapshot_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": EDIT_PLAN_SNAPSHOT_COLUMNS,
                "limit": "1",
            },
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

    async def restore_plan_snapshot_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        snapshot_id: UUID,
    ) -> EditPlanRecord:
        snapshot = await self.get_plan_snapshot_async(
            project_id=project_id,
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
        )
        return await self.create_plan_async(
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
        response = self.client.request(
            "POST",
            "/rpc/motionly_activate_edit_plan",
            params={"select": EDIT_PLAN_COLUMNS},
            json={
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_plan_id": str(plan_id),
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "edit_plan_not_found", "Edit plan was not found.")
        return self._plan_from_row(rows[0])

    async def activate_plan_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        plan_id: UUID,
    ) -> EditPlanRecord:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_activate_edit_plan",
            params={"select": EDIT_PLAN_COLUMNS},
            json={
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_plan_id": str(plan_id),
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "edit_plan_not_found", "Edit plan was not found.")
        return self._plan_from_row(rows[0])

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
        existing = self.get_plan(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)
        plan = self._validate_plan_for_write(
            plan=plan,
            project_id=project_id,
            workspace_id=workspace_id,
            transcript_id=existing.transcript_id,
            plan_id=plan_id,
        )
        response = self.client.request(
            "PATCH",
            "/edit_plans",
            params={"id": f"eq.{plan_id}", "select": EDIT_PLAN_COLUMNS},
            json=self._json_safe(
                {
                    "plan": plan,
                    "summary": summary,
                    "schema_version": plan.schema_version,
                    "target_duration_s": plan.target_duration_s,
                    "prompt_version": plan.prompt_version,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
        self._raise_for_supabase(response)
        return self._plan_from_row(response.json()[0])

    async def update_plan_cuts_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        plan_id: UUID,
        plan: EditPlan,
        summary: PlanSummary,
        target_duration_s: int,
    ) -> EditPlanRecord:
        existing = await self.get_plan_async(
            project_id=project_id,
            workspace_id=workspace_id,
            plan_id=plan_id,
        )
        plan = self._validate_plan_for_write(
            plan=plan,
            project_id=project_id,
            workspace_id=workspace_id,
            transcript_id=existing.transcript_id,
            plan_id=plan_id,
        )
        response = await self.client.async_request(
            "PATCH",
            "/edit_plans",
            params={"id": f"eq.{plan_id}", "select": EDIT_PLAN_COLUMNS},
            json=self._json_safe(
                {
                    "plan": plan,
                    "summary": summary,
                    "schema_version": plan.schema_version,
                    "target_duration_s": plan.target_duration_s,
                    "prompt_version": plan.prompt_version,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
        self._raise_for_supabase(response)
        return self._plan_from_row(response.json()[0])

    def update_plan_with_revision(
        self,
        *,
        plan_id: UUID,
        expected_revision: int,
        new_plan: EditPlan,
        project_id: UUID,
        workspace_id: UUID,
        summary: PlanSummary,
        target_duration_s: int,
        change_type: str = "manual_edit",
    ) -> EditPlanRecord:
        """Atomic CAS update via motionly_update_plan_with_revision RPC."""
        existing = self.get_plan(
            project_id=project_id,
            workspace_id=workspace_id,
            plan_id=plan_id,
        )
        validated_plan = self._validate_plan_for_write(
            plan=new_plan,
            project_id=project_id,
            workspace_id=workspace_id,
            transcript_id=existing.transcript_id,
            plan_id=plan_id,
        )
        response = self.client.request(
            "POST",
            "/rpc/motionly_update_plan_with_revision",
            json={
                "p_plan_id": str(plan_id),
                "p_expected_revision": expected_revision,
                "p_new_plan": self._json_safe({"plan": validated_plan})["plan"],
            },
        )
        if response.status_code >= 400:
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            pg_code = body.get("code") or ""
            message = body.get("message") or ""
            if pg_code == "P0002" or "REVISION_CONFLICT" in message:
                raise RevisionConflictError(
                    plan_id=str(plan_id),
                    expected=expected_revision,
                    actual=self._extract_actual_revision(message, plan_id, workspace_id, project_id),
                )
            self._raise_for_supabase(response)
        # Re-fetch the updated plan to get the new revision and all fields
        return self.get_plan(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)

    async def update_plan_with_revision_async(
        self,
        *,
        plan_id: UUID,
        expected_revision: int,
        new_plan: EditPlan,
        project_id: UUID,
        workspace_id: UUID,
        summary: PlanSummary,
        target_duration_s: int,
        change_type: str = "manual_edit",
    ) -> EditPlanRecord:
        """Async atomic CAS update via motionly_update_plan_with_revision RPC."""
        existing = await self.get_plan_async(
            project_id=project_id,
            workspace_id=workspace_id,
            plan_id=plan_id,
        )
        validated_plan = self._validate_plan_for_write(
            plan=new_plan,
            project_id=project_id,
            workspace_id=workspace_id,
            transcript_id=existing.transcript_id,
            plan_id=plan_id,
        )
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_update_plan_with_revision",
            json={
                "p_plan_id": str(plan_id),
                "p_expected_revision": expected_revision,
                "p_new_plan": self._json_safe({"plan": validated_plan})["plan"],
            },
        )
        if response.status_code >= 400:
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            pg_code = body.get("code") or ""
            message = body.get("message") or ""
            if pg_code == "P0002" or "REVISION_CONFLICT" in message:
                raise RevisionConflictError(
                    plan_id=str(plan_id),
                    expected=expected_revision,
                    actual=await self._extract_actual_revision_async(message, plan_id, workspace_id, project_id),
                )
            self._raise_for_supabase(response)
        # Re-fetch the updated plan to get the new revision and all fields
        return await self.get_plan_async(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)

    def _extract_actual_revision(
        self, message: str, plan_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> int:
        """Try to extract actual revision from error message or re-fetch."""
        import re
        match = re.search(r"actual (\d+)", message)
        if match:
            return int(match.group(1))
        # Fallback: re-fetch
        try:
            record = self.get_plan(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)
            return record.revision
        except Exception:
            return -1

    async def _extract_actual_revision_async(
        self, message: str, plan_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> int:
        """Try to extract actual revision from error message or re-fetch (async)."""
        import re
        match = re.search(r"actual (\d+)", message)
        if match:
            return int(match.group(1))
        # Fallback: re-fetch
        try:
            record = await self.get_plan_async(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)
            return record.revision
        except Exception:
            return -1

    def _create_active_plan_rpc_payload(self, values: dict[str, object]) -> dict[str, object]:
        safe = self._json_safe(values)
        return {
            "p_workspace_id": safe["workspace_id"],
            "p_project_id": safe["project_id"],
            "p_transcript_id": safe["transcript_id"],
            "p_schema_version": safe["schema_version"],
            "p_status": safe["status"],
            "p_style": safe["style"],
            "p_target_duration_s": safe["target_duration_s"],
            "p_broll_density": safe["broll_density"],
            "p_prompt": safe["prompt"],
            "p_feedback": safe.get("feedback"),
            "p_provider": safe["provider"],
            "p_model": safe["model"],
            "p_planner_source": safe["planner_source"],
            "p_model_name": safe["model_name"],
            "p_prompt_version": safe["prompt_version"],
            "p_retry_count": safe["retry_count"],
            "p_fallback_reason": safe.get("fallback_reason"),
            "p_cache_key": safe["cache_key"],
            "p_schema_hash": safe["schema_hash"],
            "p_latency_ms": safe.get("latency_ms"),
            "p_cost_estimate_usd": safe.get("cost_estimate_usd"),
            "p_validation_errors": safe.get("validation_errors", []),
            "p_summary": safe["summary"],
            "p_plan": safe["plan"],
        }

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
            planner_source=str(row.get("planner_source") or "unknown"),
            model_name=str(row.get("model_name") or row.get("model") or ""),
            prompt_version=str(row.get("prompt_version") or EDIT_PLAN_PROMPT_VERSION),
            retry_count=int(row.get("retry_count") or 0),
            fallback_reason=str(row["fallback_reason"]) if row.get("fallback_reason") else None,
            cache_key=str(row["cache_key"]),
            schema_hash=str(row["schema_hash"]),
            latency_ms=row.get("latency_ms"),
            cost_estimate_usd=self._cost_estimate_from_row(row),
            validation_errors=[
                str(error) for error in row.get("validation_errors", []) if isinstance(error, str)
            ],
            summary=PlanSummary.model_validate(row.get("summary") or {}),
            plan=EditPlan.model_validate(row.get("plan") or {}),
            revision=int(row.get("revision") or 1),
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
                "planner_source": source_plan.planner_source,
                "model_name": source_plan.model_name,
                "prompt_version": source_plan.prompt_version,
                "retry_count": source_plan.retry_count,
                "fallback_reason": source_plan.fallback_reason,
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
            planner_source=str(row.get("planner_source") or "unknown"),
            model_name=str(row.get("model_name") or row.get("model") or ""),
            prompt_version=str(row.get("prompt_version") or EDIT_PLAN_PROMPT_VERSION),
            retry_count=int(row.get("retry_count") or 0),
            fallback_reason=str(row["fallback_reason"]) if row.get("fallback_reason") else None,
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
