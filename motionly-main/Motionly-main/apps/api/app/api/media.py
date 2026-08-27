"""Scoped media-reference refresh, quota display, and worker signing endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies import Actor, get_actor, get_project_repository
from app.api.internal_jobs import require_worker_token
from app.repositories.projects import InMemoryProjectRepository
from app.services.object_references import ObjectReferenceService
from app.services.offload import run_blocking
from app.services.storage_quota import StorageQuotaService

router = APIRouter(tags=["media"])

ActorDep = Annotated[Actor, Depends(get_actor)]
ProjectRepositoryDep = Annotated[
    InMemoryProjectRepository, Depends(get_project_repository)
]


class RefreshUrlsRequest(BaseModel):
    reference_ids: list[UUID] = Field(min_length=1, max_length=100)

    @field_validator("reference_ids")
    @classmethod
    def unique_reference_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("reference_ids must be unique")
        return value


class RefreshedUrlItem(BaseModel):
    reference_id: UUID
    object_type: str
    url: str
    expires_at: datetime


class RefreshUrlsResponse(BaseModel):
    refreshed: list[RefreshedUrlItem]
    count: int


class QuotaResponse(BaseModel):
    used_bytes: int
    reserved_bytes: int
    max_bytes: int
    used_object_count: int
    reserved_object_count: int
    max_objects: int
    percentage: float


class WorkerSignRequest(BaseModel):
    workspace_id: UUID
    project_id: UUID
    reference_ids: list[UUID] = Field(min_length=1, max_length=100)

    @field_validator("reference_ids")
    @classmethod
    def unique_reference_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("reference_ids must be unique")
        return value


class WorkerSignResponse(BaseModel):
    signed_urls: dict[UUID, str]
    expires_at: datetime
    ttl_seconds: int


def get_object_reference_service(request: Request) -> ObjectReferenceService:
    return request.app.state.object_reference_service


def get_storage_quota_service(request: Request) -> StorageQuotaService:
    return request.app.state.storage_quota_service


ObjectReferenceServiceDep = Annotated[
    ObjectReferenceService, Depends(get_object_reference_service)
]
StorageQuotaServiceDep = Annotated[StorageQuotaService, Depends(get_storage_quota_service)]


async def _verify_project(repository, *, project_id: UUID, workspace_id: UUID) -> None:
    async_method = getattr(repository, "get_project_async", None)
    if async_method is not None:
        await async_method(project_id=project_id, workspace_id=workspace_id)
        return
    await run_blocking(
        repository.get_project,
        project_id=project_id,
        workspace_id=workspace_id,
    )


@router.post(
    "/projects/{project_id}/media/refresh-urls",
    response_model=RefreshUrlsResponse,
)
async def refresh_media_urls(
    project_id: UUID,
    payload: RefreshUrlsRequest,
    actor: ActorDep,
    project_repository: ProjectRepositoryDep,
    obj_ref_service: ObjectReferenceServiceDep,
) -> RefreshUrlsResponse:
    await _verify_project(
        project_repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    refreshed = await obj_ref_service.refresh_urls(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        reference_ids=payload.reference_ids,
        principal_id=actor.user_id,
    )
    items = [
        RefreshedUrlItem(
            reference_id=item.reference_id,
            object_type=item.object_type,
            url=item.url,
            expires_at=item.expires_at,
        )
        for item in refreshed
    ]
    return RefreshUrlsResponse(refreshed=items, count=len(items))


@router.get(
    "/projects/{project_id}/media/quota",
    response_model=QuotaResponse,
)
async def get_storage_quota(
    project_id: UUID,
    actor: ActorDep,
    project_repository: ProjectRepositoryDep,
    quota_service: StorageQuotaServiceDep,
) -> QuotaResponse:
    await _verify_project(
        project_repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    usage = await quota_service.get_usage(actor.workspace_id)
    return QuotaResponse(
        used_bytes=usage.used_bytes,
        reserved_bytes=usage.reserved_bytes,
        max_bytes=usage.max_bytes,
        used_object_count=usage.used_object_count,
        reserved_object_count=usage.reserved_object_count,
        max_objects=usage.max_objects,
        percentage=usage.percentage,
    )


@router.post(
    "/internal/media/sign-urls",
    response_model=WorkerSignResponse,
    dependencies=[Depends(require_worker_token)],
)
async def sign_worker_urls(
    payload: WorkerSignRequest,
    request: Request,
) -> WorkerSignResponse:
    service: ObjectReferenceService = request.app.state.object_reference_service
    signed = await service.get_worker_urls(
        workspace_id=payload.workspace_id,
        project_id=payload.project_id,
        reference_ids=payload.reference_ids,
    )
    expires_at = next(iter(signed.values())).expires_at
    return WorkerSignResponse(
        signed_urls={reference_id: result.url for reference_id, result in signed.items()},
        expires_at=expires_at,
        ttl_seconds=ObjectReferenceService.WORKER_TTL,
    )
