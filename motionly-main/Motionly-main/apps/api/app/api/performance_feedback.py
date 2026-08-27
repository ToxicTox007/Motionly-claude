from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import Actor, get_actor
from app.api.errors import ApiError
from app.schemas.performance_feedback import (
    PerformanceFeedbackInsightsResponse,
    PerformanceFeedbackPolicyResponse,
    PerformanceFeedbackPolicyUpdateRequest,
    PerformanceFeedbackRevocationRequest,
    PerformanceFeedbackStatusResponse,
    PerformanceMetricIngestRequest,
    PerformanceMetricResponse,
)
from app.schemas.publishing import PublishPlatform, PublishStatus
from app.services.performance_feedback import (
    build_edit_feature_snapshot,
    build_performance_suggestions,
)

router = APIRouter(tags=["performance feedback"])
ActorDep = Annotated[Actor, Depends(get_actor)]


def _repository(request: Request):
    repository = getattr(request.app.state, "performance_feedback_repository", None)
    if repository is None:
        raise ApiError(
            503,
            "performance_feedback_unavailable",
            "Performance feedback controls are unavailable.",
        )
    return repository


def _verify_project(*, project_id: UUID, request: Request, actor: Actor) -> None:
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )


def _enabled(request: Request) -> None:
    if not getattr(request.app.state.settings, "performance_feedback_enabled", False):
        raise ApiError(
            403,
            "performance_feedback_disabled",
            "Performance feedback is disabled for this deployment.",
        )


def _status(request: Request) -> PerformanceFeedbackStatusResponse:
    settings = request.app.state.settings
    return PerformanceFeedbackStatusResponse(
        enabled=bool(getattr(settings, "performance_feedback_enabled", False)),
        policy_version=str(
            getattr(settings, "performance_feedback_policy_version", "performance-feedback/v1")
        ).strip(),
        default_retention_days=int(getattr(settings, "performance_feedback_retention_days", 90)),
        minimum_examples=int(getattr(settings, "performance_feedback_minimum_examples", 3)),
    )


@router.get(
    "/projects/{project_id}/performance-feedback/status",
    response_model=PerformanceFeedbackStatusResponse,
)
async def get_status(
    project_id: UUID, request: Request, actor: ActorDep
) -> PerformanceFeedbackStatusResponse:
    _verify_project(project_id=project_id, request=request, actor=actor)
    return _status(request)


@router.get(
    "/projects/{project_id}/performance-feedback/policy",
    response_model=PerformanceFeedbackPolicyResponse,
)
async def get_policy(
    project_id: UUID, request: Request, actor: ActorDep
) -> PerformanceFeedbackPolicyResponse:
    _verify_project(project_id=project_id, request=request, actor=actor)
    return (
        _repository(request)
        .get_policy_or_default(workspace_id=actor.workspace_id, user_id=actor.user_id)
        .response()
    )


@router.put(
    "/projects/{project_id}/performance-feedback/policy",
    response_model=PerformanceFeedbackPolicyResponse,
)
async def set_policy(
    project_id: UUID,
    payload: PerformanceFeedbackPolicyUpdateRequest,
    request: Request,
    actor: ActorDep,
) -> PerformanceFeedbackPolicyResponse:
    _verify_project(project_id=project_id, request=request, actor=actor)
    _enabled(request)
    required_version = _status(request).policy_version
    if payload.policy_version != required_version:
        raise ApiError(
            422,
            "performance_feedback_policy_version_required",
            "Reload performance feedback settings and acknowledge the current policy version.",
        )
    return (
        _repository(request)
        .set_policy_if_revision(
            workspace_id=actor.workspace_id,
            user_id=actor.user_id,
            **payload.model_dump(),
        )
        .response()
    )


@router.post(
    "/projects/{project_id}/performance-feedback/revoke",
    response_model=PerformanceFeedbackPolicyResponse,
)
async def revoke_policy(
    project_id: UUID,
    payload: PerformanceFeedbackRevocationRequest,
    request: Request,
    actor: ActorDep,
) -> PerformanceFeedbackPolicyResponse:
    _verify_project(project_id=project_id, request=request, actor=actor)
    return (
        _repository(request)
        .revoke(workspace_id=actor.workspace_id, user_id=actor.user_id, reason=payload.reason)
        .response()
    )


