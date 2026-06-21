from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.billing import build_billing_status
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
from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer
from app.schemas.effects import (
    EffectsConfigRequest,
    EffectsConfigResponse,
    EffectsStatus,
    EffectsUpdateResponse,
)
from app.services.ai_feedback import record_ai_feedback
from app.services.effects import (
    DEFAULT_AFTER_EFFECTS_PRESETS,
    after_effects_handoff_urls,
    build_effects_cache_key,
    defaults_for_preset,
    effect_count_limit_per_30s,
    normalize_motion_and_gradient,
    preview_urls,
    render_time_impact,
)
from app.services.queue import EffectsQueue

router = APIRouter(prefix="/projects/{project_id}/effects", tags=["Effects"])

ActorDep = Annotated[Actor, Depends(get_actor)]
EffectsRepositoryDep = Annotated[InMemoryEffectsRepository, Depends(get_effects_repository)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]
PlanRepositoryDep = Annotated[InMemoryPlanRepository, Depends(get_plan_repository)]
EffectsQueueDep = Annotated[EffectsQueue, Depends(get_effects_queue)]


@router.get("", response_model=EffectsConfigResponse | None)
async def get_effects(
    project_id: UUID,
    actor: ActorDep,
    repository: EffectsRepositoryDep,
) -> EffectsConfigResponse | None:
    config = repository.latest_config(project_id=project_id, workspace_id=actor.workspace_id)
    return repository.response_for_config(config) if config else None


@router.patch("", response_model=EffectsUpdateResponse, status_code=202)
async def update_effects(
    project_id: UUID,
    payload: EffectsConfigRequest,
    request: Request,
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
        and not build_billing_status(
            actor.workspace_id,
            billing_repository,
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
    texture = (
        preset_defaults["texture"] if payload.reset_to_defaults else payload.texture
    )
    motion_effects = (
        preset_defaults["motion_effects"]
        if payload.reset_to_defaults
        else payload.motion_effects
    )
    motion = preset_defaults["motion"] if payload.reset_to_defaults else payload.motion
    background_removal = (
        preset_defaults["background_removal"]
        if payload.reset_to_defaults
        else payload.background_removal
    )
    gradient_ramp = (
        preset_defaults["gradient_ramp"]
        if payload.reset_to_defaults
        else payload.gradient_ramp
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
    job = effects_queue.enqueue_effects_config(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        edit_plan_id=active_plan.id if active_plan else None,
    )

    if not payload.force:
        cached = repository.find_cached_config(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            cache_key=cache_key,
        )
        if cached:
            return EffectsUpdateResponse(
                config=repository.response_for_config(cached),
                job_id=job.id,
                cached=True,
            )

    preview_before_url, preview_after_url = preview_urls(project_id, payload.preset)
    next_version = _next_effects_version(
        repository=repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    after_effects_project_url, correction_manifest_url = after_effects_handoff_urls(
        project_id,
        next_version,
    )
    config = repository.create_config(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        edit_plan_id=active_plan.id if active_plan else None,
        status=EffectsStatus.READY,
        preset=payload.preset,
        lut_intensity=lut_intensity,
        texture=texture,
        motion_effects=motion_effects,
        motion=motion,
        background_removal=background_removal,
        gradient_ramp=gradient_ramp,
        after_effects_presets=after_effects_presets,
        after_effects_project_url=after_effects_project_url,
        correction_manifest_url=correction_manifest_url,
        effect_count_limit_per_30s=effect_count_limit_per_30s(motion_effects, motion),
        render_time_impact=render_time_impact(
            texture=texture,
            motion_effects=motion_effects,
            motion=motion,
            background_removal=background_removal,
            gradient_ramp=gradient_ramp,
            after_effects_presets=after_effects_presets,
        ),
        preview_before_url=preview_before_url,
        preview_after_url=preview_after_url,
        cache_key=cache_key,
    )
    record_ai_feedback(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=active_plan.id if active_plan else None,
        event_type=AIEditFeedbackEventType.CHANGED,
        layer=AIEditLayer.EFFECTS,
        source="look_editor",
        decision_id=f"{active_plan.id if active_plan else project_id}:effects",
        action="update_effects",
        ai_payload=(
            {
                "color_grade": active_plan.plan.color_grade,
                "effects": active_plan.plan.effects,
                "zoom_moments": [
                    moment.model_dump(mode="json")
                    for moment in active_plan.plan.zoom_moments
                ],
            }
            if active_plan
            else {}
        ),
        user_payload=repository.response_for_config(config).model_dump(mode="json"),
        metadata={"reset_to_defaults": payload.reset_to_defaults},
    )
    return EffectsUpdateResponse(config=repository.response_for_config(config), job_id=job.id)


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
