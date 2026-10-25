"""One registry-driven inference runtime (plan Task 6, Path B).

This module replaces the environment-driven ``model_gateway_factory`` with a
single builder, :func:`build_inference_runtime`, that the API and both Python
workers use. The declarative registry file is the only source of routes
(Path B, Section 0.4): there is no ``MODEL_GATEWAY_ROUTES`` env JSON and no
vendor base-URL constants.

``build_inference_runtime`` loads ``AI_REGISTRY_FILE``, validates it with the
Section 2.6 rules, builds exactly one adapter object per valid adapter record,
converts each valid route into a gateway :class:`ModelRoute`, and registers them
on one :class:`ModelGateway` configured exactly as the old factory configured it
(Redis or in-memory ledger and circuit store, operations telemetry, live-effects
gate, monthly budget, feature budgets).

The runtime exposes two stable identities so the API and the workers can prove
they built the same catalog:

* ``registry_version`` — the SHA-256 of the canonical JSON of the registry
  document (``"empty"`` when no registry is configured);
* ``catalog_fingerprint`` — a stable hash over every registered route's id,
  capability id, adapter id, model, revision and mapping hash.

Nothing here logs a secret value, a credentialed base URL, or a request body. A
secret or auth construction failure drops the affected routes with a diagnostic;
it never crashes and never prints the value. One structured summary line is
logged per build.

Path B startup rule: when ``AI_REGISTRY_FILE`` is unset and ``APP_ENV`` is
``production`` or ``staging``, startup raises a clear :class:`RuntimeError`. Any
other app env yields an empty runtime (no routes) with a single structured
warning, so existing local/dev/test behaviour keeps working until Task 10
introduces the dev fake registry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from app.core.local_audit import local_audit_live_effects_gate_from_settings
from app.services.async_job_adapter import (
    AsyncJobAdapter,
    AsyncJobRouteConfig,
    AudioTransform,
)
from app.services.generic_http_adapter import GenericHttpAdapter, GenericHttpRouteConfig
from app.services.inference_auth import AuthConfigurationError, build_auth
from app.services.inference_operations import (
    InferenceOperationRepository,
    InMemoryInferenceOperationRepository,
)
from app.services.inference_registry import (
    AdapterRecord,
    DiagnosticCode,
    RegistryValidation,
    RouteRecord,
    load_registry_sources,
    to_model_route,
    validate_registry,
)
from app.services.inference_secrets import SecretResolver
from app.services.inference_templates import validate_mapping
from app.services.model_adapters import AzureOpenAIAdapter, OpenAICompatibleAdapter
from app.services.model_gateway import (
    InMemoryBudgetLedger,
    InMemoryCircuitStore,
    ModelGateway,
    ModelLiveEffectsGate,
    ModelRoute,
    OperationsGatewayTelemetry,
    RedisBudgetLedger,
    RedisCircuitStore,
    parse_feature_budgets,
)

logger = logging.getLogger(__name__)

# App envs that must never boot without a registry file (plan Path B). These are
# the two non-development envs the codebase recognises.
_REGISTRY_REQUIRED_ENVS = frozenset({"production", "staging"})

# A ``local`` route whose runner has no factory yet (Task 14 provides them) is
# dropped with this diagnostic code (defined on the registry's exhaustive list).
LOCAL_RUNNER_UNAVAILABLE = DiagnosticCode.LOCAL_RUNNER_UNAVAILABLE


@dataclass(frozen=True)
class DroppedRoute:
    """A route the runtime could not activate, with the reason code."""

    route_id: str
    code: str
    message: str


@dataclass(frozen=True)
class InferenceRuntime:
    """The product of one registry build: a gateway plus its identities."""

    gateway: ModelGateway
    validation: RegistryValidation
    registry_version: str
    catalog_fingerprint: str
    route_records: Mapping[str, RouteRecord]
    adapter_records: Mapping[str, AdapterRecord]
    operations_repository: InferenceOperationRepository
    dropped_routes: tuple[DroppedRoute, ...] = ()
    # The Section 2.6 startup decision (plan Task 7). Populated by
    # ``build_inference_runtime`` via ``validate_inference_startup``. In an env
    # that requires a registry (production/staging) a missing required capability
    # raises before the runtime is returned; in other envs it is stored so
    # ``/capabilities`` (Task 8) can report the gaps without crashing dev/test.
    # Typed ``object | None`` to avoid an import cycle with
    # ``inference_validation`` (which imports this module).
    startup_validation: object | None = None

    def capability_fingerprint(self, capability_ids: frozenset[str]) -> str:
        """A fingerprint restricted to the routes serving the given capabilities.

        Workers stamp this into the job payload for the capabilities a job
        requires, so a drift that does not touch those routes does not fail the
        job (plan Task 6 worker fail-closed design).
        """
        return _catalog_fingerprint(
            route
            for route in self.route_records.values()
            if route.capability.value in capability_ids
        )


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------
def _canonical_json(document: object) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _registry_version(document_source: object | None) -> str:
    """SHA-256 of the canonical JSON of the registry document (``empty`` when none)."""
    if document_source is None:
        return "empty"
    digest = hashlib.sha256(_canonical_json(document_source).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _mapping_hash(mapping: Mapping[str, object] | None) -> str:
    if not mapping:
        return "none"
    return hashlib.sha256(_canonical_json(mapping).encode("utf-8")).hexdigest()[:16]


def _catalog_fingerprint(routes) -> str:
    """A stable hash over every route's identity (id/capability/adapter/model/rev/mapping).

    This extends ``route_catalog_fingerprint`` semantics (id:model:revision) with
    capability id, adapter id and the mapping hash, without editing
    ``model_gateway.py``. Routes are sorted by id so the hash is order-stable.
    """
    identity = "|".join(
        ":".join(
            (
                route.id,
                route.capability.value,
                route.adapter_id,
                route.model_id,
                route.model_revision,
                _mapping_hash(route.mapping),
            )
        )
        for route in sorted(routes, key=lambda item: item.id)
    )
    return f"catalog:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
def build_inference_runtime(
    settings: object,
    *,
    metrics_registry: object | None = None,
    operations_repository: InferenceOperationRepository | None = None,
    live_effects_gate: ModelLiveEffectsGate | None = None,
    secret_resolver: SecretResolver | None = None,
    transport: object | None = None,
    operations_store: object | None = None,
    local_adapter_factories: Mapping[str, Callable[..., object]] | None = None,
    registry_store: object | None = None,
) -> InferenceRuntime:
    """Build the one registry-driven runtime (plan Task 6).

    ``operations_store`` is an alias accepted for the shared operations
    repository (callers differ); ``operations_repository`` wins when both are
    given. ``transport`` is an injected ``httpx`` transport for tests of the
    remote adapters. ``local_adapter_factories`` maps a ``runner`` name to a
    factory that builds a ``local`` route's adapter (Task 14 provides them).
    """
    app_env = str(getattr(settings, "app_env", "production")).strip().lower()
    registry_file = getattr(settings, "ai_registry_file", None)

    # By default, wire the real local runner factory registry (plan Task 14) so
    # a ``local`` route resolves its runner (``mediapipe_selfie_matte``,
    # ``demucs_stems``, ``yamnet_sound``, ...) without the caller passing it.
    # The import is local so the registry module (and its lazy heavy-package
    # factories) is only touched when the runtime is actually built.
    if local_adapter_factories is None:
        from app.services.inference_local import local_runner_factories

        local_adapter_factories = local_runner_factories()

    operations = _resolve_operations_repository(
        settings,
        operations_repository=operations_repository,
        operations_store=operations_store,
    )

    document, diagnostics = _load_document(registry_file, app_env)

    # Merge the database registry source when enabled (plan Task 20). The file
    # registry stays a complete fallback; the database is additive. File and
    # database ids must never collide, which ``merge_registry_documents`` turns
    # into a ``registry_fragment_collision`` error.
    if (
        document is not None
        and bool(getattr(settings, "ai_registry_database_enabled", False))
        and registry_store is not None
    ):
        document, diagnostics = _merge_database_source(
            document, diagnostics, registry_store
        )

    if document is None:
        # Either no registry (empty runtime, dev only) or an unloadable one.
        errors = [d for d in diagnostics if d.is_error]
        if errors:
            # A registry was configured but could not be loaded at all (plan Task
            # 6: "already raise if the document cannot be loaded at all").
            messages = "; ".join(f"{d.code}: {d.message}" for d in errors)
            raise RuntimeError(
                f"AI_REGISTRY_FILE could not be loaded: {messages}"
            )
        validation = RegistryValidation(
            document=None, diagnostics=(), valid_route_ids=(), dropped_route_ids=()
        )
        gateway = _build_gateway(
            settings,
            adapters={},
            routes=[],
            metrics_registry=metrics_registry,
            operations_repository=operations,
            live_effects_gate=live_effects_gate,
        )
        runtime = InferenceRuntime(
            gateway=gateway,
            validation=validation,
            registry_version="empty",
            catalog_fingerprint=_catalog_fingerprint([]),
            route_records={},
            adapter_records={},
            operations_repository=operations,
            dropped_routes=(),
        )
        runtime = _attach_startup_validation(runtime, settings, app_env)
        logger.warning(
            "inference_runtime_empty",
            extra={
                "app_env": app_env,
                "reason": "ai_registry_file_unset",
            },
        )
        _log_build_summary(runtime)
        return runtime

    registry_version = _registry_version(_document_source(document))
    validation = validate_registry(
        document,
        app_env=app_env,
        gpu_workers_enabled=bool(getattr(settings, "gpu_workers_enabled", True)),
        block_noncommercial=bool(getattr(settings, "ai_block_noncommercial", False)),
        mapping_validator=validate_mapping,
        workspace_scoped_routes_enabled=bool(
            getattr(settings, "ai_workspace_scoped_routes", False)
        ),
    )

    resolver = secret_resolver or SecretResolver(settings=settings)  # type: ignore[arg-type]

    valid_route_ids = set(validation.valid_route_ids)
    valid_routes = [r for r in document.routes if r.id in valid_route_ids]

    # Build one adapter object per adapter record referenced by a valid route.
    adapters, adapter_errors = _build_adapters(
        document=document,
        valid_routes=valid_routes,
        resolver=resolver,
        transport=transport,
        operations=operations,
        local_adapter_factories=local_adapter_factories or {},
        app_env=app_env,
        settings=settings,
    )

    model_routes: list[ModelRoute] = []
    activated_records: list[RouteRecord] = []
    dropped: list[DroppedRoute] = []
    adapter_records: dict[str, AdapterRecord] = {}

    local_factories = local_adapter_factories or {}
    for route in valid_routes:
        record = document.adapter_by_id(route.adapter_id)
        if record is None:  # pragma: no cover - validation already caught this
            continue
        drop = adapter_errors.get(route.adapter_id)
        if drop is not None:
            dropped.append(DroppedRoute(route.id, drop.code, drop.message))
            continue
        if record.type == "local":
            model_record = (
                document.model_by_id(route.model_ref)
                if route.model_ref is not None
                else None
            )
            local_adapter, local_drop = _build_local_adapter(
                route, record, model_record, local_factories, settings
            )
            if local_drop is not None:
                dropped.append(local_drop)
                continue
            adapters[route.adapter_id] = local_adapter
        if route.adapter_id not in adapters:
            # An adapter that produced no object and is not a resolvable local
            # route: nothing to register it against, so the route cannot run.
            continue
        model_route = replace(to_model_route(route, record), adapter=route.adapter_id)
        model_routes.append(model_route)
        activated_records.append(route)
        adapter_records[route.adapter_id] = record

    # Keep only adapters that back an activated route.
    used_adapters = {
        route.adapter_id: adapters[route.adapter_id]
        for route in activated_records
        if route.adapter_id in adapters
    }

    gateway = _build_gateway(
        settings,
        adapters=used_adapters,
        routes=model_routes,
        metrics_registry=metrics_registry,
        operations_repository=operations,
        live_effects_gate=live_effects_gate,
    )

    route_records = {route.id: route for route in activated_records}
    runtime = InferenceRuntime(
        gateway=gateway,
        validation=validation,
        registry_version=registry_version,
        catalog_fingerprint=_catalog_fingerprint(activated_records),
        route_records=route_records,
        adapter_records=adapter_records,
        operations_repository=operations,
        dropped_routes=tuple(dropped),
    )
    runtime = _attach_startup_validation(runtime, settings, app_env)
    _log_build_summary(runtime)
    return runtime


def _merge_database_source(document, diagnostics, registry_store):
    """Merge the database registry document into the file document (plan Task 20).

    Returns ``(merged_document_or_None, diagnostics)``. An id that appears in
    both sources is a ``registry_fragment_collision`` error (file and database
    ids must not collide, Section 2.4). A database load error is surfaced as its
    own diagnostics and aborts the build (``None`` document).
    """
    from app.services.inference_registry import merge_registry_documents

    try:
        db_document, db_diagnostics = registry_store.load_document()
    except Exception as exc:  # noqa: BLE001 - surfaced as a build failure
        logger.warning(
            "inference_registry_database_load_failed",
            extra={"error": type(exc).__name__},
        )
        raise RuntimeError("the database registry source could not be loaded") from exc
    if db_document is None:
        return None, list(diagnostics) + list(db_diagnostics)
    merged, merge_diagnostics = merge_registry_documents([document, db_document])
    return merged, list(diagnostics) + list(merge_diagnostics)


class RuntimeHolder:
    """Holds the current runtime and swaps it atomically on a version change.

    The API and both Python workers read the live runtime through a single
    holder so every reader of ``app.state.model_gateway`` /
    ``app.state.inference_runtime`` sees the current catalog after a reload
    (plan Task 20). A reload is triggered when the database
    ``ai_registry_version`` counter differs from the one the current runtime was
    built against; the new runtime is built fully before the swap, so in-flight
    calls keep using the old gateway until they finish (the swap only rebinds a
    reference).

    ``maybe_reload`` is cheap to call: it compares the stored version against the
    store's counter and rebuilds only on a change, at most once per
    ``reload_interval_s``. Construction never blocks on a reload; the first
    runtime is built eagerly from ``build``.
    """

    def __init__(
        self,
        *,
        build: Callable[[], InferenceRuntime],
        registry_store: object | None = None,
        reload_interval_s: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._build = build
        self._registry_store = registry_store
        self._reload_interval_s = max(0.0, float(reload_interval_s))
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._runtime = build()
        self._db_version = self._current_db_version()
        self._last_check = self._clock()

    @property
    def runtime(self) -> InferenceRuntime:
        with self._lock:
            return self._runtime

    @property
    def gateway(self):
        return self.runtime.gateway

    def _current_db_version(self) -> int | None:
        if self._registry_store is None:
            return None
        try:
            return int(self._registry_store.registry_version())
        except Exception:  # noqa: BLE001 - a transient read never crashes readers
            return self._db_version if hasattr(self, "_db_version") else None

    def maybe_reload(self, *, force: bool = False) -> bool:
        """Reload if the database version changed. Returns True when it swapped.

        Honours ``reload_interval_s`` throttling unless ``force`` is set. A build
        failure leaves the current runtime in place and is swallowed (readers
        keep serving the last good catalog); it is re-raised only with ``force``.
        """
        if self._registry_store is None:
            return False
        now = self._clock()
        with self._lock:
            if not force and self._reload_interval_s > 0:
                if now - self._last_check < self._reload_interval_s:
                    return False
            self._last_check = now
            latest = self._current_db_version()
            if not force and latest == self._db_version:
                return False
            try:
                new_runtime = self._build()
            except Exception:  # noqa: BLE001
                if force:
                    raise
                logger.warning("inference_runtime_reload_failed")
                return False
            self._runtime = new_runtime
            self._db_version = latest
            return True


def build_inference_registry_store(
    settings: object, metrics_registry: object | None = None
) -> object | None:
    """Build the database registry store for the catalog, or ``None``.

    Returns a store only when ``AI_REGISTRY_DATABASE_ENABLED`` is true and
    Supabase is configured. The store is used for the catalog
    (adapters/models/routes) and the version counter; it is built **without** a
    secret store, so no ``ai_secrets`` read is reachable from startup or the
    workers (plan Task 20: the worker reads only the catalog tables). Secret
    decryption for ``db:`` refs is wired separately in the admin/ops context.
    """
    if not bool(getattr(settings, "ai_registry_database_enabled", False)):
        return None
    supabase_url = getattr(settings, "supabase_url", None)
    service_role_key = getattr(settings, "supabase_service_role_key", None)
    if not supabase_url or not service_role_key:
        return None
    from app.services.inference_registry_store import (
        SupabaseInferenceRegistryRepository,
    )

    return SupabaseInferenceRegistryRepository(
        supabase_url=str(supabase_url),
        service_role_key=str(service_role_key),
        metrics_registry=metrics_registry,
    )


def current_inference_runtime(app: object) -> InferenceRuntime:
    """The live runtime for any reader of ``app.state`` (plan Task 20).

    Prefers the :class:`RuntimeHolder` (which reflects reloads) and falls back to
    the eagerly built ``app.state.inference_runtime`` so existing readers keep
    working whether or not the database source is enabled.
    """
    state = getattr(app, "state", app)
    holder = getattr(state, "inference_runtime_holder", None)
    if holder is not None:
        holder.maybe_reload()
        return holder.runtime
    return state.inference_runtime



# Built-in development registry (plan Task 10). When AI_REGISTRY_FILE is unset in
# a non-production app env, the runtime loads this in-memory registry so
# create_app works out of the box without any external configuration. It defines
# one fake ``planning.chat`` route and one fake ``transcription.speech_to_text``
# route backed by the deterministic fake adapter (no network). Both are pinned,
# ``network: private`` and ``explicitly_free``, so a fake route never crosses the
# live-effects boundary (the LOCAL_AUDIT_MODE guarantee). Planning falls back to
# the deterministic fixture plan, and transcription returns a schema-valid
# fixture transcript, reproducing what the old dev fixture providers returned.
_DEV_FAKE_REGISTRY: dict[str, object] = {
    "version": 1,
    "adapters": [
        {"id": "dev-fake-endpoint", "type": "fake", "network": "private"},
    ],
    "models": [
        {
            "id": "dev-fake-plan-model",
            "model_id": "dev-fake-plan-model",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        },
        {
            "id": "dev-fake-stt-model",
            "model_id": "dev-fake-stt-model",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        },
    ],
    "routes": [
        {
            "id": "dev-planning-fake",
            "capability": "planning.chat",
            "adapter_id": "dev-fake-endpoint",
            "model_id": "dev-fake-plan-model",
            "model_ref": "dev-fake-plan-model",
            "model_revision": "2026-01-01",
            "pricing": {"status": "free"},
            "explicitly_free": True,
            "budget": {"feature": "planning"},
        },
        {
            "id": "dev-transcription-fake",
            "capability": "transcription.speech_to_text",
            "adapter_id": "dev-fake-endpoint",
            "model_id": "dev-fake-stt-model",
            "model_ref": "dev-fake-stt-model",
            "model_revision": "2026-01-01",
            "pricing": {"status": "free"},
            "explicitly_free": True,
            "budget": {"feature": "transcription"},
        },
    ],
}


def _load_document(registry_file: object | None, app_env: str):
    if not registry_file:
        if app_env in _REGISTRY_REQUIRED_ENVS:
            raise RuntimeError(
                "AI_REGISTRY_FILE is required in production and staging; the "
                "declarative registry is the only source of inference routes"
            )
        # Dev/test boot: no external registry is configured, so serve the
        # built-in fake planning and transcription routes (plan Task 10). Parsed
        # through the same strict loader as a file registry.
        from app.services.inference_registry import load_registry_document

        return load_registry_document(_DEV_FAKE_REGISTRY)
    # AI_REGISTRY_FILE may be a single path or a comma-separated list of
    # fragments (plan Task 14): operators compose a remote registry with the
    # local cpu/gpu fragments. ``load_registry_sources`` merges them
    # deterministically and errors on an id collision across fragments.
    return load_registry_sources(str(registry_file))


def _document_source(document) -> dict[str, object]:
    """A deterministic JSON-shaped view of the parsed document for versioning.

    Reads the raw file would be simpler but a parsed, re-serialized view is
    immune to formatting differences between two deployments that ship the same
    logical registry.
    """
    return {
        "version": document.version,
        "adapters": [_adapter_source(a) for a in document.adapters],
        "models": [_model_source(m) for m in document.models],
        "routes": [_route_source(r) for r in document.routes],
    }


def _adapter_source(adapter: AdapterRecord) -> dict[str, object]:
    return {
        "id": adapter.id,
        "type": adapter.type,
        "base_url": adapter.base_url,
        "network": adapter.network,
        "auth_type": adapter.auth.type,
        "auth_secret_ref": adapter.auth.secret_ref,
        "api_version": adapter.api_version,
        "extra_headers": dict(adapter.extra_headers),
        "extra_body": dict(adapter.extra_body),
        "timeout_s": adapter.timeout_s,
    }


def _model_source(model) -> dict[str, object]:
    return {
        "id": model.id,
        "model_id": model.model_id,
        "revision": model.revision,
        "licence": model.licence,
        "commercial_use": model.commercial_use,
    }


def _route_source(route: RouteRecord) -> dict[str, object]:
    return {
        "id": route.id,
        "capability": route.capability.value,
        "adapter_id": route.adapter_id,
        "model_id": route.model_id,
        "model_revision": route.model_revision,
        "priority": route.priority,
        "enabled": route.enabled,
        "mapping": dict(route.mapping) if route.mapping is not None else None,
        "params": dict(route.params),
    }


# ---------------------------------------------------------------------------
# Adapter construction
# ---------------------------------------------------------------------------
def _build_adapters(
    *,
    document,
    valid_routes,
    resolver: SecretResolver,
    transport: object | None,
    operations: InferenceOperationRepository,
    local_adapter_factories: Mapping[str, Callable[..., object]],
    app_env: str,
    settings: object,
) -> tuple[dict[str, object], dict[str, DroppedRoute]]:
    """Build one adapter object per adapter record backing a valid route.

    Returns ``(adapters_by_record_id, adapter_errors_by_record_id)``. An adapter
    whose secret/auth construction fails is recorded in the error map so every
    route that references it is dropped with a diagnostic; it never crashes and
    never prints the value.
    """
    adapters: dict[str, object] = {}
    errors: dict[str, DroppedRoute] = {}
    routes_by_adapter: dict[str, list[RouteRecord]] = {}
    for route in valid_routes:
        routes_by_adapter.setdefault(route.adapter_id, []).append(route)

    for adapter_id, routes in routes_by_adapter.items():
        record = document.adapter_by_id(adapter_id)
        if record is None:  # pragma: no cover - validation already caught this
            continue
        try:
            adapter = _build_one_adapter(
                record=record,
                routes=routes,
                resolver=resolver,
                transport=transport,
                operations=operations,
                local_adapter_factories=local_adapter_factories,
                app_env=app_env,
                settings=settings,
            )
        except _AdapterDropped as drop:
            # Record one error so every route on this adapter is dropped. The
            # reason never contains a secret value.
            errors[adapter_id] = DroppedRoute(
                route_id=adapter_id, code=drop.code, message=drop.message
            )
            continue
        if adapter is not None:
            adapters[adapter_id] = adapter
    return adapters, errors


class _AdapterDropped(Exception):
    """An adapter could not be constructed; its routes are dropped."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _build_one_adapter(
    *,
    record: AdapterRecord,
    routes,
    resolver: SecretResolver,
    transport: object | None,
    operations: InferenceOperationRepository,
    local_adapter_factories: Mapping[str, Callable[..., object]],
    app_env: str,
    settings: object,
):
    adapter_type = record.type
    if adapter_type in {"openai_compatible", "azure_openai"}:
        return _build_openai_compatible(record, resolver, app_env)
    if adapter_type == "generic_http":
        return _build_generic_http(record, routes, resolver, transport)
    if adapter_type == "async_job":
        return _build_async_job(record, routes, resolver, transport, operations)
    if adapter_type == "huggingface_endpoint":
        return _build_huggingface_endpoint(record, resolver, transport)
    if adapter_type == "aws_bedrock":
        return _build_aws_bedrock(record, settings, transport)
    if adapter_type == "fake":
        return _build_fake(app_env)
    if adapter_type == "local":
        # Local adapters have no single per-adapter object; each route resolves
        # its runner factory. A missing factory drops the route (handled by
        # _local_route_drop), so return None here.
        return None
    raise _AdapterDropped(
        "adapter_type_unknown", f"unsupported adapter type {adapter_type!r}"
    )


