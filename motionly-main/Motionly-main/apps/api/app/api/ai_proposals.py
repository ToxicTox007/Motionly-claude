from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import Actor, get_actor, get_proposal_repository
from app.schemas.ai_proposals import (
    ProposalDecisionRequest,
    ProposalDecisionResponse,
    ProposalListResponse,
    ProposalResponse,
)
from app.services.ai_feedback import record_plan_suggestions

router = APIRouter(prefix="/projects/{project_id}/proposals", tags=["AI edit proposals"])

ActorDep = Annotated[Actor, Depends(get_actor)]
ProposalRepositoryDep = Annotated[Any, Depends(get_proposal_repository)]


@router.get("", response_model=ProposalListResponse)
async def list_proposals(
    project_id: UUID,
    actor: ActorDep,
    repository: ProposalRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ProposalListResponse:
    records = repository.list(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    return ProposalListResponse(proposals=[record.to_response() for record in records])


@router.get("/{proposal_id}", response_model=ProposalResponse)
async def get_proposal(
    project_id: UUID,
    proposal_id: UUID,
    actor: ActorDep,
    repository: ProposalRepositoryDep,
) -> ProposalResponse:
    return repository.get(
        proposal_id=proposal_id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    ).to_response()


@router.post("/{proposal_id}/decisions", response_model=ProposalDecisionResponse)
async def decide_proposal(
    project_id: UUID,
    proposal_id: UUID,
    payload: ProposalDecisionRequest,
    request: Request,
    actor: ActorDep,
    repository: ProposalRepositoryDep,
) -> ProposalDecisionResponse:
    result = repository.decide(
        proposal_id=proposal_id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        user_id=actor.user_id,
        payload=payload,
    )
    if result.edit_plan_id is not None:
        plan = repository.plan_repository.get_plan(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            plan_id=result.edit_plan_id,
        )
        record_plan_suggestions(
            request=request,
            workspace_id=actor.workspace_id,
            project_id=project_id,
            user_id=actor.user_id,
            plan=plan,
        )
    return result
