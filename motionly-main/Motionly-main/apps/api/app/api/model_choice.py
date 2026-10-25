"""User model choice API (plan Section 2.11, Task 30).

Endpoints the model picker UI (Task 32, next wave) consumes:

* ``GET /model-options`` — any member: per capability, each selectable option
  with its operator display data, status, quota weight, whether this workspace
  may pick it (with a reason when not), and the effective choice for the
  workspace or a given project.
* ``GET`` / ``PUT /workspaces/current/model-settings`` — the workspace default,
  the members' allow-list and the lock. ``PUT`` needs owner or admin.
* ``GET`` / ``PUT`` / ``DELETE /projects/{project_id}/model-settings`` — the
  per-project override. Any workspace member who can edit the project may change
  it; a locked workspace refuses the override with ``model_choice_locked``.

Responses carry only operator display data (``display_name``, ``description``,
``labels``) plus the opaque route id used as the option id, the quota weight and
a status. No base URL, secret or model id is ever returned.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.dependencies import Actor, get_actor, get_project_repository, require_role
from app.api.errors import ApiError
from app.services.inference_capabilities import InferenceCapability
from app.services.model_choice import (
    REASON_WORKSPACE_LOCKED,
    CapabilityOptionsView,
    ModelChoiceService,
    OptionView,
)

router = APIRouter(tags=["model choice"])

ActorDep = Annotated[Actor, Depends(get_actor)]
ProjectRepositoryDep = Annotated[Any, Depends(get_project_repository)]

# The 14 fine-grained capability ids are the only valid capability values.
_VALID_CAPABILITIES = frozenset(item.value for item in InferenceCapability)


def get_model_choice_service(request: Request) -> ModelChoiceService:
    service = getattr(request.app.state, "model_choice_service", None)
    if service is None:  # pragma: no cover - always wired in create_app
        raise ApiError(503, "model_choice_unavailable", "Model choice is unavailable.")
    return service


ModelChoiceServiceDep = Annotated[ModelChoiceService, Depends(get_model_choice_service)]


# ---------------------------------------------------------------------------
# Response/request models (operator display data only; never a URL or model id).
# ---------------------------------------------------------------------------
class ModelOption(BaseModel):
    """One selectable option for a capability, with this workspace's eligibility."""

    option_id: str = Field(description="The option id (an opaque registry route id).")
    display_name: str | None = Field(
        default=None, description="Operator display name; may name a model or vendor."
    )
    description: str | None = Field(
        default=None, description="Operator description of the option."
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Closed-set labels: fast, balanced, quality, economy, local.",
    )
    quota_weight: float = Field(
        description="Multiplies the feature-quota units of calls this option serves."
    )
    status: str = Field(description="Live availability: available, degraded or unavailable.")
    allowed: bool = Field(description="Whether this workspace may pick the option right now.")
    reason: str | None = Field(
        default=None,
        description="Why the option may not be picked (plan_not_allowed, workspace_restricted); "
        "null when allowed.",
    )
    required_plan: str | None = Field(
        default=None,
        description="Cheapest plan that could pick a plan-locked option (for 'Requires <plan>').",
    )


class CapabilityOptions(BaseModel):
    """The options and effective choice for one capability."""

    capability: str = Field(description="The Section 2.1 capability id.")
    options: list[ModelOption]
    operator_default_option_id: str | None = Field(
        default=None, description="The operator default option id for the capability."
    )
    effective_option_id: str | None = Field(
        default=None,
        description="The option that would serve a call now (for the workspace or project).",
    )
    effective_source: str = Field(
        description="Where the effective choice came from: project, workspace or operator."
    )
    requested_option_id: str | None = Field(
        default=None, description="The user's highest-priority requested option, when set."
    )
    fallback_reason: str | None = Field(
        default=None,
        description="Why the effective option differs from the request (e.g. plan_not_allowed).",
    )


class ModelOptionsResponse(BaseModel):
    """The GET /model-options contract (plan Section 2.11)."""

    capabilities: list[CapabilityOptions]


class WorkspaceModelSettingItem(BaseModel):
    """One workspace capability setting."""

    capability: str
    default_option_id: str | None = None
    allowed_option_ids: list[str] | None = Field(
        default=None,
        description="Options members may pick; null means every option the plan allows.",
    )
    locked: bool = False


class WorkspaceModelSettingsResponse(BaseModel):
    settings: list[WorkspaceModelSettingItem]


