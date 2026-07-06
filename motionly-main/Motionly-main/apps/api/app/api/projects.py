from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import Actor, get_actor, get_project_repository
from app.repositories.projects import InMemoryProjectRepository
from app.schemas.projects import (
    ProjectCreateRequest,
    ProjectDeleteResponse,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.services.offload import run_blocking

router = APIRouter(prefix="/projects", tags=["projects"])

ActorDep = Annotated[Actor, Depends(get_actor)]
ProjectRepositoryDep = Annotated[InMemoryProjectRepository, Depends(get_project_repository)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return await run_blocking(getattr(repository, sync_name), **kwargs)


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    payload: ProjectCreateRequest,
    request: Request,
    actor: ActorDep,
    repository: ProjectRepositoryDep,
) -> ProjectResponse:
    project = await _repository_call(
        repository,
        "create_project_async",
        "create_project",
        workspace_id=actor.workspace_id,
        created_by=actor.user_id,
        title=payload.title,
    )
    upload_repository = getattr(request.app.state, "upload_repository", None)
    if hasattr(upload_repository, "seed_project"):
        await run_blocking(
            upload_repository.seed_project,
            project_id=project.id,
            workspace_id=project.workspace_id,
            title=project.title,
        )
    return project.to_response()


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    actor: ActorDep,
    repository: ProjectRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ProjectListResponse:
    projects = await _repository_call(
        repository,
        "list_projects_async",
        "list_projects",
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    return ProjectListResponse(
        projects=[project.to_response() for project in projects],
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    actor: ActorDep,
    repository: ProjectRepositoryDep,
) -> ProjectResponse:
    project = await _repository_call(
        repository,
        "get_project_async",
        "get_project",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    return project.to_response()


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    actor: ActorDep,
    repository: ProjectRepositoryDep,
) -> ProjectResponse:
    project = await _repository_call(
        repository,
        "update_project_async",
        "update_project",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        title=payload.title,
        status=payload.status,
    )
    return project.to_response()


@router.delete("/{project_id}", response_model=ProjectDeleteResponse)
async def delete_project(
    project_id: UUID,
    actor: ActorDep,
    repository: ProjectRepositoryDep,
) -> ProjectDeleteResponse:
    await _repository_call(
        repository,
        "delete_project_async",
        "delete_project",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    return ProjectDeleteResponse(id=project_id)
