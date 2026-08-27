from __future__ import annotations

from typing import Any
from uuid import UUID

from app.api.errors import ApiError
from app.repositories.workflows import WorkflowArtifactType, WorkflowRunState
from app.schemas.captions import TranscriptStatus
from app.schemas.plans import EDIT_PLAN_PROMPT_VERSION, PlanCreateRequest
from app.schemas.uploads import UploadStatus
from app.schemas.visual import VisualManifestStatus
from app.services.ai_proposals import (
    build_multimodal_context,
    request_identity,
)
from app.services.transcript_quality import is_transcript_usable_for_planning


def ensure_workflow_proposal(
    *,
    workflow_repository: Any,
    proposal_repository: Any,
    plan_repository: Any,
    project_repository: Any,
    workflow_run_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    request_hash: str,
) -> Any:
    """Ensure and link one canonical proposal without crossing the history data boundary."""
    run = workflow_repository.get_workflow(
        workflow_run_id=workflow_run_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if run.workflow_type != "first_draft" or run.workflow_version != "first-draft-v2":
        raise ApiError(
            409,
            "workflow_proposal_version_invalid",
            "Only first-draft-v2 can create a workflow proposal.",
        )
    if run.request_hash != request_hash:
        raise ApiError(
            409,
            "workflow_request_hash_mismatch",
            "Workflow proposal identity does not match the canonical run.",
        )
    if run.state != WorkflowRunState.PROPOSAL_READY:
        raise ApiError(
            409,
            "workflow_not_proposal_ready",
            "Canonical analysis must complete before proposal generation.",
        )

    upload = plan_repository.get_active_upload(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    if (
        upload.id != run.uploaded_video_id
        or upload.status != UploadStatus.READY
        or max(1, int(getattr(upload, "media_revision", 1)))
        != run.source_media_revision
        or getattr(upload, "source_object_reference_id", None)
        != run.source_object_reference_id
        or not getattr(upload, "verified_sha256", None)
    ):
        raise ApiError(
            409,
            "workflow_source_stale",
            "The canonical workflow source is missing, failed, or stale.",
        )

    transcript = plan_repository.latest_transcript(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    if (
        transcript is None
        or transcript.uploaded_video_id != upload.id
        or transcript.status != TranscriptStatus.READY
        or not transcript.words
        or not is_transcript_usable_for_planning(transcript.quality_status)
    ):
        raise ApiError(
            409,
            "workflow_transcript_not_ready",
            "Canonical transcription is missing, failed, or unusable.",
        )

    visual_repository = getattr(plan_repository, "visual_repository", None)
    latest_visual = (
        visual_repository.latest_for_upload(
            uploaded_video_id=upload.id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        if visual_repository is not None
        else None
    )
    if (
        latest_visual is None
        or latest_visual.status != VisualManifestStatus.READY
        or latest_visual.manifest is None
    ):
        raise ApiError(
            409,
            "workflow_visual_not_ready",
            "Canonical visual analysis is missing, failed, or stale.",
        )

    created_by = run.started_by
    if created_by is None:
        project = project_repository.get_project(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        created_by = project.created_by
    if created_by is None:  # pragma: no cover - persistent schemas make this defensive
        raise ApiError(
            409,
            "workflow_initiating_user_missing",
            "A canonical initiating user is required for proposal generation.",
        )

    context = build_multimodal_context(
        repository=plan_repository,
        workspace_id=workspace_id,
        project_id=project_id,
        payload=PlanCreateRequest(),
    )
    if (
        context.artifacts.upload_id != run.uploaded_video_id
        or context.artifacts.media_revision != run.source_media_revision
        or context.artifacts.transcript_id != transcript.id
        or context.artifacts.visual_manifest_id != latest_visual.id
        or context.artifacts.visual_manifest_version != latest_visual.version
        or context.artifacts.visual_manifest_status != VisualManifestStatus.READY.value
    ):
        raise ApiError(
            409,
            "workflow_analysis_stale",
            "Proposal context does not match canonical workflow analysis.",
        )

    deterministic_key = f"workflow-proposal:{workflow_run_id}"
    proposal, _ = proposal_repository.create_queued(
        context=context,
        request_id=request_identity(
            context=context,
            idempotency_key=deterministic_key,
        ),
        created_by=created_by,
        prompt_version=EDIT_PLAN_PROMPT_VERSION,
    )

    workflow_repository.link_artifact(
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        artifact_type=WorkflowArtifactType.TRANSCRIPT,
        artifact_id=transcript.id,
    )
    workflow_repository.link_artifact(
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        artifact_type=WorkflowArtifactType.VISUAL_MANIFEST,
        artifact_id=latest_visual.id,
        artifact_revision=latest_visual.version,
    )
    workflow_repository.link_artifact(
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        artifact_type=WorkflowArtifactType.PROPOSAL,
        artifact_id=proposal.id,
        artifact_revision=proposal.revision,
    )
    # In-memory repositories do not have the SQL proposal-link trigger.
    if hasattr(proposal, "workflow_run_id"):
        proposal.workflow_run_id = workflow_run_id
    return proposal
