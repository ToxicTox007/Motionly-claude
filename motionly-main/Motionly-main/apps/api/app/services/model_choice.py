"""User model choice resolution (plan Section 2.11, Tasks 30 and 31).

The operator supplies the models; users choose among the selectable registry
routes per capability. This module turns a workspace's and project's stored
choices (``app/repositories/model_choice.py``) into a single resolved route for a
capability, following the Section 2.11 resolution order and validation, with a
short-TTL cache.

**Resolution order** (per capability):

1. the **project** setting's ``option_id`` — unless the workspace is locked;
2. the **workspace** setting's ``default_option_id``;
3. the **operator default** route for the capability.

**Validation.** A candidate option is used only when it is a valid, enabled,
*selectable* route (an offering option), allowed by the workspace's plan (lapsed
paid plans count as free, exactly as quotas do) and by the workspace's
``allowed_option_ids``. Otherwise resolution moves to the next level and records
why the candidate was rejected:

* ``option_unavailable`` — the option is not a current selectable option;
* ``plan_not_allowed`` — the workspace's plan may not pick it;
* ``workspace_restricted`` — it is not in the workspace ``allowed_option_ids``;
* ``workspace_locked`` — the workspace is locked, so the project override is
  ignored.

The operator default (resolution step 3) is never rejected: it is the operator's
own fallback and always serves.

**Resolving against the live runtime.** The runtime can reload
(``RuntimeHolder``), so every resolution reads the *current* runtime through
:func:`app.services.inference_runtime.current_inference_runtime`. The cache key
includes the runtime's ``registry_version`` so a reload invalidates it, and a
write to either settings table invalidates the affected keys explicitly.

Nothing here surfaces a base URL, a secret or a model id: a resolved result is a
route id plus the ``choice_source`` and an optional rejection reason; the display
data a caller shows comes from the offering, which is operator display data only.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.schemas.billing import BillingPlanKey
from app.services.billing import effective_plan_key
from app.services.inference_capabilities import InferenceCapability, parse_capability
from app.services.inference_offerings import (
    CapabilityOfferings,
    OfferingOption,
    offerings_for,
)
from app.services.inference_runtime import InferenceRuntime, current_inference_runtime

# Validation reasons recorded when a candidate option is rejected (Section 2.11).
REASON_OPTION_UNAVAILABLE = "option_unavailable"
REASON_PLAN_NOT_ALLOWED = "plan_not_allowed"
REASON_WORKSPACE_RESTRICTED = "workspace_restricted"
REASON_WORKSPACE_LOCKED = "workspace_locked"

# Where a resolved choice came from (mirrors ModelCallContext.choice_source).
SOURCE_PROJECT = "project"
SOURCE_WORKSPACE = "workspace"
SOURCE_OPERATOR = "operator"


@dataclass(frozen=True)
class ResolvedChoice:
    """The resolved route for a capability and how it was chosen.

    ``route_id`` is the served route id (``None`` only when the capability has no
    route at all). ``choice_source`` is ``project``/``workspace``/``operator``.
    ``requested_option_id`` is the highest-priority option the user actually
    asked for (project, else workspace), or ``None`` when neither set one.
    ``rejected_reason`` records why a requested option was not honoured, so the
    gateway and UI can explain a silent downgrade.
    """

    capability: str
    route_id: str | None
    choice_source: str
    requested_option_id: str | None = None
    rejected_reason: str | None = None


@dataclass(frozen=True)
class OptionView:
    """One selectable option enriched with this workspace's eligibility.

    ``allowed`` is whether this workspace may pick it right now; ``reason`` is a
    machine-readable disallow reason when it may not (e.g. ``plan_not_allowed``
    or ``workspace_restricted``). ``required_plan`` names the cheapest plan that
    could pick a plan-locked option, so the UI can show "Requires <plan>".
    """

    route_id: str
    display_name: str | None
    description: str | None
    labels: tuple[str, ...]
    quota_weight: float
    status: str
    allowed: bool
    reason: str | None
    required_plan: str | None


@dataclass(frozen=True)
class CapabilityOptionsView:
    """The options for one capability plus the effective choice for the caller."""

    capability: str
    options: tuple[OptionView, ...]
    operator_default_route_id: str | None
    effective_route_id: str | None
    effective_source: str
    effective_requested_option_id: str | None
    effective_rejected_reason: str | None


# Billing plan rank, lowest to highest, for "Requires <plan>".
_PLAN_RANK: dict[str, int] = {
    BillingPlanKey.FREE.value: 0,
    BillingPlanKey.CREATOR.value: 1,
    BillingPlanKey.PRO.value: 2,
    BillingPlanKey.AGENCY.value: 3,
}


def _plan_allows(option: OfferingOption, plan: BillingPlanKey) -> bool:
    """Whether ``plan`` may pick ``option`` (empty ``plans`` means every plan)."""
    return not option.plans or plan.value in option.plans


def _required_plan(option: OfferingOption) -> str | None:
    """The cheapest plan that could pick ``option`` (``None`` when any may)."""
    if not option.plans:
        return None
    return min(option.plans, key=lambda plan: _PLAN_RANK.get(plan, 99))


def _in_allow_list(route_id: str, allowed_option_ids: tuple[str, ...] | None) -> bool:
    """``None`` allow-list means "every option"; otherwise membership is required."""
    return allowed_option_ids is None or route_id in allowed_option_ids


class ModelChoiceService:
    """Resolve the Section 2.11 choice for a capability, with a short-TTL cache.

    The service is constructed with a repository (the settings store), a
    callable that returns the *current* runtime, and a callable that returns the
    workspace's effective billing plan. Resolution reads the runtime fresh every
    time (through ``runtime_provider``) so a registry reload is honoured; the
    cache is keyed partly by the runtime's ``registry_version`` and is
    invalidated on a settings write.
    """

    def __init__(
        self,
        *,
        repository,
        runtime_provider: Callable[[], InferenceRuntime],
        plan_provider: Callable[[UUID], BillingPlanKey],
        cache_ttl_s: float = 5.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._repository = repository
        self._runtime_provider = runtime_provider
        self._plan_provider = plan_provider
        self._cache_ttl_s = max(0.0, float(cache_ttl_s))
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        # key -> (expires_at, ResolvedChoice)
        self._cache: dict[tuple[str, str, str, str], tuple[float, ResolvedChoice]] = {}

    # -- public API ---------------------------------------------------------
    def resolve(
        self,
        *,
        capability: str,
        workspace_id: UUID | None,
        project_id: UUID | None,
    ) -> ResolvedChoice:
        """Resolve the served route for ``capability`` (Section 2.11 order)."""
        capability_id = _normalize_capability(capability)
        if workspace_id is None:
            # No tenant: the operator default is the only thing we can resolve.
            runtime = self._runtime_provider()
            offerings = offerings_for(runtime).get(capability_id)
            return ResolvedChoice(
                capability=capability_id,
                route_id=offerings.operator_default_route_id if offerings else None,
                choice_source=SOURCE_OPERATOR,
            )

        runtime = self._runtime_provider()
        cache_key = (
            runtime.registry_version,
            str(workspace_id),
            str(project_id) if project_id is not None else "",
            capability_id,
        )
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        resolved = self._resolve_uncached(
            runtime=runtime,
            capability_id=capability_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        self._cache_put(cache_key, resolved)
        return resolved

    def expected_route_for(
        self,
        capability: str,
        workspace_id: UUID | None,
        project_id: UUID | None,
    ) -> str | None:
        """The route id a call for ``capability`` would prefer (pre-call cache keys).

        Consumers that cache or key provenance before the gateway call (visual
        enrichers, embeddings, diarization, stems, generated B-roll) use this to
        fold the chosen route into their cache key, so two workspaces that chose
        different routes never share a cached result. It returns exactly the
        route the gateway's resolver would prefer for the same tenant, resolved
        against the current runtime; ``None`` when no route serves the capability.
        """
        return self.resolve(
            capability=capability, workspace_id=workspace_id, project_id=project_id
        ).route_id

    def options_for_capability(
        self,
        *,
        capability: str,
        workspace_id: UUID,
        project_id: UUID | None,
    ) -> CapabilityOptionsView | None:
        """Build the GET /model-options view for one capability and this workspace."""
        capability_id = _normalize_capability(capability)
        runtime = self._runtime_provider()
        offerings = offerings_for(runtime).get(capability_id)
        if offerings is None:
            return None
        plan = self._plan_provider(workspace_id)
        workspace_setting = self._repository.get_workspace_setting(workspace_id, capability_id)
        allowed_ids = (
            workspace_setting.allowed_option_ids if workspace_setting is not None else None
        )
        option_views: list[OptionView] = []
        for option in offerings.options:
            allowed = True
            reason: str | None = None
            if not _plan_allows(option, plan):
                allowed, reason = False, REASON_PLAN_NOT_ALLOWED
            elif not _in_allow_list(option.route_id, allowed_ids):
                allowed, reason = False, REASON_WORKSPACE_RESTRICTED
            option_views.append(
                OptionView(
                    route_id=option.route_id,
                    display_name=option.display_name,
                    description=option.description,
                    labels=option.labels,
                    quota_weight=option.quota_weight,
                    status=option.status,
                    allowed=allowed,
                    reason=reason,
                    required_plan=_required_plan(option),
                )
            )
        effective = self.resolve(
            capability=capability_id, workspace_id=workspace_id, project_id=project_id
        )
        return CapabilityOptionsView(
            capability=capability_id,
            options=tuple(option_views),
            operator_default_route_id=offerings.operator_default_route_id,
            effective_route_id=effective.route_id,
            effective_source=effective.choice_source,
            effective_requested_option_id=effective.requested_option_id,
            effective_rejected_reason=effective.rejected_reason,
        )

    def validate_selectable_option(
        self,
        *,
        capability: str,
        workspace_id: UUID,
        option_id: str,
    ) -> str | None:
        """Why ``option_id`` cannot be chosen for ``capability``, or ``None`` if it can.

        Used by the settings endpoints before a write: the option must be a
        current selectable option (``option_unavailable``), allowed by the plan
        (``plan_not_allowed``) and by the workspace allow-list
        (``workspace_restricted``).
        """
        capability_id = _normalize_capability(capability)
        runtime = self._runtime_provider()
        offerings = offerings_for(runtime).get(capability_id)
        option = _find_option(offerings, option_id) if offerings else None
        if option is None:
            return REASON_OPTION_UNAVAILABLE
        plan = self._plan_provider(workspace_id)
        if not _plan_allows(option, plan):
            return REASON_PLAN_NOT_ALLOWED
        workspace_setting = self._repository.get_workspace_setting(workspace_id, capability_id)
        allowed_ids = (
            workspace_setting.allowed_option_ids if workspace_setting is not None else None
        )
        if not _in_allow_list(option_id, allowed_ids):
            return REASON_WORKSPACE_RESTRICTED
        return None

    def invalidate_workspace(self, workspace_id: UUID) -> None:
        """Drop every cached resolution for a workspace (after a settings write)."""
        with self._lock:
            self._cache = {
                key: value
                for key, value in self._cache.items()
                if key[1] != str(workspace_id)
            }

    def invalidate_project(self, workspace_id: UUID, project_id: UUID) -> None:
        """Drop cached resolutions for a project (after a project settings write).

        A project write can change what the project resolves to, so both the
        project-scoped entries and the workspace-scoped entries for this
        workspace are dropped (the simplest correct invalidation).
        """
        self.invalidate_workspace(workspace_id)

    # -- internals ----------------------------------------------------------
    def _resolve_uncached(
        self,
        *,
        runtime: InferenceRuntime,
        capability_id: str,
        workspace_id: UUID,
        project_id: UUID | None,
    ) -> ResolvedChoice:
        offerings = offerings_for(runtime).get(capability_id)
        operator_default = offerings.operator_default_route_id if offerings else None

        plan = self._plan_provider(workspace_id)
        workspace_setting = self._repository.get_workspace_setting(workspace_id, capability_id)
        allowed_ids = (
            workspace_setting.allowed_option_ids if workspace_setting is not None else None
        )
        locked = bool(workspace_setting.locked) if workspace_setting is not None else False

        # The highest-priority option the user asked for (project, else workspace).
        requested_option_id: str | None = None
        first_rejected_reason: str | None = None

        # 1. Project override (unless the workspace is locked).
        project_setting = (
            self._repository.get_project_setting(workspace_id, project_id, capability_id)
            if project_id is not None
            else None
        )
        if project_setting is not None:
            requested_option_id = project_setting.option_id
            if locked:
                first_rejected_reason = REASON_WORKSPACE_LOCKED
            else:
                reason = self._reject_reason(
                    offerings, project_setting.option_id, plan, allowed_ids
                )
                if reason is None:
                    return ResolvedChoice(
                        capability=capability_id,
                        route_id=project_setting.option_id,
                        choice_source=SOURCE_PROJECT,
                        requested_option_id=project_setting.option_id,
                    )
                first_rejected_reason = reason

        # 2. Workspace default.
        if workspace_setting is not None and workspace_setting.default_option_id:
            if requested_option_id is None:
                requested_option_id = workspace_setting.default_option_id
            reason = self._reject_reason(
                offerings, workspace_setting.default_option_id, plan, allowed_ids
            )
            if reason is None:
                return ResolvedChoice(
                    capability=capability_id,
                    route_id=workspace_setting.default_option_id,
                    choice_source=SOURCE_WORKSPACE,
                    requested_option_id=requested_option_id,
                    rejected_reason=first_rejected_reason,
                )
            if first_rejected_reason is None:
                first_rejected_reason = reason

        # 3. Operator default (never rejected).
        return ResolvedChoice(
            capability=capability_id,
            route_id=operator_default,
            choice_source=SOURCE_OPERATOR,
            requested_option_id=requested_option_id,
            rejected_reason=first_rejected_reason if requested_option_id is not None else None,
        )

    @staticmethod
    def _reject_reason(
        offerings: CapabilityOfferings | None,
        option_id: str,
        plan: BillingPlanKey,
        allowed_ids: tuple[str, ...] | None,
    ) -> str | None:
        option = _find_option(offerings, option_id) if offerings else None
        if option is None:
            return REASON_OPTION_UNAVAILABLE
        if not _plan_allows(option, plan):
            return REASON_PLAN_NOT_ALLOWED
        if not _in_allow_list(option_id, allowed_ids):
            return REASON_WORKSPACE_RESTRICTED
        return None

    def _cache_get(self, key: tuple[str, str, str, str]) -> ResolvedChoice | None:
        if self._cache_ttl_s <= 0:
            return None
        now = self._clock()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if now >= expires_at:
                self._cache.pop(key, None)
                return None
            return value

    def _cache_put(self, key: tuple[str, str, str, str], value: ResolvedChoice) -> None:
        if self._cache_ttl_s <= 0:
            return
        with self._lock:
            self._cache[key] = (self._clock() + self._cache_ttl_s, value)


def _find_option(
    offerings: CapabilityOfferings | None, option_id: str
) -> OfferingOption | None:
    if offerings is None:
        return None
    return next((opt for opt in offerings.options if opt.route_id == option_id), None)


def _normalize_capability(capability: str) -> str:
    """Accept a new id or a legacy operation string and return the canonical id."""
    try:
        return str(parse_capability(capability))
    except ValueError:
        # Keep the raw string so an unknown capability simply resolves to no
        # route rather than raising inside the gateway's hot path.
        return str(capability)


class ModelChoiceGatewayResolver:
    """Fills ``ModelCallContext.preferred_route_id`` / ``choice_source`` (Task 31).

    Set on the gateway like the quota guard (``gateway.set_choice_resolver``).
    At invoke time the gateway calls :meth:`preference_for` with the coarse
    capability family and the call context; the resolver maps the family onto the
    fine-grained capability id for the request's operation and resolves the
    Section 2.11 choice for the context's workspace/project. Returning ``None``
    leaves the gateway on its normal priority order (operator default first).
    """

    def __init__(self, service: ModelChoiceService) -> None:
        self._service = service

    def preference_for(self, *, operation, context) -> tuple[str | None, str] | None:
        """Return ``(preferred_route_id, choice_source)`` or ``None``.

        ``operation`` is the request's :class:`ModelOperation`; it is mapped to
        the fine-grained capability id. ``context`` carries the workspace and
        project. A workspace-less call (no tenant) returns ``None`` so background
        work without a tenant keeps the operator order.
        """
        workspace_id = _coerce_uuid(getattr(context, "workspace_id", None))
        if workspace_id is None or operation is None:
            return None
        capability_id = _capability_id_for_operation(operation)
        if capability_id is None:
            return None
        project_id = _coerce_uuid(getattr(context, "project_id", None))
        resolved = self._service.resolve(
            capability=capability_id, workspace_id=workspace_id, project_id=project_id
        )
        if resolved.route_id is None:
            return None
        return resolved.route_id, resolved.choice_source

    def resolve(self, *, operation, context) -> ResolvedChoice | None:
        """Full resolution for the gateway's ``model_choice`` metadata."""
        workspace_id = _coerce_uuid(getattr(context, "workspace_id", None))
        if workspace_id is None or operation is None:
            return None
        capability_id = _capability_id_for_operation(operation)
        if capability_id is None:
            return None
        project_id = _coerce_uuid(getattr(context, "project_id", None))
        return self._service.resolve(
            capability=capability_id, workspace_id=workspace_id, project_id=project_id
        )


