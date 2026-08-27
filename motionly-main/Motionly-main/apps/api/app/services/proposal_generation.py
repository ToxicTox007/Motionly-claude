from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.api.errors import ApiError
from app.schemas.ai_proposals import ProposalCostUsage, ProposalProvenance, ProposalState
from app.schemas.plans import EDIT_PLAN_PROMPT_VERSION, PlanCreateRequest
from app.services.ai_proposals import (
    build_multimodal_context,
    context_digest,
    derive_proposal_actions,
)
from app.services.edit_planner import normalize_plan, summarize_plan
from app.services.operations import monotonic_ms
from app.services.style_classifier import classify_edit_style
from app.services.timeline_compiler import migrate_legacy_plan_to_timeline


def _history_codes(values: list[str]) -> list[str]:
    codes: list[str] = []
    for value in values:
        code = None
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                code = parsed.get("code")
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        if not code:
            lowered = value.lower()
            if "repair" in lowered:
                code = "planner_repair_applied"
            elif "fallback" in lowered:
                code = "planner_fallback_used"
            elif ":" in value:
                code = "planner_validation_error"
            else:
                code = value[:80].replace(" ", "_")
        text = str(code)[:120]
        if text and text not in codes:
            codes.append(text)
    return codes[:30]


def _gateway_metadata(result: Any) -> dict[str, Any]:
    raw = result.raw_payload if isinstance(result.raw_payload, dict) else {}
    metadata = raw.get("model_gateway")
    return dict(metadata) if isinstance(metadata, dict) else {}


