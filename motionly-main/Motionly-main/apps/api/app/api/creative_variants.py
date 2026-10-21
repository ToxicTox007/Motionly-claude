from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import Actor, get_actor, get_plan_repository
from app.api.errors import ApiError
from app.api.plans import _update_plan_with_revision
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.creative_variants import (
    CreativeVariantApplyRequest,
    CreativeVariantApplyResponse,
    CreativeVariantReviewStatus,
    CreativeVariantSelectionRequest,
    CreativeVariantSetCreateRequest,
    CreativeVariantSetListResponse,
    CreativeVariantSetPayload,
    CreativeVariantSetResponse,
)
from app.schemas.renders import RenderCreateRequest
from app.schemas.scale import ScaleResourceStatus
from app.services.creative_variants import apply_creative_variant, build_creative_variants
from app.services.edit_planner import summarize_plan

router = APIRouter(
    prefix="/projects/{project_id}/creative-variants",
    tags=["creative variant testing"],
)

ActorDep = Annotated[Actor, Depends(get_actor)]
PlanRepositoryDep = Annotated[InMemoryPlanRepository, Depends(get_plan_repository)]


async def _render_applied_plan(
    *,
    project_id: UUID,
    plan_id: UUID,
    request: Request,
    actor: Actor,
    plan_repository: InMemoryPlanRepository,
):
    """Queue a render of the applied plan through the existing render route.

    This is a module-level seam (not an inline call) so tests can inject a
    fast-completing render target the Task-15 way by monkeypatching this symbol,
    without touching the production render route's own internals.
    """
    from app.api.renders import _create_render_target

    return await _create_render_target(
        project_id=project_id,
        payload=RenderCreateRequest(edit_plan_id=plan_id),
        request=request,
        actor=actor,
        repository=request.app.state.render_repository,
        billing_repository=request.app.state.billing_repository,
        plan_repository=plan_repository,
        render_queue=request.app.state.render_queue,
        broll_repository=getattr(request.app.state, "broll_repository", None),
    )


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
        applied_variant_id=payload.applied_variant_id,
        applied_plan_id=payload.applied_plan_id,
        applied_plan_revision=payload.applied_plan_revision,
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


@router.post("/{variant_set_id}/apply", response_model=CreativeVariantApplyResponse)
async def apply_creative_variant_direction(
    project_id: UUID,
    variant_set_id: UUID,
    payload: CreativeVariantApplyRequest,
    actor: ActorDep,
    request: Request,
    plan_repository: PlanRepositoryDep,
) -> CreativeVariantApplyResponse:
    """Apply a chosen variant to the active plan, creating a new plan version.

    The variant's primary dimension is turned into concrete plan changes and
    written through the standard revision-enforced path (``expected_revision``
    on the plan → ``_update_plan_with_revision``), so a stale plan revision
    raises the standard ``collaboration_revision_conflict`` 409. Optionally
    queues a render of the new plan version through the existing render route.
    """
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
            "This variant set changed. Reload before applying a direction.",
            details={"current_revision": record.revision},
        )
    persisted_payload = CreativeVariantSetPayload.model_validate(record.payload)
    variant = next(
        (item for item in persisted_payload.variants if item.id == payload.variant_id),
        None,
    )
    if variant is None:
        raise ApiError(
            422,
            "creative_variant_not_found",
            "Choose a variant from this creative test set.",
        )

    active_plan = await _repository_call(
        plan_repository,
        "active_plan_async",
        "active_plan",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    applied = apply_creative_variant(
        plan=active_plan.plan,
        variant=variant,
        broll_density=active_plan.broll_density,
    )
    summary = summarize_plan(
        applied.plan,
        style_classification=active_plan.summary.style_classification,
    )
    # Standard revision-enforced write: a stale plan revision raises
    # RevisionConflictError, surfaced as the standard 409 by the global handler.
    updated_plan = await _update_plan_with_revision(
        plan_repository,
        plan_id=active_plan.id,
        expected_revision=payload.expected_plan_revision,
        new_plan=applied.plan,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        summary=summary,
        target_duration_s=applied.plan.target_duration_s,
        change_type="creative_variant_apply",
        request=request,
    )
    # Persist the applied B-roll density on the plan record (only B-roll changes
    # it; other dimensions pass the plan's current density straight through).
    updated_plan.broll_density = applied.broll_density

    updated_payload = persisted_payload.model_copy(
        update={
            "selected_variant_id": variant.id,
            "review_status": CreativeVariantReviewStatus.APPLIED,
            "review_only": False,
            "applied_variant_id": variant.id,
            "applied_plan_id": updated_plan.id,
            "applied_plan_revision": updated_plan.revision,
        }
    )
    updated_set = await _repository_call(
        request.app.state.scale_repository,
        "update_if_revision_async",
        "update_if_revision",
        record=record,
        expected_revision=payload.expected_revision,
        payload=updated_payload.model_dump(mode="json"),
        revision=record.revision + 1,
    )

    render_enqueued = False
    render_job_id: UUID | None = None
    if payload.render:
        render_response = await _render_applied_plan(
            project_id=project_id,
            plan_id=updated_plan.id,
            request=request,
            actor=actor,
            plan_repository=plan_repository,
        )
        render_enqueued = True
        render_job_id = render_response.job_id

    return CreativeVariantApplyResponse(
        variant_set=_variant_set_response(updated_set),
        plan=updated_plan.to_response(),
        plan_revision=updated_plan.revision,
        applied_dimension=applied.dimension,
        render_enqueued=render_enqueued,
        render_job_id=render_job_id,
    )