def _build_openai_compatible(
    record: AdapterRecord, resolver: SecretResolver, app_env: str
):
    try:
        auth = build_auth(record.auth, resolver, provider=record.id)
    except AuthConfigurationError as exc:
        # The message never carries a secret value (inference_auth guarantees it).
        raise _AdapterDropped("auth", f"auth could not be built: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - e.g. a missing secret file
        raise _AdapterDropped(
            "secret", f"adapter secret could not be resolved: {type(exc).__name__}"
        ) from exc
    # allow_insecure_http is only safe for private-network adapters (http base).
    allow_insecure_http = record.network == "private"
    timeout = record.timeout_s if record.timeout_s is not None else 60.0
    common = {
        "base_url": str(record.base_url),
        "auth": auth,
        "extra_headers": dict(record.extra_headers),
        "extra_body": dict(record.extra_body),
        "timeout_seconds": timeout,
        "allow_insecure_http": allow_insecure_http,
        "embeddings_input_style": record.embeddings_input_style,
    }
    try:
        if record.type == "azure_openai":
            return AzureOpenAIAdapter(
                api_version=record.api_version or "2025-01-01-preview", **common
            )
        return OpenAICompatibleAdapter(**common)
    except ValueError as exc:
        raise _AdapterDropped("adapter_invalid", f"adapter is invalid: {exc}") from exc


def _build_generic_http(
    record: AdapterRecord, routes, resolver: SecretResolver, transport: object | None
):
    try:
        auth = build_auth(record.auth, resolver, provider=record.id)
    except AuthConfigurationError as exc:
        raise _AdapterDropped("auth", f"auth could not be built: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise _AdapterDropped(
            "secret", f"adapter secret could not be resolved: {type(exc).__name__}"
        ) from exc
    route_configs: dict[str, GenericHttpRouteConfig] = {}
    for route in routes:
        if route.mapping is None:  # pragma: no cover - validation requires a mapping
            continue
        config_kwargs: dict[str, object] = {
            "mapping": dict(route.mapping),
            "params": dict(route.params),
            "label_aliases": dict(route.label_aliases),
        }
        if route.limits.max_batch is not None:
            config_kwargs["max_batch"] = route.limits.max_batch
        if route.limits.max_request_bytes is not None:
            config_kwargs["max_request_bytes"] = route.limits.max_request_bytes
        try:
            route_configs[route.id] = GenericHttpRouteConfig(**config_kwargs)
        except ValueError as exc:
            raise _AdapterDropped(
                "mapping_invalid", f"route mapping is invalid: {exc}"
            ) from exc
    try:
        return GenericHttpAdapter(
            base_url=str(record.base_url),
            auth=auth,
            timeout_s=record.timeout_s if record.timeout_s is not None else 60.0,
            provider=record.id,
            extra_headers=dict(record.extra_headers),
            transport=transport,  # type: ignore[arg-type]
            routes=route_configs,
        )
    except ValueError as exc:
        raise _AdapterDropped("adapter_invalid", f"adapter is invalid: {exc}") from exc


def _build_async_job(
    record: AdapterRecord,
    routes,
    resolver: SecretResolver,
    transport: object | None,
    operations: InferenceOperationRepository,
):
    try:
        auth = build_auth(record.auth, resolver, provider=record.id)
    except AuthConfigurationError as exc:
        raise _AdapterDropped("auth", f"auth could not be built: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise _AdapterDropped(
            "secret", f"adapter secret could not be resolved: {type(exc).__name__}"
        ) from exc
    route_configs: dict[str, AsyncJobRouteConfig] = {}
    for route in routes:
        if route.mapping is None:  # pragma: no cover - validation requires a mapping
            continue
        audio_transform = None
        if route.input_transform is not None and route.input_transform.audio_codec:
            audio_transform = AudioTransform(
                codec=route.input_transform.audio_codec,
                sample_rate=route.input_transform.audio_sample_rate or 44100,
                channels=route.input_transform.audio_channels or 2,
            )
        try:
            route_configs[route.id] = AsyncJobRouteConfig(
                mapping=dict(route.mapping),
                params=dict(route.params),
                input_transform_audio=audio_transform,
                upload_filename=route.upload_filename,
            )
        except ValueError as exc:
            raise _AdapterDropped(
                "mapping_invalid", f"route mapping is invalid: {exc}"
            ) from exc
    try:
        return AsyncJobAdapter(
            base_url=str(record.base_url),
            auth=auth,
            operations_repository=operations,
            provider=record.id,
            extra_headers=dict(record.extra_headers),
            transport=transport,  # type: ignore[arg-type]
            routes=route_configs,
        )
    except ValueError as exc:
        raise _AdapterDropped("adapter_invalid", f"adapter is invalid: {exc}") from exc
def _build_huggingface_endpoint(
    record: AdapterRecord, resolver: SecretResolver, transport: object | None
):
    """Build a ``huggingface_endpoint`` adapter (plan Task 24).

    Auth is bearer, built with ``inference_auth.build_auth`` (the registry
    validator already pinned the type to bearer for a hosted endpoint). The
    config block mirrors ``HuggingFaceEndpointConfig``; an invalid config drops
    the adapter's routes with a diagnostic rather than crashing.
    """
    from app.services.huggingface_endpoint_adapter import (
        HuggingFaceConfigError,
        HuggingFaceEndpointConfig,
        huggingface_endpoint_factory,
    )

    try:
        auth = build_auth(record.auth, resolver, provider=record.id)
    except AuthConfigurationError as exc:
        raise _AdapterDropped("auth", f"auth could not be built: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise _AdapterDropped(
            "secret", f"adapter secret could not be resolved: {type(exc).__name__}"
        ) from exc
    config = dict(record.config)
    kwargs: dict[str, object] = {
        "base_url": str(record.base_url),
        "mode": config.get("mode"),
    }
    if config.get("pipeline_task") is not None:
        kwargs["pipeline_task"] = config["pipeline_task"]
    if config.get("timeout_s") is not None:
        kwargs["timeout_s"] = float(config["timeout_s"])  # type: ignore[arg-type]
    if config.get("max_response_bytes") is not None:
        kwargs["max_response_bytes"] = int(config["max_response_bytes"])  # type: ignore[arg-type]
    if record.extra_headers:
        kwargs["extra_headers"] = dict(record.extra_headers)
    try:
        endpoint_config = HuggingFaceEndpointConfig(**kwargs)  # type: ignore[arg-type]
    except HuggingFaceConfigError as exc:
        raise _AdapterDropped(
            DiagnosticCode.ADAPTER_CONFIG_INVALID, f"adapter config is invalid: {exc}"
        ) from exc
    return huggingface_endpoint_factory(
        config=endpoint_config,
        auth=auth,
        provider=record.id,
        transport=transport,  # type: ignore[arg-type]
    )


def _build_aws_bedrock(
    record: AdapterRecord, settings: object, transport: object | None
):
    """Build an ``aws_bedrock`` adapter (plan Task 25).

    No AI secret is resolved: credentials come from the IAM chain (env/ECS/IMDS)
    read through the process environment. The config block mirrors
    ``BedrockConfig``; an invalid config drops the adapter's routes.
    """
    import os

    from app.services.aws_bedrock_adapter import (
        BedrockConfig,
        BedrockConfigError,
        aws_bedrock_factory,
    )

    config = dict(record.config)
    kwargs: dict[str, object] = {
        "region": config.get("region"),
        "api": config.get("api"),
    }
    if config.get("embedding_family") is not None:
        kwargs["embedding_family"] = config["embedding_family"]
    if config.get("timeout_s") is not None:
        kwargs["timeout_s"] = float(config["timeout_s"])  # type: ignore[arg-type]
    if record.extra_headers:
        kwargs["extra_headers"] = dict(record.extra_headers)
    try:
        bedrock_config = BedrockConfig(**kwargs)  # type: ignore[arg-type]
    except BedrockConfigError as exc:
        raise _AdapterDropped(
            DiagnosticCode.ADAPTER_CONFIG_INVALID, f"adapter config is invalid: {exc}"
        ) from exc
    env = getattr(settings, "aws_credential_env", None)
    if not isinstance(env, Mapping):
        env = dict(os.environ)
    return aws_bedrock_factory(
        config=bedrock_config,
        env=env,
        provider=record.id,
        bedrock_transport=transport,  # type: ignore[arg-type]
        credential_transport=transport,  # type: ignore[arg-type]
    )


def _build_fake(app_env: str):
    # The registry validator already errors on a fake adapter in production, but
    # the fake adapter itself also refuses production construction.
    from app.services.inference_fake import DeterministicInferenceAdapter

    return DeterministicInferenceAdapter(app_env=app_env)


def _build_local_adapter(
    route: RouteRecord,
    record: AdapterRecord,
    model_record: object | None,
    local_adapter_factories: Mapping[str, Callable[..., object]],
    settings: object,
) -> tuple[object | None, DroppedRoute | None]:
    """Build a ``local`` route's adapter via its runner factory, or drop it.

    The runner name comes from ``route.params["runner"]`` and is looked up in
    the factory registry (``app.services.inference_local.local_runner_factories``
    by default, wired in :func:`build_inference_runtime`). A missing factory, or
    a factory that cannot load its runner, drops the route with
    ``local_runner_unavailable`` so startup keeps working without that route.
    """
    runner = route.params.get("runner")
    factory = (
        local_adapter_factories.get(runner) if isinstance(runner, str) else None
    )
    if factory is None:
        return None, DroppedRoute(
            route_id=route.id,
            code=LOCAL_RUNNER_UNAVAILABLE,
            message=(
                f"local route runner {runner!r} has no registered adapter factory"
            ),
        )
    try:
        adapter = factory(
            route=route, adapter=record, model=model_record, settings=settings
        )
    except Exception as exc:  # noqa: BLE001 - a runner that cannot load drops the route
        return None, DroppedRoute(
            route_id=route.id,
            code=LOCAL_RUNNER_UNAVAILABLE,
            message=f"local runner could not be built: {type(exc).__name__}",
        )
    return adapter, None


# ---------------------------------------------------------------------------
# Gateway construction (moved from model_gateway_factory, Path B)
# ---------------------------------------------------------------------------
def _build_gateway(
    settings: object,
    *,
    adapters: Mapping[str, object],
    routes,
    metrics_registry: object | None,
    operations_repository: object | None,
    live_effects_gate: ModelLiveEffectsGate | None,
) -> ModelGateway:
    redis_url = getattr(settings, "redis_url", None)
    failure_threshold = int(getattr(settings, "model_gateway_failure_threshold", 3))
    cooldown_seconds = int(
        getattr(settings, "model_gateway_circuit_cooldown_seconds", 60)
    )
    if redis_url:
        budget_ledger = RedisBudgetLedger(str(redis_url))
        circuit_store = RedisCircuitStore(
            str(redis_url),
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )
    else:
        budget_ledger = InMemoryBudgetLedger()
        circuit_store = InMemoryCircuitStore(
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )

    return ModelGateway(
        adapters=adapters,  # type: ignore[arg-type]
        routes=routes,
        budget_ledger=budget_ledger,
        circuit_store=circuit_store,
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=metrics_registry,
            operations_repository=operations_repository,
        ),
        live_effects_gate=live_effects_gate
        or local_audit_live_effects_gate_from_settings(settings),
        monthly_budget_usd=float(
            getattr(settings, "model_gateway_monthly_budget_usd", 100.0)
        ),
        request_budget_usd=float(
            getattr(settings, "model_gateway_request_budget_usd", 5.0)
        ),
        feature_budgets_usd=parse_feature_budgets(
            getattr(settings, "model_gateway_feature_budgets_usd", "")
        ),
    )


