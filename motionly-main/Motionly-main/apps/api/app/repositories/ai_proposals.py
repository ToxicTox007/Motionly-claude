from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.ai_proposals import (
    AI_ORCHESTRATOR_VERSION,
    AI_TOOL_REGISTRY_VERSION,
    ArtifactRevisionDigest,
    MultimodalProposalContext,
    ProposalAction,
    ProposalCostUsage,
    ProposalDecisionRequest,
    ProposalDecisionResponse,
    ProposalDecisionType,
    ProposalProvenance,
    ProposalResponse,
    ProposalState,
)
from app.schemas.plans import BrollDensity, EditPlan, EditPlanStatus, PlanSummary
from app.services.ai_proposals import (
    TOOL_REGISTRY,
    apply_actions,
    canonical_hash,
    context_digest,
    proposal_result_hash,
)
from app.services.edit_planner import PLANNER_SCHEMA_HASH, summarize_plan
from app.services.supabase import SupabaseRestClient

PROPOSAL_COLUMNS = (
    "id,workspace_id,project_id,workflow_run_id,request_id,request_hash,state,revision,context_version,"
    "context_digest,context_snapshot,upload_id,media_revision,transcript_id,"
    "visual_manifest_id,visual_manifest_version,base_active_plan_id,"
    "base_active_plan_revision,base_timeline_hash,prompt_version,tool_registry_version,"
    "orchestrator_version,provider,model,model_revision,route_id,gateway_request_id,"
    "planner_source,overall_rationale,confidence,degradation_flags,risk_flags,"
    "validation_history_codes,retry_history_codes,fallback_history_codes,"
    "estimated_cost_usd,actual_cost_usd,usage,latency_ms,result_hash,failure_code,"
    "failure_message,generated_plan,generated_summary,created_by,created_at,updated_at"
)


def validate_decision_request(payload: ProposalDecisionRequest) -> None:
    ids = payload.accepted_action_ids
    if len(ids) != len(set(ids)):
        raise ApiError(
            422,
            "proposal_duplicate_selection",
            "Accepted action IDs must be unique.",
        )
    if (payload.expected_base_active_plan_id is None) != (
        payload.expected_base_active_plan_revision is None
    ):
        raise ApiError(
            422,
            "proposal_base_expectation_invalid",
            "Base plan id and revision must both be null or both be set.",
        )
    if payload.decision == ProposalDecisionType.REJECT and ids:
        raise ApiError(
            422,
            "proposal_reject_selection_invalid",
            "Rejection cannot accept actions.",
        )
    if payload.decision == ProposalDecisionType.PARTIAL_APPROVE and not ids:
        raise ApiError(
            422,
            "proposal_partial_selection_invalid",
            "Partial approval requires accepted actions.",
        )


ACTION_COLUMNS = (
    "id,proposal_id,workspace_id,project_id,action_id,ordinal,tool_name,tool_version,args,"
    "rationale,confidence,dependency_ids,risk_flags,dry_run_hash,created_at"
)


@dataclass
class ProposalRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    request_id: str
    request_hash: str
    state: ProposalState
    revision: int
    context: MultimodalProposalContext
    context_digest: str
    provenance: ProposalProvenance
    created_by: UUID
    workflow_run_id: UUID | None = None
    actions: list[ProposalAction] = field(default_factory=list)
    generated_plan: EditPlan | None = None
    generated_summary: PlanSummary | None = None
    overall_rationale: str | None = None
    confidence: float | None = None
    degradation_flags: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    validation_history_codes: list[str] = field(default_factory=list)
    retry_history_codes: list[str] = field(default_factory=list)
    fallback_history_codes: list[str] = field(default_factory=list)
    cost: ProposalCostUsage = field(default_factory=ProposalCostUsage)
    result_hash: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def artifacts(self) -> ArtifactRevisionDigest:
        return self.context.artifacts

    def to_response(self) -> ProposalResponse:
        return ProposalResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            state=self.state,
            revision=self.revision,
            request_id=self.request_id,
            context_digest=self.context_digest,
            result_hash=self.result_hash,
            base_active_plan_id=self.artifacts.base_active_plan_id,
            base_active_plan_revision=self.artifacts.base_active_plan_revision,
            upload_id=self.artifacts.upload_id,
            media_revision=self.artifacts.media_revision,
            transcript_id=self.artifacts.transcript_id,
            artifact_revisions=self.artifacts,
            overall_rationale=self.overall_rationale,
            confidence=self.confidence,
            degradation_flags=self.degradation_flags,
            risk_flags=self.risk_flags,
            actions=self.actions,
            provenance=self.provenance,
            validation_history_codes=self.validation_history_codes,
            retry_history_codes=self.retry_history_codes,
            fallback_history_codes=self.fallback_history_codes,
            cost=self.cost,
            failure_code=self.failure_code,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass(frozen=True)
