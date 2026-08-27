from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from app.api.errors import ApiError
from app.schemas.ai_proposals import (
    AI_ORCHESTRATOR_VERSION,
    AI_TOOL_REGISTRY_VERSION,
    TOOL_ARGUMENT_MODELS,
    ArtifactRevisionDigest,
    MultimodalProposalContext,
    ProposalAction,
    ProposalToolName,
)
from app.schemas.plans import EditPlan, PlanCreateRequest
from app.services.timeline_compiler import migrate_legacy_plan_to_timeline


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_jsonable_python(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def tenant_scoped_hash(*, workspace_id: UUID, purpose: str, value: Any) -> str:
    return canonical_hash({"workspace_id": str(workspace_id), "purpose": purpose, "value": value})


def _safe_audio_intelligence(raw_payload: dict[str, object]) -> dict[str, Any] | None:
    value = raw_payload.get("audio_intelligence")
    return dict(value) if isinstance(value, dict) else None


def _visual_summary(record: Any | None) -> tuple[dict[str, Any], list[str]]:
    if record is None:
        return {"identity": None, "status": "unavailable", "summary": {}}, [
            "visual_context_unavailable"
        ]
    manifest = getattr(record, "manifest", None)
    status = str(getattr(getattr(record, "status", None), "value", None) or "unavailable")
    summary = dict(getattr(manifest, "summary", {}) or {}) if manifest is not None else {}
    visual = {
        "identity": {
            "id": str(record.id),
            "version": int(record.version),
            "schema_version": int(record.schema_version),
            "provider": str(record.provider),
            "model": str(record.model),
            "cache_key": record.cache_key,
        },
        "status": status,
        "summary": summary,
    }
    flags = [] if status == "ready" and manifest is not None else [f"visual_context_{status}"]
    return visual, flags


def build_multimodal_context(
    *,
    repository: Any,
    workspace_id: UUID,
    project_id: UUID,
    payload: PlanCreateRequest,
) -> MultimodalProposalContext:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
    upload = repository.get_active_upload(project_id=project_id, workspace_id=workspace_id)
    transcript = repository.latest_transcript(project_id=project_id, workspace_id=workspace_id)
    if transcript is None:
        raise ApiError(404, "transcript_not_found", "Generate a transcript before planning edits.")
    if transcript.uploaded_video_id != upload.id:
        raise ApiError(
            409, "proposal_media_context_stale", "Transcript is not for the active media."
        )

    active_plan = None
    try:
        active_plan = repository.active_plan(project_id=project_id, workspace_id=workspace_id)
    except ApiError as exc:
        if exc.code != "edit_plan_not_found":
            raise

    latest_visual = None
    visual_repository = getattr(repository, "visual_repository", None)
    if visual_repository is not None:
        latest_visual = visual_repository.latest_for_upload(
            uploaded_video_id=upload.id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
    visual, degradation = _visual_summary(latest_visual)
    media_revision = max(1, int(getattr(upload, "media_revision", 1)))
    transcript_payload = {
        "id": str(transcript.id),
        "provider": transcript.provider,
        "model": transcript.model,
        "language": transcript.language,
        "confidence": transcript.confidence,
        "duration_ms": transcript.duration_ms,
        "quality_status": str(transcript.quality_status),
        "quality_reasons": list(transcript.quality_reasons),
        "words": [word.model_dump(mode="json") for word in transcript.words],
    }
    transcript_revision = tenant_scoped_hash(
        workspace_id=workspace_id,
        purpose="transcript-revision",
        value=transcript_payload,
    )
    timeline_hash = (
        tenant_scoped_hash(
            workspace_id=workspace_id,
            purpose="active-timeline",
            value=(active_plan.plan.timeline or active_plan.plan),
        )
        if active_plan is not None
        else None
    )
    artifacts = ArtifactRevisionDigest(
        upload_id=upload.id,
        media_revision=media_revision,
        media_sha256=getattr(upload, "verified_sha256", None),
        transcript_id=transcript.id,
        transcript_revision=transcript_revision,
        visual_manifest_id=getattr(latest_visual, "id", None),
        visual_manifest_version=getattr(latest_visual, "version", None),
        visual_manifest_status=str(visual["status"]),
        base_active_plan_id=getattr(active_plan, "id", None),
        base_active_plan_revision=getattr(active_plan, "revision", None),
        base_timeline_hash=timeline_hash,
    )
    controls = payload.model_dump(mode="json", exclude={"force", "idempotency_key"})
    return MultimodalProposalContext(
        workspace_id=workspace_id,
        project_id=project_id,
        artifacts=artifacts,
        transcript=transcript_payload,
        audio_intelligence=_safe_audio_intelligence(transcript.raw_payload),
        visual=visual,
        base_plan=(
            {
                "id": str(active_plan.id),
                "revision": active_plan.revision,
                "timeline_hash": timeline_hash,
            }
            if active_plan is not None
            else None
        ),
        controls=controls,
        modality_degradation=degradation,
    )


def context_digest(context: MultimodalProposalContext) -> str:
    return tenant_scoped_hash(
        workspace_id=context.workspace_id,
        purpose=context.version,
        value=context,
    )


def request_identity(*, context: MultimodalProposalContext, idempotency_key: str | None) -> str:
    identity: dict[str, Any] = {"project_id": str(context.project_id)}
    if idempotency_key is not None:
        identity["idempotency_key"] = idempotency_key
    else:
        identity["context_digest"] = context_digest(context)
    return tenant_scoped_hash(
        workspace_id=context.workspace_id,
        purpose="proposal-request-v1",
        value=identity,
    )


@dataclass(frozen=True)
class ToolRegistry:
    version: str = AI_TOOL_REGISTRY_VERSION

    def validate_actions(self, actions: Iterable[ProposalAction]) -> list[ProposalAction]:
        ordered = sorted(actions, key=lambda item: item.ordinal)
        ids = [action.id for action in ordered]
        if len(ids) != len(set(ids)):
            raise ApiError(422, "proposal_duplicate_action", "Proposal action IDs must be unique.")
        if [action.ordinal for action in ordered] != list(range(len(ordered))):
            raise ApiError(
                422, "proposal_action_order_invalid", "Proposal actions must be contiguous."
            )
        known = set(ids)
        for action in ordered:
            if action.tool_name not in TOOL_ARGUMENT_MODELS:
                raise ApiError(422, "proposal_unknown_tool", "Proposal contains an unknown tool.")
            try:
                TOOL_ARGUMENT_MODELS[action.tool_name].model_validate(action.args)
            except ValidationError as exc:
                raise ApiError(
                    422, "proposal_tool_args_invalid", "Proposal tool arguments are invalid."
                ) from exc
            if len(action.dependency_ids) != len(set(action.dependency_ids)):
                raise ApiError(
                    422, "proposal_duplicate_dependency", "Action dependencies must be unique."
                )
            if any(dependency not in known for dependency in action.dependency_ids):
                raise ApiError(
                    422, "proposal_dependency_unknown", "Action dependency does not exist."
                )
        self._assert_acyclic(ordered)
        return ordered

    @staticmethod
    def _assert_acyclic(actions: list[ProposalAction]) -> None:
        graph = {action.id: action.dependency_ids for action in actions}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(action_id: str) -> None:
            if action_id in visiting:
                raise ApiError(
                    422, "proposal_dependency_cycle", "Proposal dependencies contain a cycle."
                )
            if action_id in visited:
                return
            visiting.add(action_id)
            for dependency in graph[action_id]:
                visit(dependency)
            visiting.remove(action_id)
            visited.add(action_id)

        for action_id in graph:
            visit(action_id)

    def validate_selection(
        self,
        *,
        actions: list[ProposalAction],
        accepted_action_ids: list[str],
        partial: bool,
    ) -> list[ProposalAction]:
        ordered = self.validate_actions(actions)
        if len(accepted_action_ids) != len(set(accepted_action_ids)):
            raise ApiError(
                422, "proposal_duplicate_selection", "Accepted action IDs must be unique."
            )
        by_id = {action.id: action for action in ordered}
        unknown = [action_id for action_id in accepted_action_ids if action_id not in by_id]
        if unknown:
            raise ApiError(422, "proposal_action_unknown", "An accepted action ID does not exist.")
        selected = [action for action in ordered if action.id in accepted_action_ids]
        if partial and (not selected or len(selected) == len(ordered)):
            raise ApiError(
                422,
                "proposal_partial_selection_invalid",
                "Partial approval requires a nonempty proper subset of actions.",
            )
        selected_ids = {action.id for action in selected}
        if any(
            dependency not in selected_ids
            for action in selected
            for dependency in action.dependency_ids
        ):
            raise ApiError(
                422,
                "proposal_dependency_not_selected",
                "Accepted actions must include all dependencies.",
            )
        return selected


TOOL_REGISTRY = ToolRegistry()


def _action(
    *,
    ordinal: int,
    tool_name: ProposalToolName,
    args: dict[str, Any],
    rationale: str,
    confidence: float,
    dependency_ids: list[str] | None = None,
    risk_flags: list[str] | None = None,
) -> ProposalAction:
    typed_args = TOOL_ARGUMENT_MODELS[tool_name].model_validate(args).model_dump(mode="json")
    dry_run_hash = canonical_hash({"tool": tool_name.value, "version": "1", "args": typed_args})
    identity_hash = canonical_hash(
        {"ordinal": ordinal, "tool": tool_name.value, "args": typed_args}
    )
    action_id = f"action_{ordinal:02d}_{identity_hash[:12]}"
    return ProposalAction(
        id=action_id,
        ordinal=ordinal,
        tool_name=tool_name,
        args=typed_args,
        rationale=rationale,
        confidence=confidence,
        dependency_ids=dependency_ids or [],
        risk_flags=risk_flags or [],
        dry_run_hash=dry_run_hash,
    )


def derive_proposal_actions(
    *,
    plan: EditPlan,
    has_base_plan: bool,
    degradation_flags: list[str],
) -> list[ProposalAction]:
    confidence = 0.82 if not degradation_flags else 0.68
    if not has_base_plan:
        return TOOL_REGISTRY.validate_actions(
            [
                _action(
                    ordinal=0,
                    tool_name=ProposalToolName.COMPLETE_FIRST_PLAN,
                    args={"plan": plan.model_dump(mode="json")},
                    rationale=(
                        "Create the first complete edit plan from the validated "
                        "multimodal brief."
                    ),
                    confidence=confidence,
                    risk_flags=degradation_flags,
                )
            ]
        )
    actions = [
        _action(
            ordinal=0,
            tool_name=ProposalToolName.SET_CUTS,
            args={
                "cuts": [cut.model_dump(mode="json") for cut in plan.cuts],
                "target_duration_s": plan.target_duration_s,
            },
            rationale="Apply the proposed pacing and source selections.",
            confidence=confidence,
        ),
        _action(
            ordinal=1,
            tool_name=ProposalToolName.SET_CAPTION_HIGHLIGHTS,
            args={
                "caption_highlights": [
                    item.model_dump(mode="json") for item in plan.caption_highlights
                ]
            },
            rationale="Emphasize concise transcript moments without changing source meaning.",
            confidence=confidence,
        ),
        _action(
            ordinal=2,
            tool_name=ProposalToolName.SET_BROLL_REQUESTS,
            args={"broll_requests": [item.model_dump(mode="json") for item in plan.broll_requests]},
            rationale="Request supporting visuals at semantically relevant moments.",
            confidence=confidence,
            risk_flags=[flag for flag in degradation_flags if flag.startswith("visual_")],
        ),
        _action(
            ordinal=3,
            tool_name=ProposalToolName.SET_LOOK_AUDIO_EFFECTS,
            args={
                "style": plan.style.value,
                "music_mood": plan.music_mood,
                "effects": plan.effects,
                "color_grade": plan.color_grade,
                "zoom_moments": [item.model_dump(mode="json") for item in plan.zoom_moments],
                "risk_flags": plan.risk_flags,
            },
            rationale="Apply the proposed visual look, music direction, and restrained effects.",
            confidence=confidence,
            risk_flags=plan.risk_flags,
        ),
    ]
    return TOOL_REGISTRY.validate_actions(actions)


def apply_actions(
    *,
    base_plan: EditPlan | None,
    actions: list[ProposalAction],
    source_video_id: UUID,
    source_duration_ms: int,
) -> EditPlan:
    TOOL_REGISTRY.validate_actions(actions)
    projected = base_plan
    for action in actions:
        if action.tool_name == ProposalToolName.COMPLETE_FIRST_PLAN:
            projected = EditPlan.model_validate(action.args["plan"])
            continue
        if projected is None:
            raise ApiError(
                409, "proposal_base_plan_missing", "This action requires an active base plan."
            )
        projected_payload = projected.model_dump(mode="json")
        if action.tool_name == ProposalToolName.SET_CUTS:
            projected_payload.update(
                {
                    "cuts": action.args["cuts"],
                    "target_duration_s": action.args["target_duration_s"],
                    "timeline": None,
                }
            )
        elif action.tool_name == ProposalToolName.SET_CAPTION_HIGHLIGHTS:
            projected_payload["caption_highlights"] = action.args["caption_highlights"]
        elif action.tool_name == ProposalToolName.SET_BROLL_REQUESTS:
            projected_payload["broll_requests"] = action.args["broll_requests"]
        elif action.tool_name == ProposalToolName.SET_LOOK_AUDIO_EFFECTS:
            projected_payload.update(
                {
                    "style": action.args["style"],
                    "music_mood": action.args["music_mood"],
                    "effects": action.args["effects"],
                    "color_grade": action.args["color_grade"],
                    "zoom_moments": action.args["zoom_moments"],
                    "risk_flags": action.args["risk_flags"],
                }
            )
        else:  # pragma: no cover - enum and registry make this defensive
            raise ApiError(422, "proposal_unknown_tool", "Proposal contains an unknown tool.")
        projected = EditPlan.model_validate(projected_payload)
    if projected is None:
        raise ApiError(422, "proposal_noop_selection", "Approval must apply at least one action.")
    return migrate_legacy_plan_to_timeline(
        plan=projected,
        source_video_id=source_video_id,
        source_duration_ms=source_duration_ms,
    )


def proposal_result_hash(
    *, workspace_id: UUID, context_digest_value: str, plan: EditPlan, action_ids: list[str]
) -> str:
    return tenant_scoped_hash(
        workspace_id=workspace_id,
        purpose=f"proposal-result:{AI_ORCHESTRATOR_VERSION}",
        value={
            "context_digest": context_digest_value,
            "action_ids": action_ids,
            "plan": plan,
        },
    )
