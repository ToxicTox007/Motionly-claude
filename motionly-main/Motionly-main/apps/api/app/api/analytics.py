from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import Actor, get_actor, get_analytics_repository
from app.repositories.analytics import InMemoryAnalyticsRepository
from app.schemas.analytics import (
    AIEditAnalyticsResponse,
    AIEditFeedbackEventCreateRequest,
    AIEditFeedbackEventResponse,
    AIEditLayer,
    FineTuningDatasetResponse,
)

router = APIRouter(prefix="/projects/{project_id}/analytics", tags=["Analytics"])

ActorDep = Annotated[Actor, Depends(get_actor)]
AnalyticsRepositoryDep = Annotated[InMemoryAnalyticsRepository, Depends(get_analytics_repository)]


@router.get("/ai-edits", response_model=AIEditAnalyticsResponse)
async def get_ai_edit_analytics(
    project_id: UUID,
    actor: ActorDep,
    repository: AnalyticsRepositoryDep,
    layer: Annotated[AIEditLayer | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AIEditAnalyticsResponse:
    events = repository.list_events(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        layer=layer,
        limit=limit,
    )
    return AIEditAnalyticsResponse(
        summary=repository.summarize(project_id=project_id, workspace_id=actor.workspace_id),
        events=[event.to_response() for event in events],
    )


@router.post("/ai-edits", response_model=AIEditFeedbackEventResponse, status_code=201)
async def record_ai_edit_feedback(
    project_id: UUID,
    payload: AIEditFeedbackEventCreateRequest,
    actor: ActorDep,
    repository: AnalyticsRepositoryDep,
) -> AIEditFeedbackEventResponse:
    event = repository.record_event(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=payload.edit_plan_id,
        event_type=payload.event_type,
        layer=payload.layer,
        decision_id=payload.decision_id,
        action=payload.action,
        source=payload.source,
        ai_payload=payload.ai_payload,
        user_payload=payload.user_payload,
        metadata=payload.metadata,
    )
    return event.to_response()


@router.get("/fine-tuning-dataset", response_model=FineTuningDatasetResponse)
async def get_fine_tuning_dataset(
    project_id: UUID,
    actor: ActorDep,
    repository: AnalyticsRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 250,
) -> FineTuningDatasetResponse:
    examples = repository.fine_tuning_examples(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    return FineTuningDatasetResponse(
        project_id=project_id,
        example_count=len(examples),
        examples=examples,
    )