@router.post(
    "/projects/{project_id}/publish/{publish_job_id}/performance-metrics",
    response_model=PerformanceMetricResponse,
    status_code=201,
)
async def ingest_performance_metric(
    project_id: UUID,
    publish_job_id: UUID,
    payload: PerformanceMetricIngestRequest,
    request: Request,
    actor: ActorDep,
) -> PerformanceMetricResponse:
    """Accept only the aggregate metric allow-list for a completed Motionly publish job."""
    _verify_project(project_id=project_id, request=request, actor=actor)
    _enabled(request)
    repository = _repository(request)
    policy = repository.get_policy_or_default(
        workspace_id=actor.workspace_id, user_id=actor.user_id
    )
    if policy.revoked_at or not policy.metrics_ingestion_enabled:
        raise ApiError(
            403,
            "performance_metrics_consent_required",
            "Explicit performance-metric ingestion consent is required before metrics are stored.",
        )
    publish_job = request.app.state.publishing_repository.get_job(
        job_id=publish_job_id, workspace_id=actor.workspace_id
    )
    if publish_job.project_id != project_id:
        raise ApiError(
            422, "performance_publish_project_mismatch", "Publish job is not in this project."
        )
    if publish_job.status != PublishStatus.PUBLISHED:
        raise ApiError(
            409,
            "performance_post_not_published",
            "Performance metrics can only be recorded after the post is published.",
        )

    export = request.app.state.render_repository.get_export(
        export_id=publish_job.export_id, workspace_id=actor.workspace_id
    )
    if export.project_id != project_id:
        raise ApiError(
            422, "performance_export_project_mismatch", "Published export is not in this project."
        )
    render_job = request.app.state.render_repository.get_render_job(
        project_id=project_id,
        render_job_id=export.render_job_id,
        workspace_id=actor.workspace_id,
    )
    plan = None
    if render_job.edit_plan_id:
        try:
            plan = request.app.state.plan_repository.get_plan(
                project_id=project_id,
                workspace_id=actor.workspace_id,
                plan_id=render_job.edit_plan_id,
            )
        except ApiError:
            plan = None
    caption_track = None
    if render_job.caption_track_id:
        try:
            caption_track = request.app.state.caption_repository.get_caption_track(
                project_id=project_id,
                track_id=render_job.caption_track_id,
                workspace_id=actor.workspace_id,
            )
        except ApiError:
            caption_track = None
    features = build_edit_feature_snapshot(
        render_job=render_job, export=export, plan=plan, caption_track=caption_track
    )
    metric = repository.capture_metric(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        publish_job_id=publish_job.id,
        platform=publish_job.platform,
        **payload.model_dump(),
        **features.as_dict(),
        duration_seconds=(export.duration_ms / 1000 if export.duration_ms else None),
    )
    if metric is None:
        # The repository repeats the consent gate atomically to fail closed if consent changed.
        raise ApiError(
            403,
            "performance_metrics_consent_required",
            "Performance-metric consent changed before this snapshot could be stored.",
        )
    return metric.response()


@router.get(
    "/projects/{project_id}/performance-feedback/insights",
    response_model=PerformanceFeedbackInsightsResponse,
)
async def get_insights(
    project_id: UUID,
    request: Request,
    actor: ActorDep,
    platform: PublishPlatform | None = None,
) -> PerformanceFeedbackInsightsResponse:
    _verify_project(project_id=project_id, request=request, actor=actor)
    status = _status(request)
    policy = _repository(request).get_policy_or_default(
        workspace_id=actor.workspace_id, user_id=actor.user_id
    )
    if not status.enabled:
        return PerformanceFeedbackInsightsResponse(
            status="feature_disabled",
            policy_revision=policy.revision,
            minimum_examples=status.minimum_examples,
        )
    if policy.revoked_at or not policy.metrics_ingestion_enabled:
        return PerformanceFeedbackInsightsResponse(
            status="consent_required",
            policy_revision=policy.revision,
            minimum_examples=status.minimum_examples,
        )
    if not policy.suggestions_enabled:
        return PerformanceFeedbackInsightsResponse(
            status="suggestions_disabled",
            policy_revision=policy.revision,
            minimum_examples=status.minimum_examples,
        )
    metrics = _repository(request).list_eligible_metrics(
        workspace_id=actor.workspace_id, user_id=actor.user_id, platform=platform
    )
    suggestions = build_performance_suggestions(metrics, minimum_examples=status.minimum_examples)
    return PerformanceFeedbackInsightsResponse(
        status="ready" if suggestions else "collecting",
        policy_revision=policy.revision,
        minimum_examples=status.minimum_examples,
        suggestions=suggestions,
    )
