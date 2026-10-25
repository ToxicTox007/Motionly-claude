"""Registry-derived per-capability availability report (plan Task 8).

This module turns three inputs into one provider-neutral capability report:

* the built :class:`~app.services.inference_runtime.InferenceRuntime` (its active
  route records and the registry document);
* the Section 2.6 startup decision carried on
  ``runtime.startup_validation`` (dropped routes, warnings, required/optional
  state);
* the gateway's **read-only circuit state** per active route.

For each of the 14 Section 2.1 capability ids it computes:

* ``status`` — ``available`` | ``degraded`` | ``unavailable``;
* ``reason`` — a machine-readable code (``not_configured``,
  ``all_routes_invalid``, ``gpu_disabled``, ``circuit_open``,
  ``hard_cap_exhausted``, ...), ``None`` when fully available;
* ``is_async`` — whether the capability is asynchronous (route-affine submit);
* ``compute_requirement`` — the aggregated placement (``cpu`` | ``gpu`` |
  ``remote`` | ``any``);
* ``route_count`` — the number of active (valid, enabled, built) routes.

The two safety rules the plan's acceptance criteria name:

1. An **optional capability with no configured route** is ``unavailable`` with
   reason ``not_configured`` (or ``all_routes_invalid`` / ``gpu_disabled`` when
   a route existed but was dropped).
2. A route with an **open circuit** makes its capability ``degraded`` — not
   ``unavailable`` — when it is the only route, and never makes the API
   unhealthy. With other healthy routes the capability stays ``available``.

Nothing here reads a base URL, a model id, a secret or a provider label. It
exposes only capability-level metadata, exactly what ``/capabilities`` returns.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.inference_capabilities import InferenceCapability
from app.services.inference_registry import DiagnosticCode, RouteRecord
from app.services.inference_runtime import InferenceRuntime

# Every Section 2.1 capability id, in a stable sorted order for a deterministic
# report. Kept independent of the operations-repository copy so this read model
# is self-contained.
ALL_CAPABILITY_IDS: tuple[str, ...] = tuple(sorted(c.value for c in InferenceCapability))

# Capabilities that are inherently asynchronous (route-affine submit) even when
# no route is configured, so ``/capabilities`` reports a stable async flag for an
# unavailable capability (plan Section 2.1/2.5).
_INHERENTLY_ASYNC: frozenset[str] = frozenset(
    {
        InferenceCapability.VIDEO_GENERATION_SUBMIT.value,
        InferenceCapability.VIDEO_GENERATION_STATUS.value,
    }
)

STATUS_AVAILABLE = "available"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"

# Machine-readable reasons (plan Section 2.6). ``None`` means fully available.
REASON_NOT_CONFIGURED = "not_configured"
REASON_ALL_ROUTES_INVALID = "all_routes_invalid"
REASON_GPU_DISABLED = "gpu_disabled"
REASON_CIRCUIT_OPEN = "circuit_open"


@dataclass(frozen=True)
class CapabilityStatus:
    """One capability's availability (plan Task 8 ``/capabilities`` row)."""

    capability: str
    status: str
    reason: str | None
    is_async: bool
    compute_requirement: str
    route_count: int
    required: bool


@dataclass(frozen=True)
class CapabilityReport:
    """The full per-capability report plus the registry identity.

    ``registry_version`` lets a consumer (Task 9B) bust its cache when the
    catalog changes.
    """

    registry_version: str
    capabilities: tuple[CapabilityStatus, ...]

    def by_id(self, capability_id: str) -> CapabilityStatus | None:
        return next(
            (c for c in self.capabilities if c.capability == capability_id), None
        )


def _aggregate_compute(routes: list[RouteRecord]) -> str:
    """Aggregate per-route compute requirements into one value.

    When every active route agrees on a requirement, return it; a mix (or no
    active route) aggregates to ``any`` (plan Section 2.6 "compute requirement
    (cpu|gpu|remote|any, aggregated)").
    """
    requirements = {route.compute_requirement for route in routes}
    if len(requirements) == 1:
        return next(iter(requirements))
    return "any"


def _is_async_capability(capability_id: str, routes: list[RouteRecord]) -> bool:
    """Whether a capability is asynchronous.

    Any active route whose capability is a ``video.generation`` submit/status, or
    an ``async_job`` strategy, is asynchronous. With no active route, fall back to
    the inherent async set so the flag is stable for an unavailable capability.
    """
    if any(route.strategy == "async_job" for route in routes):
        return True
    return capability_id in _INHERENTLY_ASYNC


def _dropped_codes_for_capability(
    runtime: InferenceRuntime, capability_id: str
) -> set[str]:
    """Collect the diagnostic codes relevant to a capability being unavailable.

    Includes both dropped-route codes and the ``ROUTE_REQUIRES_GPU`` warning:
    a GPU route is only warned (not error-dropped) by the validator, but when
    GPU workers are off it cannot run, so the warning is the signal that the
    capability is unavailable because of GPU placement.
    """
    codes: set[str] = set()
    startup = runtime.startup_validation
    if startup is None:
        return codes
    for drop in getattr(startup, "dropped_routes", ()):  # StartupWarning items
        if drop.capability == capability_id:
            codes.add(drop.code)
    for warning in getattr(startup, "warnings", ()):  # StartupWarning items
        if (
            warning.capability == capability_id
            and warning.code == DiagnosticCode.ROUTE_REQUIRES_GPU
        ):
            codes.add(warning.code)
    return codes