# ---------------------------------------------------------------------------
# Operations repository selection
# ---------------------------------------------------------------------------
def _resolve_operations_repository(
    settings: object,
    *,
    operations_repository: InferenceOperationRepository | None,
    operations_store: object | None,
) -> InferenceOperationRepository:
    if operations_repository is not None:
        return operations_repository
    if operations_store is not None:
        return operations_store  # type: ignore[return-value]
    supabase_url = getattr(settings, "supabase_url", None)
    service_key = getattr(settings, "supabase_service_role_key", None)
    if supabase_url and service_key:
        from app.repositories.inference_operations import (
            SupabaseInferenceOperationRepository,
        )

        return SupabaseInferenceOperationRepository(
            supabase_url=str(supabase_url),
            service_role_key=str(service_key),
        )
    return InMemoryInferenceOperationRepository()


# ---------------------------------------------------------------------------
# Startup validation (plan Task 7)
# ---------------------------------------------------------------------------
def _attach_startup_validation(
    runtime: InferenceRuntime, settings: object, app_env: str
) -> InferenceRuntime:
    """Run ``validate_inference_startup`` and attach the result to the runtime.

    In an env that requires a registry (production/staging) a missing required
    capability raises :class:`InferenceStartupError`, so startup fails. In any
    other env the error is caught and the gap is recorded on the returned
    validation, so dev/test keep booting (the empty registry is expected there)
    while ``/capabilities`` still reports the required capabilities as
    unavailable. The import is local to break the import cycle
    (``inference_validation`` imports this module).
    """
    from dataclasses import replace

    from app.services.inference_validation import validate_inference_startup

    raise_on_missing = app_env in _REGISTRY_REQUIRED_ENVS
    startup = validate_inference_startup(
        runtime, settings=settings, raise_on_missing=raise_on_missing
    )
    return replace(runtime, startup_validation=startup)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _log_build_summary(runtime: InferenceRuntime) -> None:
    counts: dict[str, int] = {}
    for route in runtime.route_records.values():
        counts[route.capability.value] = counts.get(route.capability.value, 0) + 1
    logger.info(
        "inference_runtime_built",
        extra={
            "registry_version": runtime.registry_version,
            "catalog_fingerprint": runtime.catalog_fingerprint,
            "route_counts": counts,
            "dropped_routes": [
                {"route_id": d.route_id, "code": d.code}
                for d in runtime.dropped_routes
            ],
        },
    )