class WorkspaceModelSettingUpdate(BaseModel):
    """PUT body for one workspace capability setting."""

    capability: str = Field(description="The Section 2.1 capability id.")
    default_option_id: str | None = Field(
        default=None, description="The workspace default option id; null clears it."
    )
    allowed_option_ids: list[str] | None = Field(
        default=None,
        description="The options members may pick; null means every option the plan allows.",
    )
    locked: bool = Field(default=False, description="When true, projects cannot override.")


class ProjectModelSettingItem(BaseModel):
    capability: str
    option_id: str


class ProjectModelSettingsResponse(BaseModel):
    settings: list[ProjectModelSettingItem]


class ProjectModelSettingUpdate(BaseModel):
    """PUT body for one project capability override."""

    capability: str = Field(description="The Section 2.1 capability id.")
    option_id: str = Field(
        description="The option id (a registry route id) to pin for the project."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate_capability(capability: str) -> str:
    if capability not in _VALID_CAPABILITIES:
        raise ApiError(422, "model_choice_capability_unknown", "Unknown capability id.")
    return capability


def _option_to_model(option: OptionView) -> ModelOption:
    return ModelOption(
        option_id=option.route_id,
        display_name=option.display_name,
        description=option.description,
        labels=list(option.labels),
        quota_weight=option.quota_weight,
        status=option.status,
        allowed=option.allowed,
        reason=option.reason,
        required_plan=option.required_plan,
    )


def _capability_options_to_model(view: CapabilityOptionsView) -> CapabilityOptions:
    return CapabilityOptions(
        capability=view.capability,
        options=[_option_to_model(option) for option in view.options],
        operator_default_option_id=view.operator_default_route_id,
        effective_option_id=view.effective_route_id,
        effective_source=view.effective_source,
        requested_option_id=view.effective_requested_option_id,
        fallback_reason=view.effective_rejected_reason,
    )


def _repository(request: Request):
    return request.app.state.model_choice_repository


# ---------------------------------------------------------------------------
# GET /model-options
# ---------------------------------------------------------------------------
@router.get("/model-options", response_model=ModelOptionsResponse)
async def model_options(
    request: Request,
    actor: ActorDep,
    service: ModelChoiceServiceDep,
    project_id: Annotated[UUID | None, Query()] = None,
) -> ModelOptionsResponse:
    """Per-capability selectable options and the effective choice (any member)."""
    if project_id is not None:
        # Confirm the project is in the caller's workspace (404 otherwise).
        get_project_repository(request).get_project(
            project_id=project_id, workspace_id=actor.workspace_id
        )
    capabilities: list[CapabilityOptions] = []
    for capability in sorted(_VALID_CAPABILITIES):
        view = service.options_for_capability(
            capability=capability,
            workspace_id=actor.workspace_id,
            project_id=project_id,
        )
        if view is None:
            continue
        capabilities.append(_capability_options_to_model(view))
    return ModelOptionsResponse(capabilities=capabilities)


# ---------------------------------------------------------------------------
# GET/PUT /workspaces/current/model-settings
# ---------------------------------------------------------------------------
@router.get("/workspaces/current/model-settings", response_model=WorkspaceModelSettingsResponse)
async def get_workspace_model_settings(
    request: Request,
    actor: ActorDep,
) -> WorkspaceModelSettingsResponse:
    """The workspace's model choice settings (any member may view)."""
    repository = _repository(request)
    settings = repository.get_workspace_settings(actor.workspace_id)
    return WorkspaceModelSettingsResponse(
        settings=[
            WorkspaceModelSettingItem(
                capability=item.capability,
                default_option_id=item.default_option_id,
                allowed_option_ids=(
                    list(item.allowed_option_ids)
                    if item.allowed_option_ids is not None
                    else None
                ),
                locked=item.locked,
            )
            for item in settings
        ]
    )


@router.put(
    "/workspaces/current/model-settings",
    response_model=WorkspaceModelSettingItem,
    dependencies=[Depends(require_role("owner", "admin"))],
)
async def put_workspace_model_settings(
    payload: WorkspaceModelSettingUpdate,
    request: Request,
    actor: ActorDep,
    service: ModelChoiceServiceDep,
) -> WorkspaceModelSettingItem:
    """Set the workspace default, allow-list and lock (owner or admin only)."""
    capability = _validate_capability(payload.capability)
    # Validate the default option, if one is set: it must be a current selectable
    # option allowed by the plan. A dangling default is refused here rather than
    # silently ignored, so an owner gets immediate feedback.
    if payload.default_option_id is not None:
        reason = service.validate_selectable_option(
            capability=capability,
            workspace_id=actor.workspace_id,
            option_id=payload.default_option_id,
        )
        if reason is not None:
            raise ApiError(422, "model_choice_option_invalid", f"Option not selectable: {reason}.")
    repository = _repository(request)
    saved = repository.upsert_workspace_setting(
        workspace_id=actor.workspace_id,
        capability=capability,
        default_option_id=payload.default_option_id,
        allowed_option_ids=payload.allowed_option_ids,
        locked=payload.locked,
        updated_by=actor.user_id,
    )
    # A settings write invalidates the resolver cache for the workspace.
    service.invalidate_workspace(actor.workspace_id)
    return WorkspaceModelSettingItem(
        capability=saved.capability,
        default_option_id=saved.default_option_id,
        allowed_option_ids=(
            list(saved.allowed_option_ids) if saved.allowed_option_ids is not None else None
        ),
        locked=saved.locked,
    )


# ---------------------------------------------------------------------------
# GET/PUT/DELETE /projects/{project_id}/model-settings
# ---------------------------------------------------------------------------
@router.get(
    "/projects/{project_id}/model-settings", response_model=ProjectModelSettingsResponse
)
async def get_project_model_settings(
    project_id: UUID,
    request: Request,
    actor: ActorDep,
    projects: ProjectRepositoryDep,
) -> ProjectModelSettingsResponse:
    """The project's model choice overrides (any member who can edit the project)."""
    projects.get_project(project_id=project_id, workspace_id=actor.workspace_id)
    repository = _repository(request)
    settings = repository.get_project_settings(actor.workspace_id, project_id)
    return ProjectModelSettingsResponse(
        settings=[
            ProjectModelSettingItem(capability=item.capability, option_id=item.option_id)
            for item in settings
        ]
    )


@router.put(
    "/projects/{project_id}/model-settings", response_model=ProjectModelSettingItem
)
async def put_project_model_settings(
    project_id: UUID,
    payload: ProjectModelSettingUpdate,
    request: Request,
    actor: ActorDep,
    projects: ProjectRepositoryDep,
    service: ModelChoiceServiceDep,
) -> ProjectModelSettingItem:
    """Pin a project override (any member who can edit the project).

    Refused with 409 ``model_choice_locked`` when the workspace is locked, and
    with 422 when the option is not a current selectable option allowed by the
    plan and the workspace allow-list.
    """
    projects.get_project(project_id=project_id, workspace_id=actor.workspace_id)
    capability = _validate_capability(payload.capability)
    repository = _repository(request)
    workspace_setting = repository.get_workspace_setting(actor.workspace_id, capability)
    if workspace_setting is not None and workspace_setting.locked:
        raise ApiError(
            409,
            "model_choice_locked",
            "The workspace has locked this capability; projects cannot override it.",
        )
    reason = service.validate_selectable_option(
        capability=capability,
        workspace_id=actor.workspace_id,
        option_id=payload.option_id,
    )
    if reason is not None:
        locked = reason == REASON_WORKSPACE_LOCKED
        code = "model_choice_locked" if locked else "model_choice_option_invalid"
        status = 409 if locked else 422
        raise ApiError(status, code, f"Option not selectable: {reason}.")
    saved = repository.upsert_project_setting(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        capability=capability,
        option_id=payload.option_id,
        updated_by=actor.user_id,
    )
    service.invalidate_project(actor.workspace_id, project_id)
    return ProjectModelSettingItem(capability=saved.capability, option_id=saved.option_id)


@router.delete(
    "/projects/{project_id}/model-settings", response_model=ProjectModelSettingsResponse
)
async def delete_project_model_settings(
    project_id: UUID,
    request: Request,
    actor: ActorDep,
    projects: ProjectRepositoryDep,
    service: ModelChoiceServiceDep,
    capability: Annotated[str, Query(description="The capability override to clear.")],
) -> ProjectModelSettingsResponse:
    """Clear a project override (any member who can edit the project)."""
    projects.get_project(project_id=project_id, workspace_id=actor.workspace_id)
    capability_id = _validate_capability(capability)
    repository = _repository(request)
    repository.delete_project_setting(
        workspace_id=actor.workspace_id, project_id=project_id, capability=capability_id
    )
    service.invalidate_project(actor.workspace_id, project_id)
    settings = repository.get_project_settings(actor.workspace_id, project_id)
    return ProjectModelSettingsResponse(
        settings=[
            ProjectModelSettingItem(capability=item.capability, option_id=item.option_id)
            for item in settings
        ]
    )
