from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import Actor, get_actor, get_analytics_repository
from app.repositories.analytics import InMemoryAnalyticsRepository
from app.schemas.analytics import (
    AIEditAnalyticsResponse,
    AIEditFeedbackEventCreateRequest,
    AIEditFeedbackEventResponse,
    AIEditLayer,
)

router = APIRouter(prefix="/projects/{project_id}/analytics", tags=["Analytics"])

ActorDep = Annotated[Actor, Depends(get_actor)]
AnalyticsRepositoryDep = Annotated[InMemoryAnalyticsRepository, Depends(get_analytics_repository)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


@router.get("/ai-edits", response_model=AIEditAnalyticsResponse)
async def get_ai_edit_analytics(
    project_id: UUID,
    actor: ActorDep,
    repository: AnalyticsRepositoryDep,
    layer: Annotated[AIEditLayer | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AIEditAnalyticsResponse:
    events = await _repository_call(
        repository,
        "list_events_async",
        "list_events",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        layer=layer,
        limit=limit,
    )
    summary = await _repository_call(
        repository,
        "summarize_async",
        "summarize",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    return AIEditAnalyticsResponse(
        summary=summary,
        events=[event.to_response() for event in events],
    )


@router.post("/ai-edits", response_model=AIEditFeedbackEventResponse, status_code=201)
async def record_ai_edit_feedback(
    project_id: UUID,
    payload: AIEditFeedbackEventCreateRequest,
    actor: ActorDep,
    repository: AnalyticsRepositoryDep,
    request: Request,
) -> AIEditFeedbackEventResponse:
    event = await _repository_call(
        repository,
        "record_event_async",
        "record_event",
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
    learning_repository = getattr(request.app.state, "editorial_learning_repository", None)
    if learning_repository is not None and getattr(
        request.app.state.settings, "editorial_learning_enabled", False
    ):
        from app.services.editorial_learning import capture_consented_feedback

        capture_consented_feedback(
            repository=learning_repository,
            workspace_id=actor.workspace_id,
            project_id=project_id,
            user_id=actor.user_id,
            source_event_id=event.id,
            layer=payload.layer,
            event_type=payload.event_type,
            ai_payload=payload.ai_payload,
            user_payload=payload.user_payload,
        )
    return event.to_response()