class ProposalDecisionRecord:
    id: UUID
    proposal_id: UUID
    workspace_id: UUID
    project_id: UUID
    decision: ProposalDecisionType
    idempotency_key: str
    request_hash: str
    accepted_action_ids: list[str]
    result_hash: str
    edit_plan_id: UUID | None
    edit_plan_revision: int | None
    created_by: UUID
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryAIProposalRepository:
    def __init__(self, *, plan_repository: Any) -> None:
        self.plan_repository = plan_repository
        self.proposals: dict[UUID, ProposalRecord] = {}
        self.decisions: dict[UUID, ProposalDecisionRecord] = {}
        self.timeline_revisions: list[dict[str, Any]] = []
        self._request_index: dict[tuple[UUID, UUID, str], UUID] = {}
        self._decision_keys: dict[tuple[UUID, UUID, str], UUID] = {}
        self._lock = RLock()

    def create_queued(
        self,
        *,
        context: MultimodalProposalContext,
        request_id: str,
        created_by: UUID,
        prompt_version: str,
    ) -> tuple[ProposalRecord, bool]:
        key = (context.workspace_id, context.project_id, request_id)
        with self._lock:
            existing_id = self._request_index.get(key)
            if existing_id is not None:
                existing = self.proposals[existing_id]
                if canonical_hash(existing.context.controls) != canonical_hash(context.controls):
                    raise ApiError(
                        409,
                        "proposal_idempotency_conflict",
                        "The idempotency key was already used for different controls.",
                    )
                return existing, True
            digest = context_digest(context)
            record = ProposalRecord(
                id=uuid4(),
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                request_id=request_id,
                request_hash=digest,
                state=ProposalState.QUEUED,
                revision=1,
                context=context,
                context_digest=digest,
                provenance=ProposalProvenance(prompt_version=prompt_version),
                created_by=created_by,
                degradation_flags=list(context.modality_degradation),
            )
            self.proposals[record.id] = record
            self._request_index[key] = record.id
            return record, False

    def get(self, *, proposal_id: UUID, project_id: UUID, workspace_id: UUID) -> ProposalRecord:
        record = self.proposals.get(proposal_id)
        if record is None or record.project_id != project_id:
            raise ApiError(404, "proposal_not_found", "AI edit proposal was not found.")
        if record.workspace_id != workspace_id:
            raise ApiError(
                403, "workspace_access_denied", "You do not have access to this proposal."
            )
        return record

    def list(
        self, *, project_id: UUID, workspace_id: UUID, limit: int = 50
    ) -> list[ProposalRecord]:
        self.plan_repository.get_project_for_workspace(
            project_id=project_id, workspace_id=workspace_id
        )
        return sorted(
            [
                proposal
                for proposal in self.proposals.values()
                if proposal.project_id == project_id and proposal.workspace_id == workspace_id
            ],
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def mark_generating(
        self, *, proposal_id: UUID, project_id: UUID, workspace_id: UUID
    ) -> ProposalRecord:
        with self._lock:
            record = self.get(
                proposal_id=proposal_id, project_id=project_id, workspace_id=workspace_id
            )
            if record.state in {
                ProposalState.AWAITING_APPROVAL,
                ProposalState.APPROVED,
                ProposalState.PARTIALLY_APPROVED,
                ProposalState.REJECTED,
            }:
                return record
            if record.state not in {
                ProposalState.QUEUED,
                ProposalState.GENERATING,
                ProposalState.FAILED,
            }:
                raise ApiError(409, "proposal_state_conflict", "Proposal cannot start generation.")
            if record.state != ProposalState.GENERATING:
                record.state = ProposalState.GENERATING
                record.revision += 1
                record.updated_at = datetime.now(UTC)
            return record

    def complete_generation(
        self,
        *,
        proposal_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
        plan: EditPlan,
        summary: PlanSummary,
        actions: list[ProposalAction],
        provenance: ProposalProvenance,
        overall_rationale: str,
        confidence: float,
        risk_flags: list[str],
        validation_history_codes: list[str],
        retry_history_codes: list[str],
        fallback_history_codes: list[str],
        cost: ProposalCostUsage,
        approval_ready: bool,
        failure_code: str | None = None,
    ) -> ProposalRecord:
        actions = TOOL_REGISTRY.validate_actions(actions)
        with self._lock:
            record = self.get(
                proposal_id=proposal_id, project_id=project_id, workspace_id=workspace_id
            )
            if record.state == ProposalState.AWAITING_APPROVAL:
                return record
            if record.state != ProposalState.GENERATING:
                raise ApiError(409, "proposal_state_conflict", "Proposal is not generating.")
            record.generated_plan = plan
            record.generated_summary = summary
            record.actions = actions
            record.provenance = provenance
            record.overall_rationale = overall_rationale[:500]
            record.confidence = confidence
            record.risk_flags = list(dict.fromkeys(risk_flags))[:10]
            record.validation_history_codes = list(dict.fromkeys(validation_history_codes))[:30]
            record.retry_history_codes = list(dict.fromkeys(retry_history_codes))[:30]
            record.fallback_history_codes = list(dict.fromkeys(fallback_history_codes))[:30]
            record.cost = cost
            record.result_hash = proposal_result_hash(
                workspace_id=workspace_id,
                context_digest_value=record.context_digest,
                plan=plan,
                action_ids=[action.id for action in actions],
            )
            record.state = (
                ProposalState.AWAITING_APPROVAL if approval_ready else ProposalState.FAILED
            )
            record.failure_code = (
                None if approval_ready else (failure_code or "proposal_not_approval_ready")
            )
            record.revision += 1
            record.updated_at = datetime.now(UTC)
            return record

    def mark_failed(
        self,
        *,
        proposal_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
        failure_code: str,
        validation_history_codes: list[str] | None = None,
    ) -> ProposalRecord:
        with self._lock:
            record = self.get(
                proposal_id=proposal_id, project_id=project_id, workspace_id=workspace_id
            )
            if record.state in {
                ProposalState.APPROVED,
                ProposalState.PARTIALLY_APPROVED,
                ProposalState.REJECTED,
            }:
                return record
            record.state = ProposalState.FAILED
            record.failure_code = failure_code[:120]
            record.validation_history_codes = list(
                dict.fromkeys(validation_history_codes or record.validation_history_codes)
            )[:30]
            record.revision += 1
            record.updated_at = datetime.now(UTC)
            return record

    def decide(
        self,
        *,
        proposal_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        payload: ProposalDecisionRequest,
    ) -> ProposalDecisionResponse:
        validate_decision_request(payload)
        request_hash = canonical_hash(payload.model_dump(mode="json"))
        decision_key = (workspace_id, proposal_id, payload.idempotency_key)
        with self._lock:
            existing_id = self._decision_keys.get(decision_key)
            if existing_id is not None:
                existing = self.decisions[existing_id]
                if existing.request_hash != request_hash:
                    raise ApiError(
                        409,
                        "proposal_idempotency_conflict",
                        "The idempotency key was already used for a different decision.",
                    )
                proposal = self.get(
                    proposal_id=proposal_id, project_id=project_id, workspace_id=workspace_id
                )
                return self._decision_response(existing, proposal, replay=True)

            proposal = self.get(
                proposal_id=proposal_id, project_id=project_id, workspace_id=workspace_id
            )
            if proposal.state != ProposalState.AWAITING_APPROVAL:
                raise ApiError(
                    409, "proposal_not_awaiting_approval", "Proposal is not awaiting approval."
                )
            if proposal.revision != payload.expected_proposal_revision:
                raise ApiError(409, "proposal_revision_stale", "Proposal revision is stale.")
            if (
                payload.expected_base_active_plan_id != proposal.artifacts.base_active_plan_id
                or payload.expected_base_active_plan_revision
                != proposal.artifacts.base_active_plan_revision
            ):
                raise ApiError(
                    409,
                    "proposal_base_expectation_mismatch",
                    "Expected base plan does not match the proposal.",
                )

            active_plan = None
            try:
                active_plan = self.plan_repository.active_plan(
                    project_id=project_id, workspace_id=workspace_id
                )
            except ApiError as exc:
                if exc.code != "edit_plan_not_found":
                    raise
            actual_base = (
                (active_plan.id, active_plan.revision) if active_plan is not None else (None, None)
            )
            expected_base = (
                payload.expected_base_active_plan_id,
                payload.expected_base_active_plan_revision,
            )
            if actual_base != expected_base:
                raise ApiError(
                    409,
                    "proposal_base_plan_stale",
                    "The active edit plan changed after generation.",
                )
            upload = self.plan_repository.get_active_upload(
                project_id=project_id, workspace_id=workspace_id
            )
            if (
                upload.id != proposal.artifacts.upload_id
                or max(1, int(getattr(upload, "media_revision", 1)))
                != proposal.artifacts.media_revision
            ):
                raise ApiError(
                    409,
                    "proposal_media_context_stale",
                    "The active media changed after generation.",
                )

            if payload.decision == ProposalDecisionType.REJECT:
                selected: list[ProposalAction] = []
            elif payload.decision == ProposalDecisionType.APPROVE:
                if payload.accepted_action_ids:
                    raise ApiError(
                        422,
                        "proposal_full_selection_invalid",
                        "Full approval applies every action automatically.",
                    )
                selected = TOOL_REGISTRY.validate_actions(proposal.actions)
                if not selected:
                    raise ApiError(
                        422, "proposal_noop_selection", "Proposal has no actions to approve."
                    )
            else:
                selected = TOOL_REGISTRY.validate_selection(
                    actions=proposal.actions,
                    accepted_action_ids=payload.accepted_action_ids,
                    partial=True,
                )

            new_plan = None
            plan_record = None
            accepted_ids = [action.id for action in selected]
            if payload.decision != ProposalDecisionType.REJECT:
                source_duration_ms = (
                    upload.metadata.duration_ms or proposal.generated_plan.target_duration_s * 1000  # type: ignore[union-attr]
                )
                new_plan = apply_actions(
                    base_plan=active_plan.plan if active_plan else None,
                    actions=selected,
                    source_video_id=upload.id,
                    source_duration_ms=source_duration_ms,
                )
                result_hash = proposal_result_hash(
                    workspace_id=workspace_id,
                    context_digest_value=proposal.context_digest,
                    plan=new_plan,
                    action_ids=accepted_ids,
                )
                controls = proposal.context.controls
                broll_density = BrollDensity(
                    str(controls.get("broll_density") or BrollDensity.MEDIUM.value)
                )
                summary = summarize_plan(
                    new_plan,
                    style_classification=(
                        proposal.generated_summary.style_classification
                        if proposal.generated_summary
                        else None
                    ),
                )
                # Validation happens before InMemoryPlanRepository deactivates the old plan.
                plan_record = self.plan_repository.create_plan(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    transcript_id=proposal.artifacts.transcript_id,
                    schema_version=new_plan.schema_version,
                    status=EditPlanStatus.READY,
                    style=new_plan.style,
                    target_duration_s=new_plan.target_duration_s,
                    broll_density=broll_density,
                    prompt=str(controls.get("prompt") or ""),
                    feedback=(
                        str(controls["feedback"]) if controls.get("feedback") is not None else None
                    ),
                    provider=proposal.provenance.provider or "unknown",
                    model=proposal.provenance.model or "unknown",
                    planner_source=proposal.provenance.planner_source or "unknown",
                    model_name=proposal.provenance.model_revision
                    or proposal.provenance.model
                    or "unknown",
                    prompt_version=proposal.provenance.prompt_version,
                    retry_count=len(proposal.retry_history_codes),
                    fallback_reason=(
                        proposal.fallback_history_codes[-1]
                        if proposal.fallback_history_codes
                        else None
                    ),
                    model_choice=proposal.provenance.model_choice,
                    cache_key=f"approved-proposal:{proposal.id}:{result_hash}",
                    schema_hash=PLANNER_SCHEMA_HASH,
                    latency_ms=proposal.cost.latency_ms,
                    cost_estimate_usd=proposal.cost.actual_cost_usd
                    or proposal.cost.estimated_cost_usd,
                    validation_errors=proposal.validation_history_codes,
                    summary=summary,
                    plan=new_plan,
                )
            else:
                result_hash = canonical_hash(
                    {
                        "workspace_id": str(workspace_id),
                        "proposal_id": str(proposal.id),
                        "decision": "reject",
                        "context_digest": proposal.context_digest,
                    }
                )

            decision = ProposalDecisionRecord(
                id=uuid4(),
                proposal_id=proposal.id,
                workspace_id=workspace_id,
                project_id=project_id,
                decision=payload.decision,
                idempotency_key=payload.idempotency_key,
                request_hash=request_hash,
                accepted_action_ids=accepted_ids,
                result_hash=result_hash,
                edit_plan_id=getattr(plan_record, "id", None),
                edit_plan_revision=getattr(plan_record, "revision", None),
                created_by=user_id,
            )
            if plan_record is not None:
                self.timeline_revisions.append(
                    {
                        "workspace_id": workspace_id,
                        "project_id": project_id,
                        "edit_plan_id": plan_record.id,
                        "revision": plan_record.revision,
                        "plan_snapshot": new_plan.model_dump(mode="json"),
                        "change_type": (
                            "partial_approval"
                            if payload.decision == ProposalDecisionType.PARTIAL_APPROVE
                            else "approval"
                        ),
                        "proposal_id": proposal.id,
                        "decision_id": decision.id,
                        "result_hash": result_hash,
                    }
                )
            proposal.state = {
                ProposalDecisionType.APPROVE: ProposalState.APPROVED,
                ProposalDecisionType.PARTIAL_APPROVE: ProposalState.PARTIALLY_APPROVED,
                ProposalDecisionType.REJECT: ProposalState.REJECTED,
            }[payload.decision]
            proposal.result_hash = result_hash
            proposal.revision += 1
            proposal.updated_at = datetime.now(UTC)
            self.decisions[decision.id] = decision
            self._decision_keys[decision_key] = decision.id
            return self._decision_response(decision, proposal, replay=False)

    @staticmethod
    def _decision_response(
        decision: ProposalDecisionRecord, proposal: ProposalRecord, *, replay: bool
    ) -> ProposalDecisionResponse:
        return ProposalDecisionResponse(
            decision_id=decision.id,
            proposal_id=decision.proposal_id,
            decision=decision.decision,
            accepted_action_ids=decision.accepted_action_ids,
            result_hash=decision.result_hash,
            edit_plan_id=decision.edit_plan_id,
            edit_plan_revision=decision.edit_plan_revision,
            proposal=proposal.to_response(),
            idempotent_replay=replay,
        )


class SupabaseAIProposalRepository(InMemoryAIProposalRepository):
    def __init__(
        self,
        *,
        plan_repository: Any,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: Any | None = None,
    ) -> None:
        super().__init__(plan_repository=plan_repository)
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="ai_edit_proposals",
            metrics_registry=metrics_registry,
        )

    def create_queued(
        self,
        *,
        context: MultimodalProposalContext,
        request_id: str,
        created_by: UUID,
        prompt_version: str,
    ) -> tuple[ProposalRecord, bool]:
        response = self.client.request(
            "POST",
            "/rpc/motionly_create_ai_edit_proposal",
            json={
                "p_workspace_id": str(context.workspace_id),
                "p_project_id": str(context.project_id),
                "p_request_id": request_id,
                "p_context_digest": context_digest(context),
                "p_context_snapshot": context.model_dump(mode="json"),
                "p_upload_id": str(context.artifacts.upload_id),
                "p_media_revision": context.artifacts.media_revision,
                "p_transcript_id": str(context.artifacts.transcript_id),
                "p_visual_manifest_id": str(context.artifacts.visual_manifest_id)
                if context.artifacts.visual_manifest_id
                else None,
                "p_visual_manifest_version": context.artifacts.visual_manifest_version,
                "p_base_active_plan_id": str(context.artifacts.base_active_plan_id)
                if context.artifacts.base_active_plan_id
                else None,
                "p_base_active_plan_revision": context.artifacts.base_active_plan_revision,
                "p_base_timeline_hash": context.artifacts.base_timeline_hash,
                "p_prompt_version": prompt_version,
                "p_created_by": str(created_by),
            },
        )
        self._raise(response)
        body = response.json()
        row = body[0] if isinstance(body, list) else body
        return self._from_row(row), bool(row.get("deduplicated", False))

    def get(self, *, proposal_id: UUID, project_id: UUID, workspace_id: UUID) -> ProposalRecord:
        response = self.client.request(
            "GET",
            "/ai_edit_proposals",
            params={
                "id": f"eq.{proposal_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": PROPOSAL_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "proposal_not_found", "AI edit proposal was not found.")
        record = self._from_row(rows[0])
        record.actions = self._actions(proposal_id, workspace_id, project_id)
        return record

    def list(
        self, *, project_id: UUID, workspace_id: UUID, limit: int = 50
    ) -> list[ProposalRecord]:
        self.plan_repository.get_project_for_workspace(
            project_id=project_id, workspace_id=workspace_id
        )
        response = self.client.request(
            "GET",
            "/ai_edit_proposals",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": PROPOSAL_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        self._raise(response)
        records = [self._from_row(row) for row in response.json()]
        for record in records:
            record.actions = self._actions(record.id, workspace_id, project_id)
        return records

    def mark_generating(
        self, *, proposal_id: UUID, project_id: UUID, workspace_id: UUID
    ) -> ProposalRecord:
        response = self.client.request(
            "POST",
            "/rpc/motionly_transition_ai_edit_proposal",
            json={
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_proposal_id": str(proposal_id),
                "p_target_state": "generating",
                "p_completion": {},
            },
        )
        self._raise(response)
        return self.get(proposal_id=proposal_id, project_id=project_id, workspace_id=workspace_id)

    def complete_generation(self, **values: Any) -> ProposalRecord:
        proposal_id = values["proposal_id"]
        workspace_id = values["workspace_id"]
        project_id = values["project_id"]
        plan: EditPlan = values["plan"]
        summary: PlanSummary = values["summary"]
        actions: list[ProposalAction] = TOOL_REGISTRY.validate_actions(values["actions"])
        provenance: ProposalProvenance = values["provenance"]
        cost: ProposalCostUsage = values["cost"]
        approval_ready = bool(values["approval_ready"])
        response = self.client.request(
            "POST",
            "/rpc/motionly_transition_ai_edit_proposal",
            json={
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_proposal_id": str(proposal_id),
                "p_target_state": "awaiting_approval" if approval_ready else "failed",
                "p_completion": {
                    "actions": [action.model_dump(mode="json") for action in actions],
                    "generated_plan": plan.model_dump(mode="json"),
                    "generated_summary": summary.model_dump(mode="json"),
                    "provenance": provenance.model_dump(mode="json"),
                    "overall_rationale": values["overall_rationale"],
                    "confidence": values["confidence"],
                    "risk_flags": values["risk_flags"],
                    "validation_history_codes": values["validation_history_codes"],
                    "retry_history_codes": values["retry_history_codes"],
                    "fallback_history_codes": values["fallback_history_codes"],
                    "cost": cost.model_dump(mode="json"),
                    "failure_code": values.get("failure_code"),
                },
            },
        )
        self._raise(response)
        return self.get(proposal_id=proposal_id, project_id=project_id, workspace_id=workspace_id)

    def mark_failed(self, **values: Any) -> ProposalRecord:
        proposal = self.get(
            proposal_id=values["proposal_id"],
            project_id=values["project_id"],
            workspace_id=values["workspace_id"],
        )
        plan = proposal.generated_plan or EditPlan.model_validate(
            {
                "style": "clean_minimal",
                "target_duration_s": 10,
                "cuts": [{"start_ms": 0, "end_ms": 1000, "reason": "generation failed"}],
                "music_mood": "none",
                "color_grade": "neutral",
            }
        )
        return self.complete_generation(
            proposal_id=proposal.id,
            project_id=proposal.project_id,
            workspace_id=proposal.workspace_id,
            plan=plan,
            summary=summarize_plan(plan),
            actions=[],
            provenance=proposal.provenance,
            overall_rationale="Generation failed before an approval-ready proposal was created.",
            confidence=0.0,
            risk_flags=[],
            validation_history_codes=values.get("validation_history_codes") or [],
            retry_history_codes=[],
            fallback_history_codes=[],
            cost=ProposalCostUsage(),
            approval_ready=False,
            failure_code=values["failure_code"],
        )

    def decide(
        self,
        *,
        proposal_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        payload: ProposalDecisionRequest,
    ) -> ProposalDecisionResponse:
        validate_decision_request(payload)
        response = self.client.request(
            "POST",
            "/rpc/motionly_decide_ai_edit_proposal",
            json={
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_proposal_id": str(proposal_id),
                "p_decision": payload.decision.value,
                "p_expected_proposal_revision": payload.expected_proposal_revision,
                "p_expected_base_active_plan_id": str(payload.expected_base_active_plan_id)
                if payload.expected_base_active_plan_id
                else None,
                "p_expected_base_active_plan_revision": payload.expected_base_active_plan_revision,
                "p_idempotency_key": payload.idempotency_key,
                "p_accepted_action_ids": payload.accepted_action_ids,
                "p_decided_by": str(user_id),
            },
        )
        if response.status_code >= 400:
            message = ""
            try:
                message = str(response.json().get("message") or "")
            except Exception:
                pass
            mapping = {
                "PROPOSAL_REVISION_STALE": (409, "proposal_revision_stale"),
                "PROPOSAL_BASE_STALE": (409, "proposal_base_plan_stale"),
                "PROPOSAL_MEDIA_STALE": (409, "proposal_media_context_stale"),
                "PROPOSAL_IDEMPOTENCY_CONFLICT": (409, "proposal_idempotency_conflict"),
                "PROPOSAL_SELECTION_INVALID": (422, "proposal_partial_selection_invalid"),
                "PROPOSAL_DEPENDENCY_INVALID": (422, "proposal_dependency_not_selected"),
            }
            for marker, (status, code) in mapping.items():
                if marker in message:
                    raise ApiError(status, code, "Proposal decision could not be applied.")
            self._raise(response)
        body = response.json()
        row = body[0] if isinstance(body, list) else body
        proposal = self.get(
            proposal_id=proposal_id, project_id=project_id, workspace_id=workspace_id
        )
        return ProposalDecisionResponse(
            decision_id=UUID(str(row["decision_id"])),
            proposal_id=proposal_id,
            decision=payload.decision,
            accepted_action_ids=[str(item) for item in row.get("accepted_action_ids", [])],
            result_hash=str(row["result_hash"]),
            edit_plan_id=UUID(str(row["edit_plan_id"])) if row.get("edit_plan_id") else None,
            edit_plan_revision=int(row["edit_plan_revision"])
            if row.get("edit_plan_revision")
            else None,
            proposal=proposal.to_response(),
            idempotent_replay=bool(row.get("idempotent_replay", False)),
        )

    def _actions(
        self, proposal_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> list[ProposalAction]:
        response = self.client.request(
            "GET",
            "/ai_edit_proposal_actions",
            params={
                "proposal_id": f"eq.{proposal_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": ACTION_COLUMNS,
                "order": "ordinal.asc",
            },
        )
        self._raise(response)
        return [
            ProposalAction(
                id=str(row["action_id"]),
                ordinal=int(row["ordinal"]),
                tool_name=str(row["tool_name"]),
                tool_version=str(row["tool_version"]),
                args=dict(row.get("args") or {}),
                rationale=str(row["rationale"]),
                confidence=float(row["confidence"]),
                dependency_ids=[str(item) for item in row.get("dependency_ids", [])],
                risk_flags=[str(item) for item in row.get("risk_flags", [])],
                dry_run_hash=str(row["dry_run_hash"]),
            )
            for row in response.json()
        ]

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ProposalRecord:
        context = MultimodalProposalContext.model_validate(row["context_snapshot"])
        return ProposalRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            request_id=str(row["request_id"]),
            request_hash=str(row.get("request_hash") or row["context_digest"]),
            state=ProposalState(str(row["state"])),
            revision=int(row["revision"]),
            context=context,
            context_digest=str(row["context_digest"]),
            provenance=ProposalProvenance(
                provider=row.get("provider"),
                model=row.get("model"),
                model_revision=row.get("model_revision"),
                route_id=row.get("route_id"),
                gateway_request_id=row.get("gateway_request_id"),
                planner_source=row.get("planner_source"),
                prompt_version=str(row["prompt_version"]),
                tool_registry_version=str(
                    row.get("tool_registry_version") or AI_TOOL_REGISTRY_VERSION
                ),
                orchestrator_version=str(
                    row.get("orchestrator_version") or AI_ORCHESTRATOR_VERSION
                ),
            ),
            created_by=UUID(str(row["created_by"])),
            workflow_run_id=(
                UUID(str(row["workflow_run_id"])) if row.get("workflow_run_id") else None
            ),
            generated_plan=EditPlan.model_validate(row["generated_plan"])
            if row.get("generated_plan")
            else None,
            generated_summary=PlanSummary.model_validate(row["generated_summary"])
            if row.get("generated_summary")
            else None,
            overall_rationale=row.get("overall_rationale"),
            confidence=float(row["confidence"]) if row.get("confidence") is not None else None,
            degradation_flags=[str(item) for item in row.get("degradation_flags", [])],
            risk_flags=[str(item) for item in row.get("risk_flags", [])],
            validation_history_codes=[
                str(item) for item in row.get("validation_history_codes", [])
            ],
            retry_history_codes=[str(item) for item in row.get("retry_history_codes", [])],
            fallback_history_codes=[str(item) for item in row.get("fallback_history_codes", [])],
            cost=ProposalCostUsage(
                estimated_cost_usd=float(row["estimated_cost_usd"])
                if row.get("estimated_cost_usd") is not None
                else None,
                actual_cost_usd=float(row["actual_cost_usd"])
                if row.get("actual_cost_usd") is not None
                else None,
                usage=dict(row.get("usage") or {}),
                latency_ms=int(row["latency_ms"]) if row.get("latency_ms") is not None else None,
            ),
            result_hash=row.get("result_hash"),
            failure_code=row.get("failure_code"),
            failure_message=row.get("failure_message"),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "proposal_storage_failed", "AI proposal data could not be saved.")