def execute_proposal_generation(
    *,
    proposal_repository: Any,
    plan_repository: Any,
    planner_provider: Any,
    workspace_id: UUID,
    project_id: UUID,
    proposal_id: UUID,
    settings: Any,
    billing_repository: Any = None,
    operations_repository: Any = None,
    metrics_registry: Any = None,
) -> dict[str, Any]:
    proposal = proposal_repository.mark_generating(
        proposal_id=proposal_id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    if proposal.state in {
        ProposalState.AWAITING_APPROVAL,
        ProposalState.APPROVED,
        ProposalState.PARTIALLY_APPROVED,
        ProposalState.REJECTED,
    }:
        return {"proposal_id": str(proposal.id), "status": proposal.state.value}

    controls = dict(proposal.context.controls)
    payload = PlanCreateRequest.model_validate(controls)
    current_context = build_multimodal_context(
        repository=plan_repository,
        workspace_id=workspace_id,
        project_id=project_id,
        payload=payload,
    )
    if context_digest(current_context) != proposal.context_digest:
        proposal_repository.mark_failed(
            proposal_id=proposal.id,
            project_id=project_id,
            workspace_id=workspace_id,
            failure_code="proposal_context_stale",
            validation_history_codes=["proposal_context_stale"],
        )
        raise ApiError(409, "proposal_context_stale", "Proposal inputs changed before generation.")

    transcript = plan_repository.latest_transcript(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    upload = plan_repository.get_active_upload(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    if transcript is None:
        raise ApiError(404, "transcript_not_found", "Generate a transcript before planning edits.")
    visual_manifest = None
    visual_repository = getattr(plan_repository, "visual_repository", None)
    if visual_repository is not None:
        ready = visual_repository.latest_ready_for_upload(
            uploaded_video_id=upload.id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        visual_manifest = ready.manifest if ready and ready.manifest else None

    style_classification = classify_edit_style(
        transcript=transcript,
        visual_manifest=visual_manifest,
        requested_style=payload.style,
        prompt=payload.prompt,
        feedback=payload.feedback,
    )
    provider_payload = payload.model_copy(
        update={
            "style": style_classification.effective_style,
            "feedback": "\n".join(
                part
                for part in (
                    f"Motionly proposal idempotency: {proposal.id}:{proposal.context_digest}",
                    payload.feedback,
                )
                if part
            )[:800],
        }
    )
    video_duration_ms = upload.metadata.duration_ms or transcript.duration_ms or 0
    started_ms = monotonic_ms()
    try:
        result = planner_provider.create_plan(
            transcript=transcript,
            payload=provider_payload,
            video_duration_ms=video_duration_ms,
            visual_manifest=visual_manifest,
            style_classification=style_classification,
            billing_repository=billing_repository,
            operations_repository=operations_repository,
        )
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        proposal_repository.mark_failed(
            proposal_id=proposal.id,
            project_id=project_id,
            workspace_id=workspace_id,
            failure_code=str(code),
            validation_history_codes=[str(code)],
        )
        raise

    plan = normalize_plan(
        plan=result.plan,
        words=transcript.words,
        duration_ms=video_duration_ms or result.plan.target_duration_s * 1000,
    )
    degradation = list(proposal.context.modality_degradation)
    if degradation:
        plan = plan.model_copy(
            update={"risk_flags": list(dict.fromkeys([*plan.risk_flags, *degradation]))[:10]}
        )
    plan = migrate_legacy_plan_to_timeline(
        plan=plan,
        source_video_id=upload.id,
        source_duration_ms=video_duration_ms or plan.target_duration_s * 1000,
    )
    actions = derive_proposal_actions(
        plan=plan,
        has_base_plan=proposal.artifacts.base_active_plan_id is not None,
        degradation_flags=degradation,
    )
    gateway = _gateway_metadata(result)
    usage = result.raw_payload.get("usage") if isinstance(result.raw_payload, dict) else {}
    safe_usage = {
        str(key): float(value)
        for key, value in (usage.items() if isinstance(usage, dict) else [])
        if key
        in {"prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"}
        and isinstance(value, int | float)
    }
    validation_codes = _history_codes(result.validation_errors)
    fallback_codes = []
    if result.fallback_reason:
        fallback_codes.append(str(result.fallback_reason)[:120])
    if "fallback" in result.planner_source or "fixture" in result.planner_source:
        fallback_codes.append(result.planner_source)
    retry_codes = [f"planner_retry_{index}" for index in range(1, result.retry_count + 1)]
    production = str(getattr(settings, "app_env", "test")) == "production"
    deterministic = result.planner_source in {"deterministic_fixture", "deterministic_fallback"}
    approval_ready = not (production and deterministic)
    actual_cost = gateway.get("actual_cost_usd")
    provenance = ProposalProvenance(
        provider=result.provider,
        model=result.model,
        model_revision=str(gateway.get("model_revision") or result.model),
        route_id=str(gateway.get("route_id")) if gateway.get("route_id") else None,
        gateway_request_id=(
            str(gateway.get("request_id") or gateway.get("gateway_request_id"))
            if gateway.get("request_id") or gateway.get("gateway_request_id")
            else None
        ),
        planner_source=result.planner_source,
        prompt_version=result.prompt_version or EDIT_PLAN_PROMPT_VERSION,
    )
    completed = proposal_repository.complete_generation(
        proposal_id=proposal.id,
        project_id=project_id,
        workspace_id=workspace_id,
        plan=plan,
        summary=summarize_plan(plan, style_classification=style_classification),
        actions=actions,
        provenance=provenance,
        overall_rationale=(
            f"Proposed {len(actions)} validated edit action"
            f"{'s' if len(actions) != 1 else ''} for {plan.style.value} pacing."
        ),
        confidence=min((action.confidence for action in actions), default=0.0),
        risk_flags=plan.risk_flags,
        validation_history_codes=validation_codes,
        retry_history_codes=retry_codes,
        fallback_history_codes=fallback_codes,
        cost=ProposalCostUsage(
            estimated_cost_usd=result.cost_estimate_usd,
            actual_cost_usd=float(actual_cost)
            if isinstance(actual_cost, int | float)
            else result.cost_estimate_usd,
            usage=safe_usage,
            latency_ms=result.latency_ms or (monotonic_ms() - started_ms),
        ),
        approval_ready=approval_ready,
        failure_code="production_deterministic_result_denied" if not approval_ready else None,
    )
    if metrics_registry is not None and not approval_ready:
        metrics_registry.observe(
            "fallback_degraded_mode_count",
            1,
            provider=result.provider,
            model=result.model,
            reason="proposal_fail_closed",
        )
    return {
        "proposal_id": str(completed.id),
        "status": completed.state.value,
        "result_hash": completed.result_hash,
        "failed": completed.state == ProposalState.FAILED,
        "error_code": completed.failure_code,
        "error_message": "Proposal was not approval-ready."
        if completed.state == ProposalState.FAILED
        else None,
    }
