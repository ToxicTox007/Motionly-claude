"""The public ``GET /capabilities`` endpoint (plan Task 8, Section 2.6).

``/capabilities`` is public: it exposes only capability-level availability, never
a base URL, model id, secret or provider label. For every Section 2.1 capability
it returns the id, availability status, a machine-readable reason, the async
flag, the aggregated compute requirement and the active route count. A
``registry_version`` is included so a consumer (Task 9B) can bust its cache when
the catalog changes.

Detailed route, warning and circuit information is **not** here; it is admin-only
through the authenticated ``/health/dependencies`` report (Section 2.6/2.7).

This router is also where Task 9A adds ``GET /public/data-processors``: the
public data-processor privacy metadata of the enabled remote routes, named for
the public capability surface, not just one route.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.services.inference_capability_report import (
    CapabilityReport,
    build_capability_report,
)
from app.services.inference_registry import is_self_hosted
from app.services.inference_runtime import InferenceRuntime

router = APIRouter(tags=["capabilities"])

CapabilityAvailabilityStatus = Literal["available", "degraded", "unavailable"]
ComputeRequirement = Literal["cpu", "gpu", "remote", "any"]


class CapabilitySummary(BaseModel):
    """One capability's public availability (plan Section 2.6).

    Capability-level only: no base URL, model id, secret or provider label.
    """

    id: str = Field(description="The stable Section 2.1 capability id.")
    status: CapabilityAvailabilityStatus = Field(
        description="available, degraded (e.g. the only route's circuit is open), or unavailable."
    )
    reason: str | None = Field(
        default=None,
        description="Machine-readable reason when not fully available "
        "(e.g. not_configured, all_routes_invalid, gpu_disabled, circuit_open); "
        "null when available.",
    )
    is_async: bool = Field(
        description="Whether the capability is asynchronous (route-affine submit)."
    )
    compute_requirement: ComputeRequirement = Field(
        description="Aggregated placement of the active routes."
    )
    route_count: int = Field(
        ge=0, description="Number of active (valid, enabled) routes for the capability."
    )
    required: bool = Field(
        description="Whether this is a startup-required capability (planning.chat, "
        "transcription.speech_to_text)."
    )


class CapabilitiesResponse(BaseModel):
    """The public capability contract.

    ``registry_version`` identifies the live catalog so a consumer can invalidate
    a cached response when the registry changes (plan Task 9B).
    """

    registry_version: str = Field(
        description="Opaque identity of the live inference registry catalog."
    )
    capabilities: list[CapabilitySummary]


def _response_from_report(report: CapabilityReport) -> CapabilitiesResponse:
    return CapabilitiesResponse(
        registry_version=report.registry_version,
        capabilities=[
            CapabilitySummary(
                id=capability.capability,
                status=capability.status,  # type: ignore[arg-type]
                reason=capability.reason,
                is_async=capability.is_async,
                compute_requirement=capability.compute_requirement,  # type: ignore[arg-type]
                route_count=capability.route_count,
                required=capability.required,
            )
            for capability in report.capabilities
        ],
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(request: Request) -> CapabilitiesResponse:
    """Public capability availability (plan Task 8).

    Reads the registry-driven runtime on ``app.state``; it never probes the
    network and never returns provider-identifying data.
    """
    runtime = request.app.state.inference_runtime
    report = build_capability_report(runtime)
    return _response_from_report(report)


# ---------------------------------------------------------------------------
# GET /public/data-processors (plan Task 9A, Section 2.6 data-processor rule)
# ---------------------------------------------------------------------------
class DataProcessorEntry(BaseModel):
    """One distinct data processor of the enabled remote routes (plan Task 9A).

    Display metadata only: no base URL, model id, route id, provider label or
    secret. These are the only fields the privacy page needs to render a
    processor section.
    """

    display_name: str = Field(
        description="The processor's display name, as the operator configured it."
    )
    purpose: str = Field(description="What the processor does with the data Motionly sends it.")
    data_categories: list[str] = Field(
        description="The categories of content the processor receives (e.g. audio, images, text)."
    )
    policy_url: str = Field(description="The processor's privacy/data-handling policy URL.")


class DataProcessorsResponse(BaseModel):
    """The public data-processor privacy contract (plan Task 9A).

    One entry per distinct data processor (deduped by display name + policy URL)
    of the enabled remote routes in the live registry. Self-hosted routes are
    excluded; vendor routes still registered directly in ``main.py`` (until Task
    19) carry no registry ``data_processor`` metadata and therefore never appear.
    """

    processors: list[DataProcessorEntry]


def _build_data_processors(runtime: InferenceRuntime) -> list[DataProcessorEntry]:
    """Collect the distinct data processors of the enabled remote routes.

    A route is included only when it is enabled and its adapter is remote (not
    self-hosted) and carries ``data_processor`` metadata. Entries are deduped by
    ``(display_name, policy_url)`` and returned in a stable, deterministic order
    so the response and the privacy page are reproducible. Only display metadata
    is exposed — never a base URL, model id, route id, provider label or secret.
    """
    seen: set[tuple[str, str]] = set()
    entries: list[DataProcessorEntry] = []
    for route in runtime.route_records.values():
        if not route.enabled:
            continue
        adapter = runtime.adapter_records.get(route.adapter_id)
        if adapter is None or is_self_hosted(adapter):
            continue
        processor = adapter.data_processor
        if processor is None:
            continue
        key = (processor.display_name, processor.policy_url)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            DataProcessorEntry(
                display_name=processor.display_name,
                purpose=processor.purpose,
                data_categories=list(processor.data_categories),
                policy_url=processor.policy_url,
            )
        )
    entries.sort(key=lambda entry: (entry.display_name, entry.policy_url))
    return entries


@router.get("/public/data-processors", response_model=DataProcessorsResponse)
async def public_data_processors(request: Request) -> DataProcessorsResponse:
    """Public data-processor privacy metadata (plan Task 9A).

    Reads the registry-driven runtime on ``app.state`` exactly as
    ``/capabilities`` does; it never probes the network and never returns a base
    URL, model id, route id, provider label or secret. The privacy page renders
    one section per entry.
    """
    runtime = request.app.state.inference_runtime
    return DataProcessorsResponse(processors=_build_data_processors(runtime))
