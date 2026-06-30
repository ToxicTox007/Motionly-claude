from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import Actor, get_actor, get_project_repository
from app.repositories.projects import InMemoryProjectRepository
from app.schemas.projects import (
    ProjectCreateRequest,
    ProjectDeleteResponse,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)

router = APIRouter(prefix="/projects", tags=["projects"])

ActorDep = Annotated[Actor, Depends(get_actor)]
ProjectRepositoryDep = Annotated[InMemoryProjectRepository, Depends(get_project_repository)]


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    payload: ProjectCreateRequest,
    request: Request,
    actor: ActorDep,
    repository: ProjectRepositoryDep,
) -> ProjectResponse:
    project = repository.create_project(
        workspace_id=actor.workspace_id,
        created_by=actor.user_id,
        title=payload.title,
    )
    upload_repository = getattr(request.app.state, "upload_repository", None)
    if hasattr(upload_repository, "seed_project"):
        upload_repository.seed_project(
            project_id=project.id,
            workspace_id=project.workspace_id,
            title=project.title,
        )
    return project.to_response()


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    actor: ActorDep,
    repository: ProjectRepositoryDep,
) -> ProjectListResponse:
    return ProjectListResponse(
        projects=[
            project.to_response()
            for project in repository.list_projects(workspace_id=actor.workspace_id)
        ],
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    actor: ActorDep,
    repository: ProjectRepositoryDep,
) -> ProjectResponse:
    return repository.get_project(
        project_id=project_id,
        workspace_id=actor.workspace_id,
    ).to_response()


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    actor: ActorDep,
    repository: ProjectRepositoryDep,
) -> ProjectResponse:
    return repository.update_project(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        title=payload.title,
        status=payload.status,
    ).to_response()


@router.delete("/{project_id}", response_model=ProjectDeleteResponse)
async def delete_project(
    project_id: UUID,
    actor: ActorDep,
    repository: ProjectRepositoryDep,
) -> ProjectDeleteResponse:
    repository.delete_project(project_id=project_id, workspace_id=actor.workspace_id)
    return ProjectDeleteResponse(id=project_id)
