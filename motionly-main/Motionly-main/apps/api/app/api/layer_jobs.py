from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import Actor, get_actor
from app.schemas.jobs import LayerJobStatusResponse
from app.services.job_status import resolve_layer_job_status

router = APIRouter(prefix="/projects/{project_id}/jobs", tags=["layer jobs"])

ActorDep = Annotated[Actor, Depends(get_actor)]


@router.get("/{job_id}", response_model=LayerJobStatusResponse)
async def get_layer_job_status(
    project_id: UUID,
    job_id: str,
    actor: ActorDep,
    request: Request,
) -> LayerJobStatusResponse:
    return resolve_layer_job_status(
        request=request,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        job_id=job_id,
    )
