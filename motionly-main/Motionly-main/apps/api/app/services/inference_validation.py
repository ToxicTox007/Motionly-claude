"""Turn registry diagnostics into the one startup decision (plan Task 7).

:func:`validate_inference_startup` consumes an :class:`InferenceRuntime` (built
in Task 6) and applies the Section 2.6 outcome:

* An **error** on a required capability (``planning.chat`` or
  ``transcription.speech_to_text``) that leaves it without any valid enabled
  route fails startup. A dedicated :class:`InferenceStartupError` is raised with
  every relevant diagnostic ``(route_id, severity, code)``; no secret, base URL
  or request body is included.
* An **invalid optional route** is already dropped by the validator/runtime; it
  is reported here once, as a structured warning with the route id and code, and
  its capability is reported ``unavailable``.
* **Warnings** (for example ``revision_provider_managed``,
  ``data_processor_missing`` in dev, non-commercial licences) are logged once
  each with the route id and code.
* In **production**, one warning is logged per route whose pricing status is
  ``unknown`` (Section 2.6 "Production logs a warning for each such route at
  startup"), because that spend is invisible to dollar budgets and is governed
  only by the hard cap.

The return value, :class:`StartupValidation`, is the structure Task 8 exposes
through ``/capabilities`` and the authenticated ``/health/dependencies`` report:
the warnings list, the dropped routes, and per-capability availability.

This module logs; it does not print. It never raises for an invalid *optional*
route. It raises only when a required capability cannot be served.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.inference_registry import (
    REQUIRED_CAPABILITIES,
    Diagnostic,
    DiagnosticCode,
    InferenceCapability,
)
from app.services.inference_runtime import DroppedRoute, InferenceRuntime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartupWarning:
    """One structured startup warning (plan Task 7/8): route id and code only."""

    route_id: str
    code: str
    message: str
    capability: str | None = None


@dataclass(frozen=True)
class CapabilityAvailability:
    """Per-capability availability for ``/capabilities`` (plan Task 8)."""

    capability: str
    available: bool
    route_count: int
    required: bool
    reason: str | None = None


@dataclass(frozen=True)
class StartupValidation:
    """The product of the startup decision (plan Task 7).

    ``warnings`` and ``dropped_routes`` feed the admin ``/health/dependencies``
    report; ``capabilities`` feeds ``/capabilities``. Everything here is safe to
    expose to an authenticated admin: no secret, base URL or request body.
    """

    warnings: tuple[StartupWarning, ...] = ()
    dropped_routes: tuple[StartupWarning, ...] = ()
    capabilities: tuple[CapabilityAvailability, ...] = ()
    unknown_pricing_route_ids: tuple[str, ...] = ()

    def capability(self, capability_id: str) -> CapabilityAvailability | None:
        return next(
            (c for c in self.capabilities if c.capability == capability_id), None
        )

    def available_capability_ids(self) -> frozenset[str]:
        return frozenset(c.capability for c in self.capabilities if c.available)

    def warning_fields(self) -> list[dict[str, str | None]]:
        return [
            {"route_id": w.route_id, "code": w.code, "capability": w.capability}
            for w in self.warnings
        ]

    def dropped_fields(self) -> list[dict[str, str | None]]:
        return [
            {"route_id": d.route_id, "code": d.code, "capability": d.capability}
            for d in self.dropped_routes
        ]


class InferenceStartupError(RuntimeError):
    """A required capability has no valid enabled route (plan invariant 3).

    Carries the relevant diagnostics as ``(route_id, severity, code)`` triples so
    an operator can see exactly which required routes were rejected and why. The
    diagnostics never contain a secret value.
    """

    code = "inference_startup_invalid"

    def __init__(
        self,
        *,
        missing_capabilities: tuple[str, ...],
        diagnostics: tuple[Diagnostic, ...],
    ) -> None:
        self.missing_capabilities = missing_capabilities
        self.diagnostics = diagnostics
        detail = ", ".join(
            f"{d.object_id}:{d.code}" for d in diagnostics
        )
        super().__init__(
            "Production inference startup failed; required capabilities "
            f"{', '.join(missing_capabilities)} have no valid enabled route. "
            f"Diagnostics: [{detail}]"
        )


# A warning diagnostic code logged once per occurrence with its route id. These
# are the Section 2.6 warning codes a route can carry.
_WARNING_CODES: frozenset[str] = frozenset(
    {
        DiagnosticCode.REVISION_PROVIDER_MANAGED,
        DiagnosticCode.LICENCE_MISSING,
        DiagnosticCode.LICENCE_NONCOMMERCIAL,
        DiagnosticCode.DATA_PROCESSOR_MISSING,
        DiagnosticCode.ROUTE_REQUIRES_GPU,
        DiagnosticCode.SECRET_REF_ENV_WARNING,
    }
)


def validate_inference_startup(
    runtime: InferenceRuntime,
    *,
    settings: object,
    raise_on_missing: bool = True,
) -> StartupValidation:
    """Apply the Section 2.6 startup decision to a built runtime (plan Task 7).

    Raises :class:`InferenceStartupError` when a required capability has no valid
    enabled route and ``raise_on_missing`` is true. Logs one structured warning
    per registry warning, per dropped route and (in production) per
    unknown-pricing route. Returns the structure Task 8 exposes. ``build_
    inference_runtime`` passes ``raise_on_missing=False`` for a dev/test runtime
    so the required-capability gap is reported, not fatal.
    """
    app_env = str(getattr(settings, "app_env", "production")).strip().lower()
    is_production = app_env == "production"
    validation = runtime.validation

    required_ids = frozenset(c.value for c in REQUIRED_CAPABILITIES)

    # Capabilities that still have at least one valid enabled route after the
    # validator dropped invalid ones. ``runtime.route_records`` holds only the
    # routes the runtime actually activated (valid, enabled, adapter built), so
    # it is the authoritative "available" set. Dropped-at-runtime routes (missing
    # secret, local runner unavailable) are not in it.
    available_by_capability: dict[str, int] = {}
    for route in runtime.route_records.values():
        key = route.capability.value
        available_by_capability[key] = available_by_capability.get(key, 0) + 1

    # ----- warnings (registry warnings, once each) -------------------------
    warnings: list[StartupWarning] = []
    for diag in validation.warnings():
        if diag.code not in _WARNING_CODES:
            continue
        warning = StartupWarning(
            route_id=diag.object_id,
            code=diag.code,
            message=diag.message,
            capability=_capability_for_route(runtime, diag.object_id),
        )
        warnings.append(warning)
        logger.warning(
            "inference_startup_warning",
            extra={"route_id": warning.route_id, "code": warning.code},
        )

    # ----- dropped routes (validator errors on optional routes + runtime drops)
    dropped: list[StartupWarning] = []
    required_error_diags: list[Diagnostic] = []
    missing_required: set[str] = set()

    for diag in validation.errors():
        if diag.code == DiagnosticCode.REQUIRED_CAPABILITY_MISSING:
            missing_required.add(diag.object_id)
            required_error_diags.append(diag)
            continue
        if diag.object_kind != "route":
            # Document/adapter/capability-level errors are only fatal when they
            # cause a required capability to be missing, which is surfaced by the
            # REQUIRED_CAPABILITY_MISSING diagnostic above.
            continue
        capability = _capability_for_route(runtime, diag.object_id)
        # A dropped route whose capability is required (and now missing) is part
        # of the fatal set; collect its diagnostic for the error.
        if capability in required_ids and capability in missing_required:
            required_error_diags.append(diag)
        dropped.append(
            StartupWarning(
                route_id=diag.object_id,
                code=diag.code,
                message=diag.message,
                capability=capability,
            )
        )

    # Routes the runtime could not activate after validation (secret failure,
    # local runner unavailable). These are always dropped, never fatal for a
    # required capability unless the validator already flagged it missing.
    for runtime_drop in runtime.dropped_routes:
        dropped.append(_dropped_from_runtime(runtime, runtime_drop))

    for drop in dropped:
        logger.warning(
            "inference_route_dropped",
            extra={"route_id": drop.route_id, "code": drop.code},
        )

    # ----- required-capability gate ---------------------------------------
    # Re-derive missing required capabilities from the authoritative activated
    # set, so a route that validated but failed to build (dropped at runtime)
    # also fails a required capability.
    runtime_missing = tuple(
        sorted(
            cap
            for cap in required_ids
            if available_by_capability.get(cap, 0) == 0
        )
    )
    if runtime_missing and raise_on_missing:
        # Collect every relevant diagnostic: the validator's errors for routes on
        # a missing required capability, plus any REQUIRED_CAPABILITY_MISSING.
        relevant = tuple(
            diag
            for diag in validation.diagnostics
            if diag.is_error
            and (
                diag.object_id in runtime_missing
                or _capability_for_route(runtime, diag.object_id) in runtime_missing
            )
        )
        if not relevant:
            relevant = tuple(required_error_diags)
        raise InferenceStartupError(
            missing_capabilities=runtime_missing, diagnostics=relevant
        )

    # ----- unknown-pricing routes (production warning) ---------------------
    unknown_pricing_ids: list[str] = []
    for route_id, record in runtime.route_records.items():
        if record.pricing.status == "unknown":
            unknown_pricing_ids.append(route_id)
            if is_production:
                logger.warning(
                    "inference_unknown_pricing_route",
                    extra={
                        "route_id": route_id,
                        "code": "pricing_unknown_hard_capped",
                        "capability": record.capability.value,
                    },
                )

    # ----- per-capability availability for /capabilities ------------------
    capabilities = _build_capability_availability(
        runtime=runtime,
        available_by_capability=available_by_capability,
        required_ids=required_ids,
        dropped=dropped,
    )

    return StartupValidation(
        warnings=tuple(warnings),
        dropped_routes=tuple(dropped),
        capabilities=capabilities,
        unknown_pricing_route_ids=tuple(unknown_pricing_ids),
    )


def _capability_for_route(runtime: InferenceRuntime, route_id: str) -> str | None:
    record = runtime.route_records.get(route_id)
    if record is not None:
        return record.capability.value
    # The route was dropped (not activated); find it in the registry document so
    # its capability can still be reported.
    document = runtime.validation.document
    if document is not None:
        for route in document.routes:
            if route.id == route_id:
                return route.capability.value
    return None


def _dropped_from_runtime(
    runtime: InferenceRuntime, drop: DroppedRoute
) -> StartupWarning:
    return StartupWarning(
        route_id=drop.route_id,
        code=drop.code,
        message=drop.message,
        capability=_capability_for_route(runtime, drop.route_id),
    )


def _build_capability_availability(
    *,
    runtime: InferenceRuntime,
    available_by_capability: dict[str, int],
    required_ids: frozenset[str],
    dropped: list[StartupWarning],
) -> tuple[CapabilityAvailability, ...]:
    """Build the per-capability availability list (plan Task 8 structure).

    Every capability that appears in the registry document (enabled or not)
    gets an entry; required capabilities always appear even when unconfigured.
    """
    document = runtime.validation.document
    configured: set[str] = set(available_by_capability)
    if document is not None:
        for route in document.routes:
            configured.add(route.capability.value)
    configured |= required_ids

    dropped_by_capability: dict[str, StartupWarning] = {}
    for drop in dropped:
        if drop.capability and drop.capability not in dropped_by_capability:
            dropped_by_capability[drop.capability] = drop

    out: list[CapabilityAvailability] = []
    for capability_id in sorted(configured):
        count = available_by_capability.get(capability_id, 0)
        available = count > 0
        reason: str | None = None
        if not available:
            drop = dropped_by_capability.get(capability_id)
            reason = drop.code if drop is not None else "not_configured"
        out.append(
            CapabilityAvailability(
                capability=capability_id,
                available=available,
                route_count=count,
                required=capability_id in required_ids,
                reason=reason,
            )
        )
    return out


__all__ = [
    "CapabilityAvailability",
    "InferenceCapability",
    "InferenceStartupError",
    "StartupValidation",
    "StartupWarning",
    "validate_inference_startup",
]
