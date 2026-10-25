"""Admin inference registry API tests (plan Tasks 21 and 22).

The endpoints are exercised at the handler level with an in-memory registry
store (Postgres cannot run here). Coverage: every endpoint, admin authorization
and non-admin 403, no secret in any response, duplicate/invalid records, the
file-managed read-only rule, the route-deletion-while-in-use refusal, the
disabled-without-flag gate, secret write-only metadata, route test and per-route
health.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.admin import require_admin
from app.api.admin_inference import (
    create_adapter,
    create_model,
    create_route,
    create_secret,
    delete_route,
    delete_secret,
    get_registry,
    list_secrets,
    rotate_secret,
    routes_health,
)
from app.api.admin_inference import (
    test_route as route_test_endpoint,
)
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.schemas.admin_inference import (
    RegistryRecordRequest,
    RouteTestRequest,
    SecretRotateRequest,
    SecretWriteRequest,
)
from app.services.inference_capabilities import InferenceCapability
from app.services.inference_fake import DeterministicInferenceAdapter
from app.services.inference_operations import InMemoryInferenceOperationRepository
from app.services.inference_registry_store import InMemoryInferenceRegistryRepository
from app.services.inference_secret_store import InferenceSecretStore
from app.services.model_gateway import (
    InMemoryBudgetLedger,
    InMemoryCircuitStore,
    ModelCapability,
    ModelGateway,
    ModelRoute,
)
from app.services.operations import OperationsRepository


def _secret_store(tmp_path) -> InferenceSecretStore:
    path = tmp_path / "kek.json"
    path.write_text(
        json.dumps({"key_version": "v1", "key_b64": base64.b64encode(os.urandom(32)).decode()})
    )
    return InferenceSecretStore.from_files(current_file=str(path))


class _RecordingDirectory:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def record_audit_event(self, **kwargs) -> None:
        self.events.append(kwargs)


def _gateway() -> ModelGateway:
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = ModelRoute(
        route_id="db-vision",
        capability=ModelCapability.VISION,
        adapter="fake",
        provider="db-vision",
        model="db-cap-model",
        model_revision="2026-01-01",
        explicitly_free=True,
        capability_id=str(InferenceCapability.VISION_CAPTION),
    )
    return ModelGateway(
        adapters={"fake": adapter},
        routes=[route],
        budget_ledger=InMemoryBudgetLedger(),
        circuit_store=InMemoryCircuitStore(),
        request_budget_usd=100.0,
        monthly_budget_usd=1000.0,
    )


def _make_request(
    tmp_path,
    *,
    enabled: bool = True,
    store: InMemoryInferenceRegistryRepository | None = None,
    operations: object | None = None,
    gateway: ModelGateway | None = None,
):
    store = store or InMemoryInferenceRegistryRepository(secret_store=_secret_store(tmp_path))
    operations = operations or InMemoryInferenceOperationRepository()
    gateway = gateway or _gateway()
    runtime = SimpleNamespace(gateway=gateway)
    directory = _RecordingDirectory()
    state = SimpleNamespace(
        settings=SimpleNamespace(
            app_env="test",
            ai_registry_database_enabled=enabled,
            ai_registry_file=None,
            gpu_workers_enabled=True,
            ai_block_noncommercial=False,
            ai_workspace_scoped_routes=False,
        ),
        inference_registry_store=store,
        inference_runtime=runtime,
        inference_runtime_holder=None,
        operations_repository=operations,
        admin_user_directory=directory,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace())
    return request, store, directory


def _actor() -> Actor:
    return Actor(user_id=uuid4(), workspace_id=uuid4())


ADAPTER = {"id": "db-fake", "type": "fake", "network": "private"}
MODEL = {
    "id": "db-cap-model",
    "model_id": "db-cap-model",
    "revision": "2026-01-01",
    "licence": "Apache-2.0",
    "commercial_use": True,
}
ROUTE = {
    "id": "db-vision",
    "capability": "vision.caption",
    "adapter_id": "db-fake",
    "model_id": "db-cap-model",
    "model_ref": "db-cap-model",
    "model_revision": "2026-01-01",
    "pricing": {"status": "free"},
    "explicitly_free": True,
}


def _seed(store) -> None:
    store.upsert_adapter(ADAPTER)
    store.upsert_model(MODEL)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------
def test_require_admin_rejects_non_admin() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    admin_api_token=None,
                    admin_user_id_list=set(),
                    admin_workspace_id_list=set(),
                )
            )
        )
    )
    with pytest.raises(ApiError) as exc:
        asyncio.run(require_admin(request, _actor(), None))
    assert exc.value.status_code == 403


def test_disabled_without_flag_returns_503(tmp_path) -> None:
    request, _store, _dir = _make_request(tmp_path, enabled=False)
    with pytest.raises(ApiError) as exc:
        asyncio.run(get_registry(request, _actor()))
    assert exc.value.status_code == 503
    assert exc.value.code == "inference_registry_disabled"


# ---------------------------------------------------------------------------
# CRUD + dry-run + validation warnings
# ---------------------------------------------------------------------------
def test_create_adapter_model_route_commits_and_bumps_version(tmp_path) -> None:
    request, store, directory = _make_request(tmp_path)
    r1 = asyncio.run(create_adapter(request, _actor(), RegistryRecordRequest(record=ADAPTER)))
    assert r1.committed is True
    r2 = asyncio.run(create_model(request, _actor(), RegistryRecordRequest(record=MODEL)))
    assert r2.committed is True
    r3 = asyncio.run(create_route(request, _actor(), RegistryRecordRequest(record=ROUTE)))
    assert r3.committed is True
    assert r3.registry_version == store.registry_version()
    assert r3.registry_version >= 3
    # Every change is audited.
    actions = [e["action"] for e in directory.events]
    assert "inference_registry_route_upsert" in actions


def test_dry_run_does_not_commit(tmp_path) -> None:
    request, store, _dir = _make_request(tmp_path)
    _seed(store)
    before = store.registry_version()
    result = asyncio.run(
        create_route(request, _actor(), RegistryRecordRequest(record=ROUTE), dry_run=True)
    )
    assert result.committed is False
    assert store.registry_version() == before
    assert result.errors == []


def test_invalid_route_is_refused_with_errors(tmp_path) -> None:
    request, store, _dir = _make_request(tmp_path)
    _seed(store)
    # A floating revision is a Section 2.6 error; the change must not commit.
    bad = {**ROUTE, "model_revision": "latest"}
    result = asyncio.run(create_route(request, _actor(), RegistryRecordRequest(record=bad)))
    assert result.committed is False
    assert any(d.severity == "error" for d in result.errors)
    assert store.list_routes() == []


def test_missing_id_is_rejected(tmp_path) -> None:
    request, _store, _dir = _make_request(tmp_path)
    body = RegistryRecordRequest(record={"type": "fake"})
    with pytest.raises(ApiError) as exc:
        asyncio.run(create_adapter(request, _actor(), body))
    assert exc.value.status_code == 422


def test_duplicate_id_upsert_replaces_not_duplicates(tmp_path) -> None:
    request, store, _dir = _make_request(tmp_path)
    asyncio.run(create_adapter(request, _actor(), RegistryRecordRequest(record=ADAPTER)))
    asyncio.run(create_adapter(request, _actor(), RegistryRecordRequest(record=ADAPTER)))
    assert len([a for a in store.list_adapters() if a["id"] == "db-fake"]) == 1


def test_file_managed_id_is_read_only(tmp_path) -> None:
    request, _store, _dir = _make_request(tmp_path)
    # dev-planning-fake is a file route in the dev registry.
    clash = {**ROUTE, "id": "dev-planning-fake"}
    with pytest.raises(ApiError) as exc:
        asyncio.run(create_route(request, _actor(), RegistryRecordRequest(record=clash)))
    assert exc.value.status_code == 409
    assert exc.value.code == "managed_by_file"


def test_registry_snapshot_marks_entries_database_managed(tmp_path) -> None:
    request, store, _dir = _make_request(tmp_path)
    _seed(store)
    store.upsert_route(ROUTE)
    snapshot = asyncio.run(get_registry(request, _actor()))
    assert snapshot.database_enabled is True
    assert all(e.managed == "database" for e in snapshot.routes)
    assert {e.id for e in snapshot.routes} == {"db-vision"}


# ---------------------------------------------------------------------------
# Route deletion guard
# ---------------------------------------------------------------------------
def test_route_delete_refused_while_operation_non_terminal(tmp_path) -> None:
    operations = InMemoryInferenceOperationRepository()
    request, store, _dir = _make_request(tmp_path, operations=operations)
    _seed(store)
    store.upsert_route(ROUTE)
    operations.reserve(
        workspace_id=uuid4(),
        project_id=None,
        capability="vision.caption",
        idempotency_key="k1",
        route_id="db-vision",
        model="db-cap-model",
        model_revision="2026-01-01",
        route_snapshot={"id": "db-vision"},
    )
    with pytest.raises(ApiError) as exc:
        asyncio.run(delete_route(request, _actor(), "db-vision"))
    assert exc.value.status_code == 409
    assert exc.value.code == "route_in_use"


def test_route_delete_allowed_when_no_pending_operation(tmp_path) -> None:
    request, store, _dir = _make_request(tmp_path)
    _seed(store)
    store.upsert_route(ROUTE)
    result = asyncio.run(delete_route(request, _actor(), "db-vision"))
    assert result.committed is True
    assert store.list_routes() == []


# ---------------------------------------------------------------------------
# Secrets: write-only, metadata-only reads
# ---------------------------------------------------------------------------
def test_secret_create_returns_metadata_only_never_value(tmp_path) -> None:
    request, store, directory = _make_request(tmp_path)
    body = SecretWriteRequest(name="db-chat", scope="global", value="super-secret-value")
    meta = asyncio.run(create_secret(request, _actor(), body))
    dumped = meta.model_dump_json()
    assert "super-secret-value" not in dumped
    assert meta.fingerprint
    # The list also returns metadata only.
    listing = asyncio.run(list_secrets(request, _actor()))
    assert "super-secret-value" not in listing.model_dump_json()
    assert listing.secrets[0].name == "db-chat"
    # The stored value is openable through the store (API/ops only).
    assert store.open_secret(name="db-chat") == "super-secret-value"


def test_secret_rotate_changes_fingerprint(tmp_path) -> None:
    request, store, _dir = _make_request(tmp_path)
    first = asyncio.run(
        create_secret(request, _actor(), SecretWriteRequest(name="k", value="one"))
    )
    rotated = asyncio.run(
        rotate_secret(request, _actor(), "k", SecretRotateRequest(value="two"))
    )
    assert rotated.fingerprint != first.fingerprint
    assert store.open_secret(name="k") == "two"


def test_secret_delete_removes_it(tmp_path) -> None:
    request, store, _dir = _make_request(tmp_path)
    asyncio.run(create_secret(request, _actor(), SecretWriteRequest(name="k", value="v")))
    listing = asyncio.run(delete_secret(request, _actor(), "k"))
    assert listing.secrets == []


# ---------------------------------------------------------------------------
# Route test + health endpoints
# ---------------------------------------------------------------------------
def test_route_test_endpoint_reports_ok(tmp_path) -> None:
    request, _store, directory = _make_request(tmp_path)
    result = asyncio.run(
        route_test_endpoint(request, _actor(), "db-vision", RouteTestRequest(max_cost_usd=0.5))
    )
    assert result.status == "ok"
    assert result.schema_valid is True
    assert result.capability == str(InferenceCapability.VISION_CAPTION)
    assert any(e["action"] == "inference_registry_route_test" for e in directory.events)


def test_route_test_unknown_route_is_404(tmp_path) -> None:
    request, _store, _dir = _make_request(tmp_path)
    with pytest.raises(ApiError) as exc:
        asyncio.run(route_test_endpoint(request, _actor(), "nope", RouteTestRequest()))
    assert exc.value.status_code == 404


def test_routes_health_reports_circuit_and_slo(tmp_path) -> None:
    operations = OperationsRepository()
    operations.record_provider_slo(
        workspace_id=str(uuid4()),
        project_id=str(uuid4()),
        provider="db-vision",
        operation="vision.caption",
        route_id="db-vision",
        success=True,
        latency_ms=120,
        fallback_count=0,
    )
    operations.record_provider_slo(
        workspace_id=str(uuid4()),
        project_id=str(uuid4()),
        provider="db-vision",
        operation="vision.caption",
        route_id="db-vision",
        success=False,
        latency_ms=800,
        error_code="provider_timeout",
        fallback_count=1,
    )
    request, store, _dir = _make_request(tmp_path, operations=operations)
    store.upsert_route(ROUTE)
    health = asyncio.run(routes_health(request, _actor()))
    entry = next(e for e in health.routes if e.route_id == "db-vision")
    assert entry.circuit_state in {"open", "closed", "unknown"}
    assert entry.total_calls == 2
    assert entry.error_rate == 0.5
    assert entry.p95_latency_ms is not None
    assert entry.fallback_count == 1
    assert entry.managed == "database"
    assert entry.last_error_code == "provider_timeout"


# ---------------------------------------------------------------------------
# HTTP-level: the require_admin guard and the disabled gate on the live app
# ---------------------------------------------------------------------------
def _http_app(tmp_path, *, enabled: bool = True):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    app.state.settings.skip_api_auth = True
    app.state.settings.admin_api_token = "admin-token"
    app.state.settings.ai_registry_database_enabled = enabled
    if enabled:
        app.state.inference_registry_store = InMemoryInferenceRegistryRepository(
            secret_store=_secret_store(tmp_path)
        )
    client = TestClient(app, raise_server_exceptions=False)
    return client


def test_http_non_admin_is_forbidden(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    client = _http_app(tmp_path)
    # No admin token: the default bypass actor is not a platform admin.
    response = client.get("/admin/inference/registry")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_access_denied"


def test_http_admin_token_reaches_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    client = _http_app(tmp_path)
    response = client.get(
        "/admin/inference/registry", headers={"x-motionly-admin-token": "admin-token"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["database_enabled"] is True


def test_http_disabled_without_flag_is_503(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    client = _http_app(tmp_path, enabled=False)
    response = client.get(
        "/admin/inference/registry", headers={"x-motionly-admin-token": "admin-token"}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "inference_registry_disabled"


def test_http_secret_create_never_echoes_value(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    client = _http_app(tmp_path)
    response = client.post(
        "/admin/inference/secrets",
        headers={"x-motionly-admin-token": "admin-token"},
        json={"name": "db-chat", "scope": "global", "value": "top-secret-xyz"},
    )
    assert response.status_code == 200, response.text
    assert "top-secret-xyz" not in response.text
    assert response.json()["fingerprint"]
