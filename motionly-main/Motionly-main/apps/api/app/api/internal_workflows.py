from __future__ import annotations

from fastapi import APIRouter, Header, Request

from app.api.errors import ApiError
from app.api.internal_jobs import require_worker_token
from app.schemas.workflow_proposals import (
    WorkflowProposalEnsureRequest,
    WorkflowProposalEnsureResponse,
)
from app.schemas.workflow_renders import (
    WorkflowDraftRenderEnsureRequest,
    WorkflowDraftRenderEnsureResponse,
)
from app.services.offload import run_blocking
from app.services.workflow_proposals import ensure_workflow_proposal
from app.services.workflow_renders import ensure_workflow_draft_render, render_queue_for

router = APIRouter(prefix="/internal/workflows", tags=["internal workflows"])


@router.post("/proposals/ensure", response_model=WorkflowProposalEnsureResponse)
async def ensure_proposal(
    payload: WorkflowProposalEnsureRequest,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> WorkflowProposalEnsureResponse:
    await require_worker_token(request, x_motionly_worker_token)
    state = request.app.state
    proposal = await run_blocking(
        ensure_workflow_proposal,
        workflow_repository=state.workflow_repository,
        proposal_repository=state.proposal_repository,
        plan_repository=state.plan_repository,
        project_repository=state.project_repository,
        workflow_run_id=payload.workflow_run_id,
        workspace_id=payload.workspace_id,
        project_id=payload.project_id,
        request_hash=payload.request_hash,
    )
    return WorkflowProposalEnsureResponse(
        workflow_run_id=payload.workflow_run_id,
        proposal_id=proposal.id,
        proposal_revision=proposal.revision,
        request_id=proposal.request_id,
        context_digest=proposal.context_digest,
        proposal_state=proposal.state.value,
    )


@router.post("/draft-renders/ensure", response_model=WorkflowDraftRenderEnsureResponse)
async def ensure_draft_render(
    payload: WorkflowDraftRenderEnsureRequest,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> WorkflowDraftRenderEnsureResponse:
    await require_worker_token(request, x_motionly_worker_token)
    job = await ensure_workflow_draft_render(
        state=request.app.state,
        workflow_run_id=payload.workflow_run_id,
        workspace_id=payload.workspace_id,
        project_id=payload.project_id,
        request_hash=payload.request_hash,
    )
    if (
        job.workflow_run_id is None
        or job.props_hash is None
        or job.render_engine is None
        or job.source_plan_revision is None
        or job.source_media_revision is None
    ):
        raise ApiError(
            502,
            "workflow_render_response_invalid",
            "Canonical workflow render persistence returned incomplete lineage.",
        )
    return WorkflowDraftRenderEnsureResponse(
        workflow_run_id=job.workflow_run_id,
        render_job_id=job.id,
        props_hash=job.props_hash,
        render_engine=job.render_engine,
        render_queue=render_queue_for(job),
        source_plan_revision=job.source_plan_revision,
        source_media_revision=job.source_media_revision,
    )
