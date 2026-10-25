from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.billing import build_billing_status_async
from app.api.dependencies import (
    Actor,
    get_actor,
    get_billing_repository,
    get_effects_queue,
    get_effects_repository,
    get_plan_repository,
)
from app.api.errors import ApiError
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.effects import InMemoryEffectsRepository
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.effects import (
    EffectsConfigRequest,
    EffectsConfigResponse,
    EffectsUpdateResponse,
)
from app.services.effects import (
    DEFAULT_AFTER_EFFECTS_PRESETS,
    build_effects_cache_key,
    defaults_for_preset,
    normalize_motion_and_gradient,
)
from app.services.layer_job_enqueue import (
    build_queued_layer_response_fields,
    prepare_heavy_job_enqueue,
    serialize_layer_job_payload,
)
from app.services.offload import run_blocking
from app.services.queue import EffectsQueue

router = APIRouter(prefix="/projects/{project_id}/effects", tags=["Effects"])

ActorDep = Annotated[Actor, Depends(get_actor)]
EffectsRepositoryDep = Annotated[InMemoryEffectsRepository, Depends(get_effects_repository)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]
PlanRepositoryDep = Annotated[InMemoryPlanRepository, Depends(get_plan_repository)]
EffectsQueueDep = Annotated[EffectsQueue, Depends(get_effects_queue)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


@router.get("", response_model=EffectsConfigResponse | None)
async def get_effects(
    project_id: UUID,
    actor: ActorDep,
    repository: EffectsRepositoryDep,
) -> EffectsConfigResponse | None:
    config = await _repository_call(
        repository,
        "latest_config_async",
        "latest_config",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    return repository.response_for_config(config) if config else None


@router.patch("", response_model=EffectsUpdateResponse, status_code=202)
async def update_effects(
    project_id: UUID,
    payload: EffectsConfigRequest,
    request: Request,
    response: Response,
    actor: ActorDep,
    repository: EffectsRepositoryDep,
    billing_repository: BillingRepositoryDep,
    plan_repository: PlanRepositoryDep,
    effects_queue: EffectsQueueDep,
) -> EffectsUpdateResponse:
    premium_requested = (
        bool(payload.motion_effects)
        or bool(payload.motion.keyframes)
        or bool(payload.motion.clip_effects)
        or payload.motion.ken_burns.enabled
        or payload.motion.speed_ramp.enabled
        or payload.motion.beat_impact.enabled
        or payload.motion.auto_reframe.enabled
        or payload.background_removal.enabled
        or payload.gradient_ramp.enabled
        or bool(payload.after_effects_presets)
    )
    if (
        request.app.state.settings.billing_enabled
        and not (
            await build_billing_status_async(actor.workspace_id, billing_repository)
        ).entitlements.premium_effects
        and premium_requested
    ):
        raise ApiError(
            402,
            "premium_effects_plan_required",
            "Upgrade to Pro to use premium effects.",
        )
    active_plan = next(
        (
            plan
            for plan in plan_repository.list_plans(
                project_id=project_id,
                workspace_id=actor.workspace_id,
            )
            if plan.is_active
        ),
        None,
    )
    preset_defaults = defaults_for_preset(payload.preset)
    lut_intensity = (
        float(preset_defaults["lut_intensity"])
        if payload.reset_to_defaults
        else payload.lut_intensity
    )
    texture = preset_defaults["texture"] if payload.reset_to_defaults else payload.texture
    motion_effects = (
        preset_defaults["motion_effects"] if payload.reset_to_defaults else payload.motion_effects
    )
    motion = preset_defaults["motion"] if payload.reset_to_defaults else payload.motion
    background_removal = (
        preset_defaults["background_removal"]
        if payload.reset_to_defaults
        else payload.background_removal
    )
    gradient_ramp = (
        preset_defaults["gradient_ramp"] if payload.reset_to_defaults else payload.gradient_ramp
    )
    motion_effects, motion, gradient_ramp = normalize_motion_and_gradient(
        motion_effects=motion_effects,
        motion=motion,
        gradient_ramp=gradient_ramp,
    )
    after_effects_presets = (
        preset_defaults["after_effects_presets"]
        if payload.reset_to_defaults
        else payload.after_effects_presets or DEFAULT_AFTER_EFFECTS_PRESETS
    )
    cache_key = build_effects_cache_key(
        edit_plan_id=active_plan.id if active_plan else None,
        preset=payload.preset,
        lut_intensity=lut_intensity,
        texture=texture,
        motion_effects=motion_effects,
        motion=motion,
        background_removal=background_removal,
        gradient_ramp=gradient_ramp,
        after_effects_presets=after_effects_presets,
    )
    execution_id = (
        f"sync-complete--effects--{project_id}--{active_plan.id if active_plan else 'none'}"
    )

    if not payload.force:
        cached = await _repository_call(
            repository,
            "find_cached_config_async",
            "find_cached_config",
            project_id=project_id,
            workspace_id=actor.workspace_id,
            cache_key=cache_key,
        )
        if cached:
            return EffectsUpdateResponse(
                config=repository.response_for_config(cached),
                job_id=execution_id,
                status="ready",
                cached=True,
            )

    prepare_heavy_job_enqueue(request=request)
    job = await run_blocking(
        effects_queue.enqueue_effects_config,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        edit_plan_id=active_plan.id if active_plan else None,
        job_payload=serialize_layer_job_payload(payload=payload, user_id=actor.user_id),
    )
    return EffectsUpdateResponse(
        **build_queued_layer_response_fields(
            project_id=project_id,
            job_id=job.id,
            response=response,
        )
    )


def _next_effects_version(
    *,
    repository: InMemoryEffectsRepository,
    project_id: UUID,
    workspace_id: UUID,
) -> int:
    next_version = getattr(repository, "next_version", None)
    if callable(next_version):
        return next_version(project_id=project_id, workspace_id=workspace_id)
    return 1