def _reason_for_unavailable(
    *, had_configured_routes: bool, dropped_codes: set[str]
) -> str:
    """Pick the machine-readable reason for an unavailable capability.

    ``gpu_disabled`` wins when a route was dropped only because GPU workers are
    off; ``all_routes_invalid`` when a configured route existed but every one was
    dropped; ``not_configured`` when no route was ever configured.
    """
    if not had_configured_routes and not dropped_codes:
        return REASON_NOT_CONFIGURED
    if DiagnosticCode.ROUTE_REQUIRES_GPU in dropped_codes:
        # A GPU route that cannot run because GPU workers are off is the GPU
        # placement reason, even if it was also dropped at runtime for a missing
        # local runner (the GPU requirement is the root cause).
        return REASON_GPU_DISABLED
    if dropped_codes:
        return REASON_ALL_ROUTES_INVALID
    return REASON_NOT_CONFIGURED


def build_capability_report(runtime: InferenceRuntime) -> CapabilityReport:
    """Compute the per-capability availability report for one runtime.

    Reads only the runtime's active route records, the startup validation's
    dropped/required data and the gateway's read-only circuit state. Produces one
    :class:`CapabilityStatus` for every Section 2.1 capability id.
    """
    startup = runtime.startup_validation
    required_ids = _required_ids(startup)

    # Active routes, grouped by capability id.
    active_by_capability: dict[str, list[RouteRecord]] = {}
    for route in runtime.route_records.values():
        active_by_capability.setdefault(route.capability.value, []).append(route)

    # Capabilities that appear in the registry document at all (enabled or not),
    # so a dropped-only capability still reports a route-based reason.
    configured_ids = _configured_ids(runtime)

    statuses: list[CapabilityStatus] = []
    for capability_id in ALL_CAPABILITY_IDS:
        active_routes = active_by_capability.get(capability_id, [])
        route_count = len(active_routes)
        required = capability_id in required_ids
        is_async = _is_async_capability(capability_id, active_routes)
        compute = _aggregate_compute(active_routes)

        if route_count == 0:
            dropped_codes = _dropped_codes_for_capability(runtime, capability_id)
            reason = _reason_for_unavailable(
                had_configured_routes=capability_id in configured_ids,
                dropped_codes=dropped_codes,
            )
            statuses.append(
                CapabilityStatus(
                    capability=capability_id,
                    status=STATUS_UNAVAILABLE,
                    reason=reason,
                    is_async=is_async,
                    compute_requirement=compute,
                    route_count=0,
                    required=required,
                )
            )
            continue

        # One or more active routes: inspect circuit state. A route with an open
        # circuit degrades the capability only when every active route is open;
        # with any healthy route the capability stays available (plan Section
        # 2.6 acceptance criterion).
        open_count = sum(
            1
            for route in active_routes
            if runtime.gateway.circuit_state(_model_route(runtime, route.id)) == "open"
        )
        if open_count and open_count == route_count:
            status = STATUS_DEGRADED
            reason = REASON_CIRCUIT_OPEN
        else:
            status = STATUS_AVAILABLE
            reason = None
        statuses.append(
            CapabilityStatus(
                capability=capability_id,
                status=status,
                reason=reason,
                is_async=is_async,
                compute_requirement=compute,
                route_count=route_count,
                required=required,
            )
        )

    return CapabilityReport(
        registry_version=runtime.registry_version,
        capabilities=tuple(statuses),
    )


def _model_route(runtime: InferenceRuntime, route_id: str):
    """Find the gateway :class:`ModelRoute` whose id matches, for circuit state.

    The gateway's circuit key is ``route_id:model_revision``; the ModelRoute is
    the object that carries it. Disabled routes are included because a disabled
    route can still be active for a route-affine status call.
    """
    for route in runtime.gateway.all_routes:
        if route.route_id == route_id:
            return route
    # Should not happen for an active route; fall back to a synthetic lookup that
    # reports a closed circuit (an unknown route cannot be open).
    return _ClosedCircuitRoute(route_id)


@dataclass(frozen=True)
class _ClosedCircuitRoute:
    """A stand-in whose ``circuit_key`` never matches an open circuit."""

    route_id: str
    model_revision: str = "unknown"

    @property
    def circuit_key(self) -> str:
        return f"{self.route_id}:{self.model_revision}"


def _required_ids(startup: object | None) -> frozenset[str]:
    if startup is None:
        return frozenset()
    required: set[str] = set()
    for capability in getattr(startup, "capabilities", ()):  # CapabilityAvailability
        if getattr(capability, "required", False):
            required.add(capability.capability)
    return frozenset(required)


def _configured_ids(runtime: InferenceRuntime) -> frozenset[str]:
    startup = runtime.startup_validation
    configured: set[str] = set()
    if startup is not None:
        for capability in getattr(startup, "capabilities", ()):
            # ``route_count`` or a drop implies a configured route existed.
            if getattr(capability, "route_count", 0) > 0:
                configured.add(capability.capability)
    document = runtime.validation.document
    if document is not None:
        for route in document.routes:
            configured.add(route.capability.value)
    return frozenset(configured)


__all__ = [
    "ALL_CAPABILITY_IDS",
    "CapabilityReport",
    "CapabilityStatus",
    "STATUS_AVAILABLE",
    "STATUS_DEGRADED",
    "STATUS_UNAVAILABLE",
    "build_capability_report",
]
