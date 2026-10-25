"""Request/response schemas for the admin inference registry API (plan Task 21/22).

These models shape the Tier 3 admin surface that the next-wave admin AI UI
(Task 23) consumes. They never carry a secret value: a secret body is write-only
and a secret read returns metadata only (name, scope, fingerprint, key version,
timestamps). Adapter/model/route records are passed through as free-form JSON
objects validated by the registry loader/validator (Section 2.6), so the schema
here stays thin and the single source of truth for the record shape remains
``app.services.inference_registry``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Validation diagnostics (shared by every mutating response)
# ---------------------------------------------------------------------------


class ValidationDiagnostic(BaseModel):
    """One Section 2.6 validation finding surfaced to the operator."""

    object_kind: str
    object_id: str
    severity: Literal["error", "warning"]
    code: str
    message: str
    path: str


# ---------------------------------------------------------------------------
# Registry records (free-form objects validated by the registry loader)
# ---------------------------------------------------------------------------


class RegistryEntry(BaseModel):
    """A single adapter/model/route record with its management metadata.

    ``managed`` is ``"file"`` for an entry that came from the declarative file
    registry (read-only in the UI) or ``"database"`` for an admin-editable one.
    """

    id: str
    managed: Literal["file", "database"]
    record: dict[str, Any] = Field(default_factory=dict)


class RegistrySnapshotResponse(BaseModel):
    """The merged file+database registry view for the admin UI."""

    database_enabled: bool
    registry_version: int
    adapters: list[RegistryEntry] = Field(default_factory=list)
    models: list[RegistryEntry] = Field(default_factory=list)
    routes: list[RegistryEntry] = Field(default_factory=list)
    warnings: list[ValidationDiagnostic] = Field(default_factory=list)
    errors: list[ValidationDiagnostic] = Field(default_factory=list)


class RegistryRecordRequest(BaseModel):
    """A create/update body: the raw adapter/model/route record object."""

    record: dict[str, Any]


class MutationResponse(BaseModel):
    """The result of a write: the stored record plus validation findings.

    ``committed`` is ``false`` for a dry-run or a rejected change; the registry
    version is unchanged in that case.
    """

    committed: bool
    registry_version: int
    entry: RegistryEntry | None = None
    warnings: list[ValidationDiagnostic] = Field(default_factory=list)
    errors: list[ValidationDiagnostic] = Field(default_factory=list)


class DeletionResponse(BaseModel):
    committed: bool
    registry_version: int
    warnings: list[ValidationDiagnostic] = Field(default_factory=list)
    errors: list[ValidationDiagnostic] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Secrets (write-only; reads return metadata only)
# ---------------------------------------------------------------------------


class SecretMetadataResponse(BaseModel):
    """What a secret read may expose (plan Section 2.4): never a value."""

    name: str
    scope: str
    fingerprint: str
    key_version: str
    created_by: str | None = None
    created_at: str | None = None
    rotated_at: str | None = None
    last_used_at: str | None = None


class SecretListResponse(BaseModel):
    secrets: list[SecretMetadataResponse] = Field(default_factory=list)


class SecretWriteRequest(BaseModel):
    """Create or rotate a secret. The value is write-only and never returned."""

    name: str = Field(min_length=1, max_length=200)
    scope: str = Field(default="global", min_length=1, max_length=64)
    value: str = Field(min_length=1)


class SecretRotateRequest(BaseModel):
    scope: str = Field(default="global", min_length=1, max_length=64)
    value: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Route test (plan Task 22, Section 2.7)
# ---------------------------------------------------------------------------


class RouteTestRequest(BaseModel):
    """Options for a synthetic route test. No user data is sent."""

    # Per-test dollar cost ceiling. A paid async submit additionally requires
    # ``allow_paid=true`` before the gateway is contacted.
    max_cost_usd: float = Field(default=0.50, ge=0.0, le=100.0)
    allow_paid: bool = False


class RouteTestResponse(BaseModel):
    """The outcome of one synthetic route test (plan Task 22)."""

    route_id: str
    capability: str
    # "ok" | "schema_invalid" | "error". A malformed provider response is
    # reported as schema_invalid, never as a crash.
    status: Literal["ok", "schema_invalid", "error"]
    schema_valid: bool
    latency_ms: int | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    estimated_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    cost_unknown: bool = False
    error_code: str | None = None
    circuit_state: str = "unknown"
    # True when the route is an async submit that needed allow_paid=true.
    paid_confirmation_required: bool = False
    # The route-affine operation id for an async submit test, when one was made.
    operation_id: str | None = None


# ---------------------------------------------------------------------------
# Per-route health (plan Task 22, Section 2.7)
# ---------------------------------------------------------------------------


class RouteHealthEntry(BaseModel):
    route_id: str
    capability: str | None = None
    model: str | None = None
    model_revision: str | None = None
    enabled: bool = True
    managed: Literal["file", "database"] = "file"
    circuit_state: str = "unknown"
    last_success_at: str | None = None
    last_failure_at: str | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    error_rate: float | None = None
    total_calls: int = 0
    fallback_count: int = 0
    last_error_code: str | None = None
    # Budget / hard-cap usage from the ledgers (counts, never a secret).
    hard_cap_max_requests: int | None = None
    hard_cap_max_units: int | None = None
    pricing_status: str = "priced"


class RouteHealthResponse(BaseModel):
    database_enabled: bool
    routes: list[RouteHealthEntry] = Field(default_factory=list)
