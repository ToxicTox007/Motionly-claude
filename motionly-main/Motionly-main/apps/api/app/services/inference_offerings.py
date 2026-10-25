"""Per-capability user-model-choice offerings (plan Section 2.11, Task 29).

This read model turns a built :class:`~app.services.inference_runtime.InferenceRuntime`
into, for every capability id, the **selectable** options a user may choose and
the **operator default** route for that capability.

An option's status is the runtime's live view of its route: the capability-level
availability from :func:`build_capability_report`, narrowed by the individual
route's circuit state (an open circuit downgrades the option to ``degraded``).
Nothing here reads a base URL, a secret or a provider label; the only
vendor-naming fields it surfaces are the operator's own ``display_name`` and
``description`` offering display data (which the Task 2 scanner treats like
``data_processor``).

The operator default per capability (Section 2.11):

* the single valid, enabled, selectable route whose offering sets
  ``default: true``, if one exists (the validator guarantees at most one);
* otherwise the highest-priority (lowest ``priority`` number, then registry
  order) valid, enabled route — selectable or not, because a non-selectable
  route still serves as the default.

Task 30 (model choice settings/API) and Task 31 (gateway) build on this.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.inference_capability_report import (
    STATUS_AVAILABLE,
    STATUS_DEGRADED,
    build_capability_report,
)
from app.services.inference_registry import RouteRecord
from app.services.inference_runtime import InferenceRuntime

# Status an option reports (mirrors the capability report vocabulary).
OPTION_AVAILABLE = STATUS_AVAILABLE
OPTION_DEGRADED = STATUS_DEGRADED
OPTION_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class OfferingOption:
    """One selectable route a user may pick for a capability (plan Section 2.11).

    The option id is the route id. ``display_name``/``description`` are the
    operator's display data; ``labels``/``plans``/``quota_weight`` come straight
    off the route's offering block. ``status`` is the live availability.
    """

    route_id: str
    display_name: str | None
    description: str | None
    labels: tuple[str, ...]
    plans: tuple[str, ...]
    quota_weight: float
    status: str


@dataclass(frozen=True)
class CapabilityOfferings:
    """The selectable options and operator default for one capability."""

    capability: str
    options: tuple[OfferingOption, ...]
    operator_default_route_id: str | None


def _route_status(runtime: InferenceRuntime, route_id: str, capability_status: str) -> str:
    """Narrow a capability's status by the individual route's circuit state."""
    for model_route in runtime.gateway.all_routes:
        if model_route.route_id == route_id:
            if runtime.gateway.circuit_state(model_route) == "open":
                return OPTION_DEGRADED
            break
    return capability_status


def _operator_default_route_id(routes: list[RouteRecord]) -> str | None:
    """The operator default route id for one capability (plan Section 2.11).

    ``routes`` is the capability's valid, enabled routes in registry order.
    """
    explicit = [
        route
        for route in routes
        if route.offering is not None
        and route.offering.default
        and route.offering.selectable
    ]
    if explicit:
        # The validator guarantees at most one selectable default per capability.
        return explicit[0].id
    if not routes:
        return None
    # Highest priority = lowest priority number; registry order breaks ties.
    best = min(enumerate(routes), key=lambda item: (item[1].priority, item[0]))
    return best[1].id


def offerings_for(runtime: InferenceRuntime) -> dict[str, CapabilityOfferings]:
    """Per-capability selectable options and operator default for a runtime.

    Only valid, enabled routes (the runtime's active ``route_records``) are
    considered. A capability with no active route yields empty options and a
    ``None`` default. A capability with active routes but no selectable one
    yields empty options but still names the operator default.
    """
    report = build_capability_report(runtime)
    capability_status = {row.capability: row.status for row in report.capabilities}

    active_by_capability: dict[str, list[RouteRecord]] = {}
    for route in runtime.route_records.values():
        active_by_capability.setdefault(route.capability.value, []).append(route)

    result: dict[str, CapabilityOfferings] = {}
    for capability_id, routes in active_by_capability.items():
        base_status = capability_status.get(capability_id, OPTION_AVAILABLE)
        options: list[OfferingOption] = []
        for route in routes:
            offering = route.offering
            if offering is None or not offering.selectable:
                continue
            options.append(
                OfferingOption(
                    route_id=route.id,
                    display_name=offering.display_name,
                    description=offering.description,
                    labels=offering.labels,
                    plans=offering.plans,
                    quota_weight=offering.quota_weight,
                    status=_route_status(runtime, route.id, base_status),
                )
            )
        result[capability_id] = CapabilityOfferings(
            capability=capability_id,
            options=tuple(options),
            operator_default_route_id=_operator_default_route_id(routes),
        )
    return result


__all__ = [
    "CapabilityOfferings",
    "OfferingOption",
    "OPTION_AVAILABLE",
    "OPTION_DEGRADED",
    "OPTION_UNAVAILABLE",
    "offerings_for",
]
