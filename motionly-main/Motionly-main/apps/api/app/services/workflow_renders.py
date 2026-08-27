from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

from app.api.errors import ApiError
from app.api.renders import _create_render_target
from app.repositories.renders import RenderJobRecord
from app.repositories.workflows import WorkflowArtifactType, WorkflowRunState
from app.schemas.plans import EditPlanStatus
from app.schemas.renders import RenderCreateRequest, RenderEngine
from app.schemas.uploads import UploadStatus


def render_queue_for(job: RenderJobRecord) -> str:
    if job.render_engine == RenderEngine.REMOTION_PREMIUM:
        return "render_remotion"
    if str(job.manifest.get("compute_tier") or "cpu") == "gpu":
        return "render_gpu"
    return "render"


async def ensure_workflow_draft_render(
    *,
    state: Any,
    workflow_run_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    request_hash: str,
) -> RenderJobRecord:
    run = state.workflow_repository.get_workflow(
        workflow_run_id=workflow_run_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if run.workflow_type != "first_draft" or run.workflow_version != "first-draft-v2":
        raise ApiError(
            409,
            "workflow_render_version_invalid",
            "Only first-draft-v2 can ensure a workflow draft render.",
        )
    if run.request_hash != request_hash:
        raise ApiError(
            409,
            "workflow_request_hash_mismatch",
            "Draft render identity does not match the canonical workflow.",
        )
    if run.state not in {WorkflowRunState.ASSEMBLING, WorkflowRunState.DRAFT_RENDERING}:
        raise ApiError(
            409,
            "workflow_not_assembling",
            "The approved workflow is not assembling a draft.",
        )
    if run.active_plan_id is None or run.active_plan_revision is None:
        raise ApiError(
            409,
            "workflow_active_plan_missing",
            "The approved active plan revision is missing.",
        )

    artifacts = state.workflow_repository.list_artifacts(
        workflow_run_id=workflow_run_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    timeline = next(
        (
            artifact
            for artifact in artifacts
            if artifact.artifact_type == WorkflowArtifactType.TIMELINE_REVISION
        ),
        None,
    )
    decision = next(
        (
            artifact
            for artifact in artifacts
            if artifact.artifact_type == WorkflowArtifactType.DECISION
        ),
        None,
    )
    if (
        timeline is None
        or timeline.artifact_revision != run.active_plan_revision
        or decision is None
    ):
        raise ApiError(
            409,
            "workflow_approval_lineage_missing",
            "The linked approval decision and immutable timeline revision are required.",
        )

    plan = state.plan_repository.get_plan(
        project_id=project_id,
        workspace_id=workspace_id,
        plan_id=run.active_plan_id,
    )
    if (
        not plan.is_active
        or plan.status != EditPlanStatus.READY
        or plan.revision != run.active_plan_revision
    ):
        raise ApiError(
            409,
            "workflow_active_plan_stale",
            "The linked approved plan is no longer the active render-ready revision.",
        )
    upload = state.render_repository.get_active_upload(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    if (
        upload.id != run.uploaded_video_id
        or upload.status != UploadStatus.READY
        or upload.media_revision != run.source_media_revision
        or upload.source_object_reference_id != run.source_object_reference_id
        or not upload.verified_sha256
    ):
        raise ApiError(
            409,
            "workflow_source_stale",
            "The canonical source media reference is missing or stale.",
        )

    existing = state.render_repository.get_workflow_draft_render(
        workflow_run_id=workflow_run_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if existing is not None:
        if (
            run.state != WorkflowRunState.DRAFT_RENDERING
            or existing.edit_plan_id != run.active_plan_id
            or existing.source_plan_revision != run.active_plan_revision
            or existing.timeline_revision_id != timeline.artifact_id
            or existing.source_media_revision != run.source_media_revision
            or existing.props_hash is None
            or existing.render_engine is None
        ):
            raise ApiError(
                409,
                "workflow_render_identity_conflict",
                "The persisted workflow draft render has conflicting lineage.",
            )
        return existing

    if run.state != WorkflowRunState.ASSEMBLING:
        raise ApiError(
            409,
            "workflow_render_target_missing",
            "The workflow render target is missing after draft rendering began.",
        )
    actor_id = run.started_by
    if actor_id is None:
        project = state.project_repository.get_project(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        actor_id = project.created_by
    if actor_id is None:
        raise ApiError(
            409,
            "workflow_initiating_user_missing",
            "A canonical initiating user is required for draft rendering.",
        )

    result = await _create_render_target(
        project_id=project_id,
        payload=RenderCreateRequest(
            include_captions=False,
            include_broll=True,
            edit_plan_id=run.active_plan_id,
        ),
        request=SimpleNamespace(app=SimpleNamespace(state=state)),
        actor=SimpleNamespace(user_id=actor_id, workspace_id=workspace_id),
        repository=state.render_repository,
        billing_repository=state.billing_repository,
        plan_repository=state.plan_repository,
        render_queue=state.render_queue,
        broll_repository=getattr(state, "broll_repository", None),
        workflow_repository=state.workflow_repository,
        workflow_run_id=workflow_run_id,
        timeline_revision_id=timeline.artifact_id,
        publish=False,
    )
    if not isinstance(result, RenderJobRecord):
        raise ApiError(500, "workflow_render_response_invalid", "Draft render target is invalid.")
    return result
