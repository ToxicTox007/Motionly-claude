from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import (
    Actor,
    get_actor,
    get_regeneration_queue,
    get_regeneration_repository,
)
from app.repositories.regeneration import InMemoryRegenerationRepository
from app.schemas.regeneration import (
    REGENERATION_SCHEMA_VERSION,
    RegenerateRequest,
    RegenerateResponse,
)
from app.services.layer_job_enqueue import (
    build_queued_layer_response_fields,
    prepare_heavy_job_enqueue,
    serialize_layer_job_payload,
)
from app.services.queue import RegenerationQueue
from app.services.regeneration import (
    affected_layers_for_request,
    build_regeneration_cache_key,
    preserved_layers,
    settings_summary,
)

router = APIRouter(prefix="/projects/{project_id}/regenerate", tags=["regeneration"])

ActorDep = Annotated[Actor, Depends(get_actor)]
RegenerationRepositoryDep = Annotated[
    InMemoryRegenerationRepository,
    Depends(get_regeneration_repository),
]
RegenerationQueueDep = Annotated[RegenerationQueue, Depends(get_regeneration_queue)]


@router.post("", response_model=RegenerateResponse, status_code=202)
async def regenerate_project_layers(
    project_id: UUID,
    payload: RegenerateRequest,
    actor: ActorDep,
    repository: RegenerationRepositoryDep,
    regeneration_queue: RegenerationQueueDep,
    request: Request,
    response: Response,
) -> RegenerateResponse:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=actor.workspace_id)
    prepare_heavy_job_enqueue(request=request)

    affected_layers = affected_layers_for_request(payload)
    summary = settings_summary(payload)
    controls = repository.create_control_settings(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        schema_version=REGENERATION_SCHEMA_VERSION,
        controls=payload.controls,
        action=payload.action,
        export_options=payload.export_options,
        settings_summary=summary,
    )
    artifacts = repository.create_artifact_versions(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        layers=affected_layers,
        cache_key=build_regeneration_cache_key(payload),
        settings_summary=summary,
    )
    job = regeneration_queue.enqueue_regeneration(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        control_settings_id=controls.id,
        job_payload=serialize_layer_job_payload(payload=payload, user_id=actor.user_id),
    )
    return RegenerateResponse(
        affected_layers=affected_layers,
        preserved_layers=preserved_layers(affected_layers),
        controls=controls.to_response(),
        artifact_versions=[artifact.to_response() for artifact in artifacts],
        **build_queued_layer_response_fields(
            project_id=project_id,
            job_id=job.id,
            response=response,
        )
    )
