from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.admin import require_admin
from app.api.dependencies import Actor, get_actor
from app.api.errors import ApiError
from app.schemas.editorial_learning import (
    EditorialLearningDatasetListResponse,
    EditorialLearningDeploymentResponse,
    EditorialLearningPolicyResponse,
    EditorialLearningPolicyUpdateRequest,
    EditorialLearningPromotionRequest,
    EditorialLearningRevocationRequest,
    EditorialLearningRollbackRequest,
    EditorialLearningStatusResponse,
    EditorialMemoryIndicatorResponse,
)

router = APIRouter(tags=["editorial learning"])
ActorDep = Annotated[Actor, Depends(get_actor)]
AdminActorDep = Annotated[Actor, Depends(require_admin)]


def _repository(request: Request):
    repository = getattr(request.app.state, "editorial_learning_repository", None)
    if repository is None:
        raise ApiError(
            503, "editorial_learning_unavailable", "Editorial learning controls are unavailable."
        )
    return repository


def _enabled(request: Request) -> None:
    if not getattr(request.app.state.settings, "editorial_learning_enabled", False):
        raise ApiError(
            403,
            "editorial_learning_disabled",
            "Editorial learning is disabled for this deployment.",
        )


@router.get("/editorial-learning/status", response_model=EditorialLearningStatusResponse)
async def get_status(request: Request, actor: ActorDep) -> EditorialLearningStatusResponse:
    settings = request.app.state.settings
    return EditorialLearningStatusResponse(
        enabled=bool(getattr(settings, "editorial_learning_enabled", False)),
        policy_version=str(
            getattr(settings, "editorial_learning_policy_version", "editorial-consent/v1")
        ).strip(),
        default_retention_days=int(
            getattr(settings, "editorial_learning_retention_days", 90)
        ),
        minimum_examples=int(getattr(settings, "editorial_learning_minimum_examples", 100)),
    )


@router.get("/editorial-learning/policy", response_model=EditorialLearningPolicyResponse)
async def get_policy(request: Request, actor: ActorDep) -> EditorialLearningPolicyResponse:
    return (
        _repository(request)
        .get_policy_or_default(workspace_id=actor.workspace_id, user_id=actor.user_id)
        .response()
    )


@router.get(
    "/editorial-learning/projects/{project_id}/memory",
    response_model=EditorialMemoryIndicatorResponse,
)
async def get_memory_indicator(
    project_id: UUID,
    request: Request,
    actor: ActorDep,
) -> EditorialMemoryIndicatorResponse:
    """Surface the per-user, per-project "personalized from N edits" indicator.

    Only a promoted, consented, non-revoked deployment is reported as active.
    Withdrawing consent removes the effect, so this returns ``personalized`` =
    False even if a dataset was once promoted.
    """

    settings = request.app.state.settings
    enabled = bool(getattr(settings, "editorial_learning_enabled", False))
    repository = getattr(request.app.state, "editorial_learning_repository", None)
    if not enabled or repository is None:
        return EditorialMemoryIndicatorResponse(
            enabled=enabled, opt_out_available=enabled
        )
    try:
        preferences = repository.resolve_active_preferences(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            user_id=actor.user_id,
        )
    except Exception:
        preferences = None
    if preferences is None:
        return EditorialMemoryIndicatorResponse(enabled=True, opt_out_available=True)
    return EditorialMemoryIndicatorResponse(
        enabled=True,
        personalized=True,
        example_count=preferences.example_count,
        memory_version=preferences.memory_version,
        policy_revision=preferences.policy_revision,
        opt_out_available=True,
    )


@router.put("/editorial-learning/policy", response_model=EditorialLearningPolicyResponse)
async def set_policy(
    payload: EditorialLearningPolicyUpdateRequest,
    request: Request,
    actor: ActorDep,
) -> EditorialLearningPolicyResponse:
    _enabled(request)
    required_policy_version = str(
        getattr(
            request.app.state.settings,
            "editorial_learning_policy_version",
            "editorial-consent/v1",
        )
    ).strip()
    if payload.policy_version != required_policy_version:
        raise ApiError(
            422,
            "editorial_learning_policy_version_required",
            "Reload editorial learning settings and acknowledge the current policy version.",
        )
    policy = _repository(request).set_policy_if_revision(
        workspace_id=actor.workspace_id,
        user_id=actor.user_id,
        memory_enabled=payload.memory_enabled,
        training_enabled=payload.training_enabled,
        policy_version=required_policy_version,
        retention_days=payload.retention_days,
        expected_revision=payload.expected_revision,
    )
    return policy.response()


@router.post("/editorial-learning/revoke", response_model=EditorialLearningPolicyResponse)
async def revoke_policy(
    payload: EditorialLearningRevocationRequest,
    request: Request,
    actor: ActorDep,
) -> EditorialLearningPolicyResponse:
    policy = _repository(request).revoke(
        workspace_id=actor.workspace_id,
        user_id=actor.user_id,
        memory=payload.memory,
        training=payload.training,
        reason=payload.reason,
    )
    return policy.response()


@router.get(
    "/admin/editorial-learning/projects/{project_id}/datasets",
    response_model=EditorialLearningDatasetListResponse,
)
async def list_datasets(
    project_id: UUID,
    request: Request,
    actor: AdminActorDep,
) -> EditorialLearningDatasetListResponse:
    _enabled(request)
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    datasets = _repository(request).list_datasets(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        limit=25,
    )
    return EditorialLearningDatasetListResponse(
        datasets=[dataset.response() for dataset in datasets]
    )


@router.get(
    "/admin/editorial-learning/projects/{project_id}/deployment",
    response_model=EditorialLearningDeploymentResponse,
)
async def get_deployment(
    project_id: UUID,
    request: Request,
    actor: AdminActorDep,
) -> EditorialLearningDeploymentResponse:
    _enabled(request)
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    return (
        _repository(request)
        .get_deployment_or_default(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            user_id=actor.user_id,
        )
        .response()
    )


@router.post(
    "/admin/editorial-learning/datasets/{dataset_id}/promote",
    response_model=EditorialLearningDeploymentResponse,
)
async def promote_dataset(
    dataset_id: UUID,
    payload: EditorialLearningPromotionRequest,
    project_id: UUID,
    request: Request,
    actor: AdminActorDep,
) -> EditorialLearningDeploymentResponse:
    _enabled(request)
    return (
        _repository(request)
        .promote(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            user_id=actor.user_id,
            dataset_id=dataset_id,
            expected_revision=payload.expected_revision,
        )
        .response()
    )


@router.post(
    "/admin/editorial-learning/deployments/{project_id}/rollback",
    response_model=EditorialLearningDeploymentResponse,
)
async def rollback_deployment(
    project_id: UUID,
    payload: EditorialLearningRollbackRequest,
    request: Request,
    actor: AdminActorDep,
) -> EditorialLearningDeploymentResponse:
    return (
        _repository(request)
        .rollback(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            user_id=actor.user_id,
            reason=payload.reason,
        )
        .response()
    )
