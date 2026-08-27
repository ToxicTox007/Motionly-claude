from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import Actor, get_actor, get_plan_repository
from app.api.errors import ApiError
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.creative_variants import (
    CreativeVariantReviewStatus,
    CreativeVariantSelectionRequest,
    CreativeVariantSetCreateRequest,
    CreativeVariantSetListResponse,
    CreativeVariantSetPayload,
    CreativeVariantSetResponse,
)
from app.schemas.scale import ScaleResourceStatus
from app.services.creative_variants import build_creative_variants

router = APIRouter(
    prefix="/projects/{project_id}/creative-variants",
    tags=["creative variant testing"],
)

ActorDep = Annotated[Actor, Depends(get_actor)]
PlanRepositoryDep = Annotated[InMemoryPlanRepository, Depends(get_plan_repository)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


async def _ensure_project(project_repository, *, project_id: UUID, workspace_id: UUID) -> None:
    await _repository_call(
        project_repository,
        "get_project_async",
        "get_project",
        project_id=project_id,
        workspace_id=workspace_id,
    )


def _variant_set_response(record) -> CreativeVariantSetResponse:
    if record.project_id is None:
        raise ApiError(
            409, "creative_variant_project_missing", "Variant set is missing its project."
        )
    payload = CreativeVariantSetPayload.model_validate(record.payload)
    return CreativeVariantSetResponse(
        id=record.id,
        workspace_id=record.workspace_id,
        project_id=record.project_id,
        name=record.name,
        status=record.status,
        revision=record.revision,
        source_plan_id=payload.source_plan_id,
        source_plan_revision=payload.source_plan_revision,
        dimensions=payload.dimensions,
        variants=payload.variants,
        selected_variant_id=payload.selected_variant_id,
        review_status=payload.review_status,
        review_only=payload.review_only,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("", response_model=CreativeVariantSetListResponse)
async def list_creative_variant_sets(
    project_id: UUID,
    actor: ActorDep,
    request: Request,
    plan_repository: PlanRepositoryDep,
) -> CreativeVariantSetListResponse:
    await _ensure_project(
        request.app.state.project_repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    records = await _repository_call(
        request.app.state.scale_repository,
        "list_async",
        "list",
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="creative_variant_set",
        limit=30,
    )
    return CreativeVariantSetListResponse(
        variant_sets=[_variant_set_response(record) for record in records]
    )


@router.post("", response_model=CreativeVariantSetResponse, status_code=status.HTTP_201_CREATED)
async def create_creative_variant_set(
    project_id: UUID,
    payload: CreativeVariantSetCreateRequest,
    actor: ActorDep,
    request: Request,
    plan_repository: PlanRepositoryDep,
) -> CreativeVariantSetResponse:
    await _ensure_project(
        request.app.state.project_repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    try:
        active_plan = await _repository_call(
            plan_repository,
            "active_plan_async",
            "active_plan",
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
    except ApiError as exc:
        if exc.code != "edit_plan_not_found":
            raise
        raise ApiError(
            409,
            "creative_variant_active_plan_required",
            "Create or activate an edit plan before generating creative variants.",
        ) from exc
    variants = build_creative_variants(
        plan=active_plan.plan,
        broll_density=active_plan.broll_density,
        dimensions=payload.dimensions,
        variant_count=payload.variant_count,
    )
    persisted_payload = CreativeVariantSetPayload(
        source_plan_id=active_plan.id,
        source_plan_revision=active_plan.revision,
        dimensions=payload.dimensions,
        variants=variants,
    )
    record = await _repository_call(
        request.app.state.scale_repository,
        "create_async",
        "create",
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="creative_variant_set",
        name=payload.name,
        status=ScaleResourceStatus.READY,
        payload=persisted_payload.model_dump(mode="json"),
        result={
            "comparison_ready": True,
            "publish_blocked": True,
            "render_enqueued": False,
        },
    )
    return _variant_set_response(record)


@router.patch("/{variant_set_id}/selection", response_model=CreativeVariantSetResponse)
async def select_creative_variant(
    project_id: UUID,
    variant_set_id: UUID,
    payload: CreativeVariantSelectionRequest,
    actor: ActorDep,
    request: Request,
    plan_repository: PlanRepositoryDep,
) -> CreativeVariantSetResponse:
    await _ensure_project(
        request.app.state.project_repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    record = await _repository_call(
        request.app.state.scale_repository,
        "get_async",
        "get",
        resource_id=variant_set_id,
        workspace_id=actor.workspace_id,
    )
    if record.kind != "creative_variant_set" or record.project_id != project_id:
        raise ApiError(404, "creative_variant_set_not_found", "Creative variant set was not found.")
    if record.revision != payload.expected_revision:
        raise ApiError(
            409,
            "creative_variant_set_revision_conflict",
            "This variant set changed. Reload before choosing a direction.",
            details={"current_revision": record.revision},
        )
    persisted_payload = CreativeVariantSetPayload.model_validate(record.payload)
    if payload.variant_id not in {variant.id for variant in persisted_payload.variants}:
        raise ApiError(
            422,
            "creative_variant_not_found",
            "Choose a variant from this creative test set.",
        )
    updated_payload = persisted_payload.model_copy(
        update={
            "selected_variant_id": payload.variant_id,
            "review_status": CreativeVariantReviewStatus.SELECTED,
        }
    )
    updated = await _repository_call(
        request.app.state.scale_repository,
        "update_if_revision_async",
        "update_if_revision",
        record=record,
        expected_revision=payload.expected_revision,
        payload=updated_payload.model_dump(mode="json"),
        revision=record.revision + 1,
    )
    return _variant_set_response(updated)