def _capability_id_for_operation(operation) -> str | None:
    """Map a ``ModelOperation`` to its fine-grained Section 2.1 capability id."""
    value = getattr(operation, "value", operation)
    try:
        return str(parse_capability(str(value)))
    except ValueError:
        return None


def _coerce_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def build_model_choice_service_for_app(
    app: object,
    *,
    repository,
    cache_ttl_s: float = 5.0,
) -> ModelChoiceService:
    """Build a :class:`ModelChoiceService` bound to an app's live runtime/billing.

    Resolution reads the current runtime through ``current_inference_runtime`` so
    a registry reload is always honoured, and the workspace plan through the
    app's billing repository (lapsed-aware via ``effective_plan_key``). Used by
    the API process and the worker entrypoints alike (plan Task 31).
    """

    def runtime_provider() -> InferenceRuntime:
        return current_inference_runtime(app)

    def plan_provider(workspace_id: UUID) -> BillingPlanKey:
        state = getattr(app, "state", app)
        billing_repository = getattr(state, "billing_repository", None)
        if billing_repository is None:
            return BillingPlanKey.FREE
        subscription = billing_repository.get_subscription(workspace_id=workspace_id)
        return effective_plan_key(subscription)

    return ModelChoiceService(
        repository=repository,
        runtime_provider=runtime_provider,
        plan_provider=plan_provider,
        cache_ttl_s=cache_ttl_s,
    )


