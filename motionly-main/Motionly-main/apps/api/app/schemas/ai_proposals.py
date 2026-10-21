from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.plans import (
    BrollRequest,
    CaptionHighlight,
    EditPlan,
    EditPlanCut,
)
from app.schemas.request_body import JsonStringBodyMixin

AI_CONTEXT_VERSION = "ai-edit-context-v1"
AI_TOOL_REGISTRY_VERSION = "ai-edit-tools-v1"
AI_ORCHESTRATOR_VERSION = "ai-edit-orchestrator-v1"


class ProposalState(StrEnum):
    QUEUED = "queued"
    GENERATING = "generating"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PARTIALLY_APPROVED = "partially_approved"
    REJECTED = "rejected"
    FAILED = "failed"


class ProposalDecisionType(StrEnum):
    APPROVE = "approve"
    PARTIAL_APPROVE = "partial_approve"
    REJECT = "reject"


class ProposalToolName(StrEnum):
    COMPLETE_FIRST_PLAN = "complete_first_plan"
    SET_CUTS = "set_cuts"
    SET_CAPTION_HIGHLIGHTS = "set_caption_highlights"
    SET_BROLL_REQUESTS = "set_broll_requests"
    SET_LOOK_AUDIO_EFFECTS = "set_look_audio_effects"


class CompleteFirstPlanArgs(BaseModel):
    plan: EditPlan


class SetCutsArgs(BaseModel):
    cuts: list[EditPlanCut] = Field(min_length=1, max_length=24)
    target_duration_s: int = Field(ge=10, le=90)


class SetCaptionHighlightsArgs(BaseModel):
    caption_highlights: list[CaptionHighlight] = Field(default_factory=list, max_length=12)


class SetBrollRequestsArgs(BaseModel):
    broll_requests: list[BrollRequest] = Field(default_factory=list, max_length=12)


class SetLookAudioEffectsArgs(BaseModel):
    style: str = Field(min_length=1, max_length=80)
    music_mood: str = Field(min_length=1, max_length=160)
    effects: list[str] = Field(default_factory=list, max_length=8)
    color_grade: str = Field(min_length=1, max_length=80)
    zoom_moments: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    risk_flags: list[str] = Field(default_factory=list, max_length=10)


TOOL_ARGUMENT_MODELS: dict[ProposalToolName, type[BaseModel]] = {
    ProposalToolName.COMPLETE_FIRST_PLAN: CompleteFirstPlanArgs,
    ProposalToolName.SET_CUTS: SetCutsArgs,
    ProposalToolName.SET_CAPTION_HIGHLIGHTS: SetCaptionHighlightsArgs,
    ProposalToolName.SET_BROLL_REQUESTS: SetBrollRequestsArgs,
    ProposalToolName.SET_LOOK_AUDIO_EFFECTS: SetLookAudioEffectsArgs,
}


class ProposalAction(BaseModel):
    id: str = Field(pattern=r"^action_[0-9]{2}_[0-9a-f]{12}$")
    ordinal: int = Field(ge=0, le=20)
    tool_name: ProposalToolName
    tool_version: Literal["1"] = "1"
    args: dict[str, Any]
    rationale: str = Field(min_length=1, max_length=240)
    confidence: float = Field(ge=0, le=1)
    dependency_ids: list[str] = Field(default_factory=list, max_length=20)
    risk_flags: list[str] = Field(default_factory=list, max_length=10)
    dry_run_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_typed_args(self) -> ProposalAction:
        model = TOOL_ARGUMENT_MODELS.get(self.tool_name)
        if model is None:
            raise ValueError("unknown proposal tool")
        self.args = model.model_validate(self.args).model_dump(mode="json")
        if self.id in self.dependency_ids:
            raise ValueError("proposal action cannot depend on itself")
        return self


class ArtifactRevisionDigest(BaseModel):
    upload_id: UUID
    media_revision: int = Field(ge=1)
    media_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    transcript_id: UUID
    transcript_revision: str
    visual_manifest_id: UUID | None = None
    visual_manifest_version: int | None = Field(default=None, ge=1)
    visual_manifest_status: str
    base_active_plan_id: UUID | None = None
    base_active_plan_revision: int | None = Field(default=None, ge=1)
    base_timeline_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class MultimodalProposalContext(BaseModel):
    version: Literal["ai-edit-context-v1"] = AI_CONTEXT_VERSION
    workspace_id: UUID
    project_id: UUID
    artifacts: ArtifactRevisionDigest
    transcript: dict[str, Any]
    audio_intelligence: dict[str, Any] | None = None
    visual: dict[str, Any]
    base_plan: dict[str, Any] | None = None
    controls: dict[str, Any]
    modality_degradation: list[str] = Field(default_factory=list)


class ProposalProvenance(BaseModel):
    provider: str | None = None
    model: str | None = None
    model_revision: str | None = None
    route_id: str | None = None
    gateway_request_id: str | None = None
    planner_source: str | None = None
    prompt_version: str
    tool_registry_version: str = AI_TOOL_REGISTRY_VERSION
    orchestrator_version: str = AI_ORCHESTRATOR_VERSION
    editorial_memory: dict[str, Any] | None = None


class ProposalCostUsage(BaseModel):
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    actual_cost_usd: float | None = Field(default=None, ge=0)
    usage: dict[str, int | float] = Field(default_factory=dict)
    latency_ms: int | None = Field(default=None, ge=0)


class ProposalResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    state: ProposalState
    revision: int = Field(ge=1)
    request_id: str
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    base_active_plan_id: UUID | None = None
    base_active_plan_revision: int | None = None
    upload_id: UUID
    media_revision: int
    transcript_id: UUID
    artifact_revisions: ArtifactRevisionDigest
    overall_rationale: str | None = Field(default=None, max_length=500)
    confidence: float | None = Field(default=None, ge=0, le=1)
    degradation_flags: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    actions: list[ProposalAction] = Field(default_factory=list)
    provenance: ProposalProvenance
    validation_history_codes: list[str] = Field(default_factory=list)
    retry_history_codes: list[str] = Field(default_factory=list)
    fallback_history_codes: list[str] = Field(default_factory=list)
    cost: ProposalCostUsage = Field(default_factory=ProposalCostUsage)
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime


class ProposalListResponse(BaseModel):
    proposals: list[ProposalResponse]


class ProposalCreateResult(BaseModel):
    proposal: ProposalResponse
    job_id: str
    status_url: str
    deduplicated: bool = False


class ProposalDecisionRequest(JsonStringBodyMixin, BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ProposalDecisionType
    expected_proposal_revision: int = Field(ge=1)
    expected_base_active_plan_id: UUID | None
    expected_base_active_plan_revision: int | None = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    accepted_action_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)


class ProposalDecisionResponse(BaseModel):
    decision_id: UUID
    proposal_id: UUID
    decision: ProposalDecisionType
    accepted_action_ids: list[str]
    result_hash: str
    edit_plan_id: UUID | None = None
    edit_plan_revision: int | None = None
    proposal: ProposalResponse
    idempotent_replay: bool = False
