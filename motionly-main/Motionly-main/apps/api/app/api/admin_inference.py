"""Admin inference registry API (plan Tasks 21 and 22, Section 2.4/2.7, Tier 3).

Admin-only CRUD, dry-run, secret management, route test and per-route health for
the database registry. Every route is guarded by
:func:`app.api.admin.require_admin` and is disabled (``503``) unless
``AI_REGISTRY_DATABASE_ENABLED`` is set, so the surface stays dark until the
operator has verified the database registry.

Design rules enforced here (plan Task 21):

* **File entries are read-only.** The merged view marks each entry
  ``managed="file"`` or ``managed="database"``; a write to a file id is refused.
* **Every change is validated before commit.** A create/update/delete first
  builds the *candidate* merged file+database document, runs the full
  Section 2.6 ``validate_registry`` over it, and refuses to commit when the
  candidate has any error. Validation warnings are always returned.
* **Secrets are write-only.** Create/rotate/delete accept a value; a read
  returns metadata only (name, scope, fingerprint, key version, timestamps).
* **A route is not deleted while a non-terminal operation references it**
  (plan Section 2.5): the delete is refused with ``409``.
* **Writes bump the registry version and trigger a runtime reload**, so an
  activated route becomes live without an API restart.

No secret value, ciphertext or KEK is ever logged or returned. Request bodies
are redacted by the existing middleware; secret values additionally register
with the log redactor the moment they are sealed.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.api.admin import require_admin
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.core.logging import get_logger
from app.schemas.admin_inference import (
    DeletionResponse,
    MutationResponse,
    RegistryEntry,
    RegistryRecordRequest,
    RegistrySnapshotResponse,
    RouteHealthEntry,
    RouteHealthResponse,
    RouteTestRequest,
    RouteTestResponse,
    SecretListResponse,
    SecretMetadataResponse,
    SecretRotateRequest,
    SecretWriteRequest,
    ValidationDiagnostic,
)
from app.services.inference_registry import (
    load_registry_document,
    load_registry_sources,
    merge_registry_documents,
    validate_registry,
)
from app.services.inference_route_test import RouteTestError, run_route_test
from app.services.inference_templates import validate_mapping
from app.services.offload import run_blocking

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/inference", tags=["admin"])

AdminActorDep = Annotated[Actor, Depends(require_admin)]

# Audit actions recorded through the admin user directory (plan Task 21).
ACTION_PREFIX = "inference_registry"


# ---------------------------------------------------------------------------
# Store construction (wired from app.main; plan Task 20/21)
# ---------------------------------------------------------------------------
def build_admin_inference_registry_store(
    settings: object, metrics_registry: object | None = None
) -> object | None:
    """Build the admin registry store WITH a secret store, or ``None``.

    Returns a store only when ``AI_REGISTRY_DATABASE_ENABLED`` is true. The store
    carries an :class:`~app.services.inference_secret_store.InferenceSecretStore`
    built from ``AI_SECRETS_KEK_FILE`` so the admin API can create and open
    ``db:`` secrets. When Supabase is configured a PostgREST store is used;
    otherwise an in-memory store backs local/test admin flows. The returned
    object is API-only (lives on ``app.state.inference_registry_store``); it is
    never passed to worker code, so ``ai_secrets`` stays out of the worker's
    reach (plan Task 20).
    """
    if not bool(getattr(settings, "ai_registry_database_enabled", False)):
        return None
    from app.services.inference_secret_store import InferenceSecretStore

    secret_store = InferenceSecretStore.from_settings(settings)
    supabase_url = getattr(settings, "supabase_url", None)
    service_role_key = getattr(settings, "supabase_service_role_key", None)
    if supabase_url and service_role_key:
        from app.services.inference_registry_store import (
            SupabaseInferenceRegistryRepository,
        )

        return SupabaseInferenceRegistryRepository(
            supabase_url=str(supabase_url),
            service_role_key=str(service_role_key),
            secret_store=secret_store,
            metrics_registry=metrics_registry,
        )
    from app.services.inference_registry_store import (
        InMemoryInferenceRegistryRepository,
    )

    return InMemoryInferenceRegistryRepository(secret_store=secret_store)


# ---------------------------------------------------------------------------
# Guards and helpers
# ---------------------------------------------------------------------------
def _require_enabled(request: Request):
    """Return the admin registry store or refuse with ``503`` when disabled."""
    settings = request.app.state.settings
    if not bool(getattr(settings, "ai_registry_database_enabled", False)):
        raise ApiError(
            503,
            "inference_registry_disabled",
            "The database inference registry is not enabled.",
        )
    store = getattr(request.app.state, "inference_registry_store", None)
    if store is None:
        raise ApiError(
            503,
            "inference_registry_disabled",
            "The database inference registry is not available.",
        )
    return store


def _settings(request: Request):
    return request.app.state.settings


def _diag_models(diagnostics) -> tuple[list[ValidationDiagnostic], list[ValidationDiagnostic]]:
    warnings: list[ValidationDiagnostic] = []
    errors: list[ValidationDiagnostic] = []
    for diag in diagnostics:
        model = ValidationDiagnostic(
            object_kind=diag.object_kind,
            object_id=diag.object_id,
            severity="error" if diag.is_error else "warning",
            code=diag.code,
            message=diag.message,
            path=diag.path,
        )
        (errors if diag.is_error else warnings).append(model)
    return warnings, errors


def _file_document_ids(settings) -> tuple[dict[str, set[str]], object | None]:
    """Load the file registry and return its ids per kind and the document.

    Mirrors the runtime's loader (``load_registry_sources`` or the dev fake) so
    the merged candidate the admin API validates matches what startup builds.
    """
    registry_file = getattr(settings, "ai_registry_file", None)
    if registry_file:
        document, _diags = load_registry_sources(str(registry_file))
    else:
        from app.services.inference_runtime import _DEV_FAKE_REGISTRY

        document, _diags = load_registry_document(_DEV_FAKE_REGISTRY)
    ids: dict[str, set[str]] = {"adapter": set(), "model": set(), "route": set()}
    if document is not None:
        ids["adapter"] = {a.id for a in document.adapters}
        ids["model"] = {m.id for m in document.models}
        ids["route"] = {r.id for r in document.routes}
    return ids, document


def _db_payload(store) -> dict[str, Any]:
    """The database registry as the raw JSON shape the file loader accepts."""
    return {
        "version": 1,
        "adapters": list(store.list_adapters()),
        "models": list(store.list_models()),
        "routes": list(store.list_routes()),
    }


def _validate_candidate(settings, store, *, kind: str, record: dict[str, Any] | None,
                        delete_id: str | None = None):
    """Build the candidate merged file+DB document and validate it (Section 2.6).

    ``record`` upserts into the DB portion; ``delete_id`` removes from it. Returns
    ``(validation_or_None, diagnostics)``; a structural load error returns
    ``(None, diagnostics)``.
    """
    payload = _db_payload(store)
    key = f"{kind}s"
    entries = [dict(item) for item in payload[key]]
    if delete_id is not None:
        entries = [item for item in entries if str(item.get("id")) != delete_id]
    if record is not None:
        entries = [item for item in entries if str(item.get("id")) != str(record.get("id"))]
        entries.append(dict(record))
    payload[key] = entries

    db_document, db_diags = load_registry_document(payload)
    if db_document is None:
        return None, list(db_diags)

    _file_ids, file_document = _file_document_ids(settings)
    documents = [doc for doc in (file_document, db_document) if doc is not None]
    merged, merge_diags = merge_registry_documents(documents)
    if merged is None:
        return None, list(db_diags) + list(merge_diags)

    validation = validate_registry(
        merged,
        app_env=str(getattr(settings, "app_env", "production")).strip().lower(),
        gpu_workers_enabled=bool(getattr(settings, "gpu_workers_enabled", True)),
        block_noncommercial=bool(getattr(settings, "ai_block_noncommercial", False)),
        mapping_validator=validate_mapping,
        workspace_scoped_routes_enabled=bool(
            getattr(settings, "ai_workspace_scoped_routes", False)
        ),
    )
    return validation, list(db_diags) + list(merge_diags) + list(validation.diagnostics)


async def _audit(request: Request, actor: Actor, action: str, **details: Any) -> None:
    directory = getattr(request.app.state, "admin_user_directory", None)
    if directory is None:
        return
    try:
        await directory.record_audit_event(
            actor_user_id=str(actor.user_id),
            action=f"{ACTION_PREFIX}_{action}",
            details={k: v for k, v in details.items() if v is not None},
        )
    except Exception:  # noqa: BLE001 - an audit write never blocks the operation
        logger.warning("inference_registry_audit_failed", extra={"action": action})


def _reload(request: Request) -> int:
    """Force a runtime reload after a write and return the new registry version."""
    holder = getattr(request.app.state, "inference_runtime_holder", None)
    if holder is not None:
        try:
            holder.maybe_reload(force=True)
        except Exception:  # noqa: BLE001 - a reload failure keeps the last good runtime
            logger.warning("inference_registry_reload_failed")
    store = getattr(request.app.state, "inference_registry_store", None)
    try:
        return int(store.registry_version()) if store is not None else 0
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Registry snapshot
# ---------------------------------------------------------------------------
@router.get("/registry", response_model=RegistrySnapshotResponse)
async def get_registry(request: Request, _actor: AdminActorDep) -> RegistrySnapshotResponse:
    store = _require_enabled(request)
    settings = _settings(request)
    file_ids, _file_document = _file_document_ids(settings)

    def _entries(records, kind: str) -> list[RegistryEntry]:
        return [
            RegistryEntry(id=str(rec.get("id")), managed="database", record=dict(rec))
            for rec in records
        ]

    adapters = _entries(store.list_adapters(), "adapter")
    models = _entries(store.list_models(), "model")
    routes = _entries(store.list_routes(), "route")

    # Validate the live merged document so the UI can show current warnings.
    validation, diagnostics = _validate_candidate(
        settings, store, kind="adapter", record=None
    )
    warnings, errors = _diag_models(diagnostics)
    return RegistrySnapshotResponse(
        database_enabled=True,
        registry_version=int(store.registry_version()),
        adapters=adapters,
        models=models,
        routes=routes,
        warnings=warnings,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# CRUD + dry-run for adapters, models and routes
# ---------------------------------------------------------------------------
def _upsert(store, kind: str, record: dict[str, Any]) -> None:
    if kind == "adapter":
        store.upsert_adapter(record)
    elif kind == "model":
        store.upsert_model(record)
    else:
        store.upsert_route(record)


def _delete(store, kind: str, record_id: str) -> None:
    if kind == "adapter":
        store.delete_adapter(record_id)
    elif kind == "model":
        store.delete_model(record_id)
    else:
        store.delete_route(record_id)


async def _create_or_update(
    request: Request,
    actor: Actor,
    *,
    kind: str,
    body: RegistryRecordRequest,
    dry_run: bool,
    expected_id: str | None = None,
) -> MutationResponse:
    store = _require_enabled(request)
    settings = _settings(request)
    record = dict(body.record)
    record_id = str(record.get("id") or "").strip()
    if not record_id:
        raise ApiError(422, "invalid_record", f"The {kind} record is missing an id.")
    if expected_id is not None and record_id != expected_id:
        raise ApiError(422, "invalid_record", "The record id does not match the path.")

    # A file id is read-only; a database write must never shadow it.
    file_ids, _doc = _file_document_ids(settings)
    if record_id in file_ids[kind]:
        raise ApiError(
            409,
            "managed_by_file",
            f"The {kind} {record_id!r} is managed by the file registry and is read-only.",
        )

    validation, diagnostics = _validate_candidate(
        settings, store, kind=kind, record=record
    )
    warnings, errors = _diag_models(diagnostics)
    if validation is None or errors:
        return MutationResponse(
            committed=False,
            registry_version=int(store.registry_version()),
            entry=RegistryEntry(id=record_id, managed="database", record=record),
            warnings=warnings,
            errors=errors,
        )
    if dry_run:
        return MutationResponse(
            committed=False,
            registry_version=int(store.registry_version()),
            entry=RegistryEntry(id=record_id, managed="database", record=record),
            warnings=warnings,
            errors=[],
        )

    await run_blocking(_upsert, store, kind, record)
    version = _reload(request)
    await _audit(request, actor, f"{kind}_upsert", object_id=record_id)
    return MutationResponse(
        committed=True,
        registry_version=version,
        entry=RegistryEntry(id=record_id, managed="database", record=record),
        warnings=warnings,
        errors=[],
    )


# -- adapters --------------------------------------------------------------
@router.post("/adapters", response_model=MutationResponse)
async def create_adapter(
    request: Request, _actor: AdminActorDep, body: RegistryRecordRequest, dry_run: bool = False
) -> MutationResponse:
    return await _create_or_update(request, _actor, kind="adapter", body=body, dry_run=dry_run)


@router.put("/adapters/{adapter_id}", response_model=MutationResponse)
async def update_adapter(
    request: Request,
    _actor: AdminActorDep,
    adapter_id: str,
    body: RegistryRecordRequest,
    dry_run: bool = False,
) -> MutationResponse:
    return await _create_or_update(
        request, _actor, kind="adapter", body=body, dry_run=dry_run, expected_id=adapter_id
    )


@router.delete("/adapters/{adapter_id}", response_model=DeletionResponse)
async def delete_adapter(
    request: Request, _actor: AdminActorDep, adapter_id: str, dry_run: bool = False
) -> DeletionResponse:
    return await _delete_record(
        request, _actor, kind="adapter", record_id=adapter_id, dry_run=dry_run
    )


# -- models ----------------------------------------------------------------
@router.post("/models", response_model=MutationResponse)
async def create_model(
    request: Request, _actor: AdminActorDep, body: RegistryRecordRequest, dry_run: bool = False
) -> MutationResponse:
    return await _create_or_update(request, _actor, kind="model", body=body, dry_run=dry_run)


@router.put("/models/{model_id}", response_model=MutationResponse)
async def update_model(
    request: Request,
    _actor: AdminActorDep,
    model_id: str,
    body: RegistryRecordRequest,
    dry_run: bool = False,
) -> MutationResponse:
    return await _create_or_update(
        request, _actor, kind="model", body=body, dry_run=dry_run, expected_id=model_id
    )


@router.delete("/models/{model_id}", response_model=DeletionResponse)
async def delete_model(
    request: Request, _actor: AdminActorDep, model_id: str, dry_run: bool = False
) -> DeletionResponse:
    return await _delete_record(request, _actor, kind="model", record_id=model_id, dry_run=dry_run)


# -- routes ----------------------------------------------------------------
@router.post("/routes", response_model=MutationResponse)
async def create_route(
    request: Request, _actor: AdminActorDep, body: RegistryRecordRequest, dry_run: bool = False
) -> MutationResponse:
    return await _create_or_update(request, _actor, kind="route", body=body, dry_run=dry_run)


@router.put("/routes/{route_id}", response_model=MutationResponse)
async def update_route(
    request: Request,
    _actor: AdminActorDep,
    route_id: str,
    body: RegistryRecordRequest,
    dry_run: bool = False,
) -> MutationResponse:
    return await _create_or_update(
        request, _actor, kind="route", body=body, dry_run=dry_run, expected_id=route_id
    )


@router.delete("/routes/{route_id}", response_model=DeletionResponse)
async def delete_route(
    request: Request, _actor: AdminActorDep, route_id: str, dry_run: bool = False
) -> DeletionResponse:
    # A route is not deleted while a non-terminal operation references it
    # (plan Section 2.5): its status calls stay route-affine until terminal.
    operations = getattr(request.app.state, "operations_repository", None)
    if operations is not None and hasattr(operations, "list_non_terminal_for_route"):
        try:
            pending = await run_blocking(operations.list_non_terminal_for_route, route_id)
        except Exception as exc:  # noqa: BLE001 - a read failure must not silently allow delete
            raise ApiError(
                503,
                "operations_unavailable",
                "Could not verify in-flight operations for this route.",
            ) from exc
        if pending:
            raise ApiError(
                409,
                "route_in_use",
                "The route is referenced by a non-terminal inference operation.",
            )
    return await _delete_record(request, _actor, kind="route", record_id=route_id, dry_run=dry_run)


async def _delete_record(
    request: Request, actor: Actor, *, kind: str, record_id: str, dry_run: bool
) -> DeletionResponse:
    store = _require_enabled(request)
    settings = _settings(request)
    file_ids, _doc = _file_document_ids(settings)
    if record_id in file_ids[kind]:
        raise ApiError(
            409,
            "managed_by_file",
            f"The {kind} {record_id!r} is managed by the file registry and is read-only.",
        )
    validation, diagnostics = _validate_candidate(
        settings, store, kind=kind, record=None, delete_id=record_id
    )
    warnings, errors = _diag_models(diagnostics)
    if validation is None or errors:
        return DeletionResponse(
            committed=False,
            registry_version=int(store.registry_version()),
            warnings=warnings,
            errors=errors,
        )
    if dry_run:
        return DeletionResponse(
            committed=False,
            registry_version=int(store.registry_version()),
            warnings=warnings,
            errors=[],
        )
    await run_blocking(_delete, store, kind, record_id)
    version = _reload(request)
    await _audit(request, actor, f"{kind}_delete", object_id=record_id)
    return DeletionResponse(
        committed=True, registry_version=version, warnings=warnings, errors=[]
    )


# ---------------------------------------------------------------------------
# Secrets (write-only; reads return metadata only)
# ---------------------------------------------------------------------------
def _secret_metadata_response(meta) -> SecretMetadataResponse:
    def _iso(value):
        return value.isoformat() if hasattr(value, "isoformat") else (value or None)

    return SecretMetadataResponse(
        name=meta.name,
        scope=meta.scope,
        fingerprint=meta.fingerprint,
        key_version=meta.key_version,
        created_by=meta.created_by,
        created_at=_iso(meta.created_at),
        rotated_at=_iso(meta.rotated_at),
        last_used_at=_iso(meta.last_used_at),
    )


@router.get("/secrets", response_model=SecretListResponse)
async def list_secrets(request: Request, _actor: AdminActorDep) -> SecretListResponse:
    store = _require_enabled(request)
    metadata = await run_blocking(store.list_secret_metadata)
    return SecretListResponse(
        secrets=[_secret_metadata_response(meta) for meta in metadata]
    )


@router.post("/secrets", response_model=SecretMetadataResponse)
async def create_secret(
    request: Request, _actor: AdminActorDep, body: SecretWriteRequest
) -> SecretMetadataResponse:
    store = _require_enabled(request)
    try:
        meta = await run_blocking(
            lambda: store.put_secret(
                name=body.name,
                scope=body.scope,
                plaintext=body.value,
                created_by=str(_actor.user_id),
            )
        )
    except Exception as exc:  # noqa: BLE001 - never surface a value or cause detail
        raise ApiError(
            400, "secret_write_failed", "The secret could not be stored."
        ) from exc
    _reload(request)
    await _audit(request, _actor, "secret_create", object_id=body.name, scope=body.scope)
    return _secret_metadata_response(meta)


@router.post("/secrets/{name}/rotate", response_model=SecretMetadataResponse)
async def rotate_secret(
    request: Request, _actor: AdminActorDep, name: str, body: SecretRotateRequest
) -> SecretMetadataResponse:
    store = _require_enabled(request)
    try:
        meta = await run_blocking(
            lambda: store.put_secret(
                name=name,
                scope=body.scope,
                plaintext=body.value,
                created_by=str(_actor.user_id),
            )
        )
    except Exception as exc:  # noqa: BLE001
        raise ApiError(
            400, "secret_write_failed", "The secret could not be rotated."
        ) from exc
    _reload(request)
    await _audit(request, _actor, "secret_rotate", object_id=name, scope=body.scope)
    return _secret_metadata_response(meta)


@router.delete("/secrets/{name}", response_model=SecretListResponse)
async def delete_secret(
    request: Request, _actor: AdminActorDep, name: str, scope: str = "global"
) -> SecretListResponse:
    store = _require_enabled(request)
    await run_blocking(lambda: store.delete_secret(name=name, scope=scope))
    _reload(request)
    await _audit(request, _actor, "secret_delete", object_id=name, scope=scope)
    metadata = await run_blocking(store.list_secret_metadata)
    return SecretListResponse(
        secrets=[_secret_metadata_response(meta) for meta in metadata]
    )


# ---------------------------------------------------------------------------
# Route test (plan Task 22)
# ---------------------------------------------------------------------------
@router.post("/routes/{route_id}/test", response_model=RouteTestResponse)
async def test_route(
    request: Request, _actor: AdminActorDep, route_id: str, body: RouteTestRequest
) -> RouteTestResponse:
    _require_enabled(request)
    from app.services.inference_runtime import current_inference_runtime

    runtime = current_inference_runtime(request.app)
    gateway = runtime.gateway
    route = next(
        (r for r in gateway.all_routes if r.route_id == route_id), None
    )
    if route is None:
        raise ApiError(404, "route_not_found", "The route is not active in the runtime.")

    try:
        outcome = await run_blocking(
            lambda: run_route_test(
                gateway=gateway,
                route=route,
                max_cost_usd=body.max_cost_usd,
                allow_paid=body.allow_paid,
            )
        )
    except RouteTestError as exc:
        raise ApiError(422, "route_test_unsupported", str(exc)) from exc

    await _audit(
        request,
        _actor,
        "route_test",
        object_id=route_id,
        status=outcome.status,
        error_code=outcome.error_code,
    )
    return RouteTestResponse(
        route_id=outcome.route_id,
        capability=outcome.capability,
        status=outcome.status,
        schema_valid=outcome.schema_valid,
        latency_ms=outcome.latency_ms,
        usage=outcome.usage,
        estimated_cost_usd=outcome.estimated_cost_usd,
        actual_cost_usd=outcome.actual_cost_usd,
        cost_unknown=outcome.cost_unknown,
        error_code=outcome.error_code,
        circuit_state=outcome.circuit_state,
        paid_confirmation_required=outcome.paid_confirmation_required,
        operation_id=outcome.operation_id,
    )


# ---------------------------------------------------------------------------
# Per-route health (plan Task 22)
# ---------------------------------------------------------------------------
@router.get("/routes/health", response_model=RouteHealthResponse)
async def routes_health(request: Request, _actor: AdminActorDep) -> RouteHealthResponse:
    _require_enabled(request)
    from app.services.inference_runtime import current_inference_runtime

    runtime = current_inference_runtime(request.app)
    gateway = runtime.gateway
    operations = getattr(request.app.state, "operations_repository", None)
    slo_by_route = _slo_by_route(operations)
    db_ids = _database_route_ids(request)

    entries: list[RouteHealthEntry] = []
    for route in gateway.all_routes:
        stats = slo_by_route.get(route.route_id, {})
        entries.append(
            RouteHealthEntry(
                route_id=route.route_id,
                capability=route.capability_id,
                model=route.model,
                model_revision=route.model_revision,
                enabled=route.enabled,
                managed="database" if route.route_id in db_ids else "file",
                circuit_state=gateway.circuit_state(route),
                last_success_at=stats.get("last_success_at"),
                last_failure_at=stats.get("last_failure_at"),
                p50_latency_ms=stats.get("p50_latency_ms"),
                p95_latency_ms=stats.get("p95_latency_ms"),
                error_rate=stats.get("error_rate"),
                total_calls=stats.get("total_calls", 0),
                fallback_count=stats.get("fallback_count", 0),
                last_error_code=stats.get("last_error_code"),
                hard_cap_max_requests=route.hard_cap_max_requests,
                hard_cap_max_units=route.hard_cap_max_units,
                pricing_status=route.pricing_status,
            )
        )
    return RouteHealthResponse(database_enabled=True, routes=entries)


def _database_route_ids(request: Request) -> set[str]:
    store = getattr(request.app.state, "inference_registry_store", None)
    if store is None:
        return set()
    try:
        return {str(r.get("id")) for r in store.list_routes()}
    except Exception:  # noqa: BLE001
        return set()


def _percentile(values: list[int], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * pct))))
    return float(ordered[index])


def _slo_by_route(operations) -> dict[str, dict[str, Any]]:
    """Aggregate provider_slo_events per route (plan Task 22 health).

    Reads the raw SLO rows and groups by ``route_id`` to compute circuit-agnostic
    health: p50/p95 latency, error rate, fallback count, last success/failure.
    """
    if operations is None or not hasattr(operations, "_provider_slo_rows"):
        return {}
    try:
        rows = operations._provider_slo_rows(limit=2000)
    except Exception:  # noqa: BLE001 - a read failure yields empty health
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        route_id = row.get("route_id")
        if not route_id:
            continue
        grouped.setdefault(str(route_id), []).append(row)

    def _created(row) -> str | None:
        value = row.get("created_at")
        return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)

    stats: dict[str, dict[str, Any]] = {}
    for route_id, events in grouped.items():
        latencies = [int(e.get("latency_ms") or 0) for e in events]
        successes = [e for e in events if bool(e.get("success"))]
        failures = [e for e in events if not bool(e.get("success"))]
        total = len(events)
        last_success = max(successes, key=lambda e: str(e.get("created_at") or ""), default=None)
        last_failure = max(failures, key=lambda e: str(e.get("created_at") or ""), default=None)
        latest = max(events, key=lambda e: str(e.get("created_at") or ""))
        stats[route_id] = {
            "p50_latency_ms": _percentile(latencies, 0.50),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "error_rate": round(len(failures) / total, 4) if total else None,
            "total_calls": total,
            "fallback_count": sum(int(e.get("fallback_count") or 0) for e in events),
            "last_error_code": (last_failure or {}).get("error_code") if last_failure else None,
            "last_success_at": _created(last_success) if last_success else None,
            "last_failure_at": _created(last_failure) if last_failure else None,
        }
        # Keep the latest event's error code when it was the most recent failure.
        if not latest.get("success") and latest.get("error_code"):
            stats[route_id]["last_error_code"] = latest.get("error_code")
    return stats


__all__ = ["build_admin_inference_registry_store", "router"]