def wire_worker_choice_resolver(
    gateway,
    *,
    runtime: InferenceRuntime,
    settings: object,
    cache_ttl_s: float = 5.0,
) -> ModelChoiceService | None:
    """Attach the model-choice resolver to a worker's gateway (plan Task 31).

    The visual and matte workers build their own gateway (not through
    ``create_app``), so the resolver is wired here from the worker's settings:
    the model-choice settings are read with the service role, the plan is read
    (lapsed-aware) from the billing repository, and the preferred route is
    resolved against the worker's ``runtime``. Background jobs then follow the
    same user choice as API calls. Without Supabase (dev/test) there is no store,
    so the resolver is not attached and routing stays operator-only.

    Returns the service when wired, else ``None``. The imports are local so the
    worker image only loads the repositories it needs.
    """
    supabase_url = getattr(settings, "supabase_url", None)
    service_role_key = getattr(settings, "supabase_service_role_key", None)
    if gateway is None or not supabase_url or not service_role_key:
        return None
    from app.repositories.billing import SupabaseBillingRepository
    from app.repositories.model_choice import SupabaseModelChoiceRepository

    repository = SupabaseModelChoiceRepository(
        supabase_url=str(supabase_url),
        service_role_key=str(service_role_key),
    )
    billing_repository = SupabaseBillingRepository(
        supabase_url=str(supabase_url),
        service_role_key=str(service_role_key),
    )

    def runtime_provider() -> InferenceRuntime:
        return runtime

    def plan_provider(workspace_id: UUID) -> BillingPlanKey:
        subscription = billing_repository.get_subscription(workspace_id=workspace_id)
        return effective_plan_key(subscription)

    service = ModelChoiceService(
        repository=repository,
        runtime_provider=runtime_provider,
        plan_provider=plan_provider,
        cache_ttl_s=cache_ttl_s,
    )
    gateway.set_choice_resolver(ModelChoiceGatewayResolver(service))
    return service


__all__ = [
    "CapabilityOptionsView",
    "InferenceCapability",
    "ModelChoiceGatewayResolver",
    "ModelChoiceService",
    "OptionView",
    "REASON_OPTION_UNAVAILABLE",
    "REASON_PLAN_NOT_ALLOWED",
    "REASON_WORKSPACE_LOCKED",
    "REASON_WORKSPACE_RESTRICTED",
    "ResolvedChoice",
    "SOURCE_OPERATOR",
    "SOURCE_PROJECT",
    "SOURCE_WORKSPACE",
    "build_model_choice_service_for_app",
    "expected_route_for",
    "wire_worker_choice_resolver",
]


def expected_route_for(
    service: ModelChoiceService,
    capability: str,
    workspace_id: UUID | None,
    project_id: UUID | None,
) -> str | None:
    """Module-level convenience wrapper around :meth:`ModelChoiceService.expected_route_for`.

    Documented in the plan (Task 31) as the pre-call cache-key helper for the
    next-wave consumers (visual enrichers, embeddings, diarization, stems,
    generated B-roll). They already hold a :class:`ModelChoiceService`, so this
    simply forwards to it; the method form is preferred in new code.
    """
    return service.expected_route_for(capability, workspace_id, project_id)
