"""Tests for ``/capabilities`` and registry-driven provider health (plan Task 8).

Covers the capability matrix (empty registry, valid routes, invalid/dropped
routes, GPU-disabled routes, circuit-open routes), the two acceptance criteria
(an optional capability with no route is ``unavailable``/``not_configured``; an
open circuit degrades without making the API unhealthy), information-disclosure
guards (no secret, base URL, model id or provider label in ``/capabilities``),
adapter-declared health probes with fake responses (ok, 5xx, timeout, auth) via
``httpx.MockTransport``, the ``submit_unknown`` count in ``/health/dependencies``,
the authenticated-diagnostics guard, and the OpenAPI contract.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi.routing import APIRoute

from app.api.capabilities import router as capabilities_router
from app.api.dependencies import get_actor
from app.api.health import (
    _inference_health,
    _probe_providers,
)
from app.api.health import (
    router as health_router,
)
from app.core.config import get_settings
from app.services.inference_capability_report import build_capability_report
from app.services.inference_operations import OperationState
from app.services.inference_runtime import build_inference_runtime

ROOT = Path(__file__).resolve().parents[3]

_BUNDLE = {
    "chat-endpoint": "chat-secret",
    "stt-endpoint": "stt-secret",
    "objects-endpoint": "objects-secret",
    "remote-endpoint": "remote-secret",
}


# ---------------------------------------------------------------------------
# Registry + runtime helpers
# ---------------------------------------------------------------------------
def _bundle_file(tmp_path: Path) -> Path:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(_BUNDLE), encoding="utf-8")
    return path


def _settings(tmp_path: Path, *, app_env: str = "local", **updates):
    values = {
        "app_env": app_env,
        "ai_secrets_bundle_file": str(_bundle_file(tmp_path)),
        "redis_url": None,
        "supabase_url": None,
        "supabase_service_role_key": None,
    }
    values.update(updates)
    return get_settings().model_copy(update=values)


def _write(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _runtime(tmp_path: Path, document: dict | None, *, app_env="local", **updates):
    if document is None:
        settings = _settings(tmp_path, app_env=app_env, **updates)
        return build_inference_runtime(settings), settings
    path = _write(tmp_path, document)
    settings = _settings(
        tmp_path, app_env=app_env, ai_registry_file=str(path), **updates
    )
    return build_inference_runtime(settings), settings


def _remote_adapter(adapter_id: str, *, health=True, data_processor=True) -> dict:
    adapter: dict = {
        "id": adapter_id,
        "type": "openai_compatible",
        "base_url": f"https://{adapter_id}.example.invalid/v1",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": f"bundle:{adapter_id}"},
    }
    if health:
        adapter["health"] = {"method": "GET", "path": "/models"}
    if data_processor:
        adapter["data_processor"] = {
            "display_name": "Operator endpoint",
            "purpose": "Inference",
            "data_categories": ["text"],
            "policy_url": "https://operator.example.invalid/policy",
        }
    return adapter


def _planning_route(**overrides) -> dict:
    route = {
        "id": "planning-remote",
        "capability": "planning.chat",
        "adapter_id": "chat-endpoint",
        "model_id": "planning-model-x",
        "model_revision": "2026-01-01",
        "provider": "operator-label",
        "pricing": {
            "status": "priced",
            "unit": "tokens",
            "input_per_million_usd": 0.5,
            "output_per_million_usd": 1.0,
        },
        "budget": {"feature": "planning"},
    }
    route.update(overrides)
    return route


def _transcription_route(**overrides) -> dict:
    route = {
        "id": "transcription-remote",
        "capability": "transcription.speech_to_text",
        "adapter_id": "stt-endpoint",
        "model_id": "stt-model-x",
        "model_revision": "2026-01-01",
        "pricing": {"status": "priced", "per_request_usd": 0.25},
        "budget": {"feature": "transcription"},
    }
    route.update(overrides)
    return route


def _required_pair() -> dict:
    return {
        "version": 1,
        "adapters": [_remote_adapter("chat-endpoint"), _remote_adapter("stt-endpoint")],
        "models": [],
        "routes": [_planning_route(), _transcription_route()],
    }


def _report_statuses(runtime) -> dict[str, tuple[str, str | None]]:
    report = build_capability_report(runtime)
    return {c.capability: (c.status, c.reason) for c in report.capabilities}


# ---------------------------------------------------------------------------
# Capability matrix
# ---------------------------------------------------------------------------
def test_empty_registry_reports_all_capabilities_unavailable(tmp_path: Path) -> None:
    # Task 10: with no AI_REGISTRY_FILE, a dev env now serves the built-in fake
    # planning/transcription routes, so "no routes at all" is exercised with an
    # explicit empty registry document instead.
    runtime, _ = _runtime(tmp_path, {"version": 1, "adapters": [], "models": [], "routes": []})
    statuses = _report_statuses(runtime)
    # All 14 capabilities present and unavailable.
    assert len(statuses) == 14
    assert all(status == "unavailable" for status, _ in statuses.values())
    # Required ones report not_configured, not an error.
    assert statuses["planning.chat"] == ("unavailable", "not_configured")
    assert statuses["transcription.speech_to_text"] == ("unavailable", "not_configured")


def test_dev_without_registry_serves_the_fake_planning_and_transcription_routes(
    tmp_path: Path,
) -> None:
    # Task 10 dev fake registry: create_app works out of the box with no
    # AI_REGISTRY_FILE, serving fake planning and transcription routes.
    runtime, _ = _runtime(tmp_path, None)
    statuses = _report_statuses(runtime)
    assert statuses["planning.chat"] == ("available", None)
    assert statuses["transcription.speech_to_text"] == ("available", None)


def test_valid_routes_report_available(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, _required_pair())
    statuses = _report_statuses(runtime)
    assert statuses["planning.chat"] == ("available", None)
    assert statuses["transcription.speech_to_text"] == ("available", None)
    report = build_capability_report(runtime)
    planning = report.by_id("planning.chat")
    assert planning.route_count == 1
    assert planning.required is True
    assert planning.compute_requirement == "any"


def test_optional_capability_with_no_route_is_unavailable_not_configured(
    tmp_path: Path,
) -> None:
    # Acceptance: an optional capability with no configured route reports
    # unavailable with a machine-readable reason.
    runtime, _ = _runtime(tmp_path, _required_pair())
    statuses = _report_statuses(runtime)
    assert statuses["vision.object_detection"] == ("unavailable", "not_configured")
    assert statuses["audio.diarization"] == ("unavailable", "not_configured")


def test_tier2_capabilities_report_unavailable_not_configured_until_a_route_exists(
    tmp_path: Path,
) -> None:
    # Task 19: captions, voice emotion and text/image embeddings lose their
    # vendor wiring in Tier 1 and are rebuilt as capability routes in Tier 2
    # (Tasks 11/12). With only the required planning/transcription routes, each
    # reports unavailable/not_configured without a startup error.
    runtime, _ = _runtime(tmp_path, _required_pair())
    statuses = _report_statuses(runtime)
    for capability in (
        "vision.caption",
        "audio.emotion",
        "embeddings.text",
        "embeddings.image",
    ):
        assert statuses[capability] == ("unavailable", "not_configured"), capability


def test_invalid_optional_route_is_dropped_and_capability_unavailable(
    tmp_path: Path,
) -> None:
    document = _required_pair()
    # An object-detection route whose revision equals the model id is an error,
    # so the route is dropped and the capability is unavailable.
    document["adapters"].append(
        {
            "id": "objects-endpoint",
            "type": "generic_http",
            "base_url": "https://objects-endpoint.example.invalid/v1",
            "network": "public",
            "auth": {"type": "bearer", "secret_ref": "bundle:objects-endpoint"},
            "data_processor": {
                "display_name": "Operator endpoint",
                "purpose": "Inference",
                "data_categories": ["images"],
                "policy_url": "https://operator.example.invalid/policy",
            },
        }
    )
    document["routes"].append(
        {
            "id": "objects-remote",
            "capability": "vision.object_detection",
            "adapter_id": "objects-endpoint",
            "model_id": "same-id",
            "model_revision": "same-id",  # error: revision equals model id
            "pricing": {"status": "priced", "per_request_usd": 0.1},
            "mapping": {
                "request": {"method": "POST", "path": "/detect", "body": {}},
                "items": "response.results",
                "fields": {"label": "name", "score": "score", "box": "box"},
                "box_format": "xywh_norm",
            },
        }
    )
    runtime, _ = _runtime(tmp_path, document)
    statuses = _report_statuses(runtime)
    status, reason = statuses["vision.object_detection"]
    assert status == "unavailable"
    assert reason == "all_routes_invalid"


def test_gpu_disabled_route_reports_gpu_disabled(tmp_path: Path) -> None:
    document = _required_pair()
    # A local GPU route for captions, dropped because GPU workers are off.
    document["adapters"].append({"id": "gpu-local", "type": "local", "network": "private"})
    document["models"].append(
        {
            "id": "caption-model",
            "model_id": "local-caption",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        }
    )
    document["routes"].append(
        {
            "id": "caption-gpu",
            "capability": "vision.caption",
            "adapter_id": "gpu-local",
            "model_id": "local-caption",
            "model_ref": "caption-model",
            "model_revision": "2026-01-01",
            "requires_gpu": True,
            "compute_requirement": "gpu",
            "pricing": {"status": "free"},
            "explicitly_free": True,
            "params": {"runner": "caption_runner"},
        }
    )
    runtime, _ = _runtime(tmp_path, document, gpu_workers_enabled=False)
    statuses = _report_statuses(runtime)
    status, reason = statuses["vision.caption"]
    assert status == "unavailable"
    assert reason == "gpu_disabled"


# ---------------------------------------------------------------------------
# Circuit-open behaviour (acceptance criterion)
# ---------------------------------------------------------------------------
def _trip_circuit(runtime, route_id: str) -> None:
    route = next(r for r in runtime.gateway.all_routes if r.route_id == route_id)
    store = runtime.gateway._circuit_store
    for _ in range(10):
        store.record_failure(route.circuit_key, retryable=True)
    assert store.state(route.circuit_key) == "open"


def test_single_route_open_circuit_reports_degraded(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, _required_pair())
    _trip_circuit(runtime, "planning-remote")
    statuses = _report_statuses(runtime)
    assert statuses["planning.chat"] == ("degraded", "circuit_open")
    # The other required capability stays available.
    assert statuses["transcription.speech_to_text"] == ("available", None)


def test_open_circuit_keeps_health_dependencies_ok(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, _required_pair())
    _trip_circuit(runtime, "planning-remote")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(inference_runtime=runtime))
    )
    health = _inference_health(request)
    # An open circuit never makes the API unhealthy.
    assert health.status == "ok"
    assert health.metadata["circuit_state"]["planning-remote"] == "open"
    assert health.metadata["capabilities"]["planning.chat"]["status"] == "degraded"


def test_one_open_route_among_many_stays_available(tmp_path: Path) -> None:
    document = _required_pair()
    # A second planning route (lower priority). One open circuit must not degrade
    # the capability while the other route is healthy.
    document["adapters"].append(_remote_adapter("remote-endpoint"))
    document["routes"].append(
        _planning_route(
            id="planning-remote-2",
            adapter_id="remote-endpoint",
            priority=200,
        )
    )
    runtime, _ = _runtime(tmp_path, document)
    _trip_circuit(runtime, "planning-remote")
    statuses = _report_statuses(runtime)
    assert statuses["planning.chat"] == ("available", None)


# ---------------------------------------------------------------------------
# Information disclosure guards via the live endpoint
# ---------------------------------------------------------------------------
def test_capabilities_endpoint_leaks_no_secret_base_url_model_or_provider(
    tmp_path: Path,
) -> None:
    runtime, _ = _runtime(tmp_path, _required_pair())
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(inference_runtime=runtime))
    )
    from app.api.capabilities import capabilities

    response = asyncio.run(capabilities(request))
    blob = json.dumps(response.model_dump())
    # No secret value, base URL host, model id or provider label.
    for forbidden in (
        "chat-secret",
        "stt-secret",
        "example.invalid",
        "chat-endpoint",
        "stt-endpoint",
        "planning-model-x",
        "stt-model-x",
        "operator-label",
        "2026-01-01",
    ):
        assert forbidden not in blob, forbidden
    # It does carry capability ids and the registry version.
    assert "planning.chat" in blob
    assert response.registry_version.startswith("sha256:")


def test_capabilities_endpoint_returns_all_capability_rows(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, _required_pair())
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(inference_runtime=runtime))
    )
    from app.api.capabilities import capabilities

    response = asyncio.run(capabilities(request))
    ids = {row.id for row in response.capabilities}
    assert "planning.chat" in ids and "video.generation.submit" in ids
    assert len(response.capabilities) == 14
    submit = next(r for r in response.capabilities if r.id == "video.generation.submit")
    assert submit.is_async is True


# ---------------------------------------------------------------------------
# Adapter-declared health probes via MockTransport (no network)
# ---------------------------------------------------------------------------
def _runtime_with_health_adapter(tmp_path: Path):
    runtime, settings = _runtime(tmp_path, _required_pair())
    return runtime, settings


def _probe_with_responses(tmp_path: Path, handler):
    runtime, settings = _runtime_with_health_adapter(tmp_path)
    transport = httpx.MockTransport(handler)
    return asyncio.run(_probe_providers(settings, runtime, transport=transport))


def test_health_probe_ok(tmp_path: Path) -> None:
    def handler(_request):
        return httpx.Response(200, json={"ok": True})

    result = _probe_with_responses(tmp_path, handler)
    assert result.status == "ok"
    assert result.metadata["chat-endpoint"] == 200


def test_health_probe_5xx_is_down(tmp_path: Path) -> None:
    def handler(_request):
        return httpx.Response(503)

    result = _probe_with_responses(tmp_path, handler)
    assert result.status == "down"
    assert result.metadata["chat-endpoint"] == 503


def test_health_probe_timeout_is_down_without_leaking(tmp_path: Path) -> None:
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    result = _probe_with_responses(tmp_path, handler)
    assert result.status == "down"
    assert result.metadata["chat-endpoint"] == "unreachable"


def test_health_probe_auth_failure_is_down(tmp_path: Path) -> None:
    def handler(_request):
        return httpx.Response(401)

    result = _probe_with_responses(tmp_path, handler)
    assert result.status == "down"
    assert result.metadata["chat-endpoint"] == 401


def test_health_probe_without_health_spec_reports_not_probed(tmp_path: Path) -> None:
    document = _required_pair()
    document["adapters"] = [
        _remote_adapter("chat-endpoint", health=False),
        _remote_adapter("stt-endpoint", health=False),
    ]
    runtime, settings = _runtime(tmp_path, document)

    def handler(_request):
        raise AssertionError("an adapter without a health spec must not be probed")

    transport = httpx.MockTransport(handler)
    result = asyncio.run(_probe_providers(settings, runtime, transport=transport))
    assert result.metadata["chat-endpoint"] == "not_probed"
    assert result.status == "ok"


def test_health_probe_sends_auth_header_but_report_hides_the_secret(
    tmp_path: Path,
) -> None:
    captured: list[str] = []

    def handler(request):
        captured.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={})

    runtime, settings = _runtime(tmp_path, _required_pair())
    transport = httpx.MockTransport(handler)
    result = asyncio.run(_probe_providers(settings, runtime, transport=transport))
    # Each adapter's bearer secret is applied to its outgoing probe...
    assert "Bearer chat-secret" in captured
    assert "Bearer stt-secret" in captured
    # ...but no secret appears in the returned diagnostics metadata.
    blob = json.dumps(result.metadata)
    assert "chat-secret" not in blob
    assert "stt-secret" not in blob


# ---------------------------------------------------------------------------
# submit_unknown count in /health/dependencies
# ---------------------------------------------------------------------------
def test_inference_health_reports_submit_unknown_count(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, _required_pair())
    repo = runtime.operations_repository
    snapshot = {
        "adapter_id": "chat-endpoint",
        "adapter_type": "async_job",
        "base_url": "https://chat-endpoint.example.invalid/v1",
        "model": "m",
        "model_revision": "2026-01-01",
        "mapping_version": "v1",
    }
    op, _created = repo.reserve(
        workspace_id=uuid4(),
        project_id=None,
        capability="video.generation.submit",
        idempotency_key="k1",
        route_id="planning-remote",
        model="m",
        model_revision="2026-01-01",
        route_snapshot=snapshot,
    )
    repo.transition(op.id, to_state=OperationState.SUBMITTING, expected_from=None)
    repo.transition(op.id, to_state=OperationState.SUBMIT_UNKNOWN, expected_from=None)

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(inference_runtime=runtime))
    )
    health = _inference_health(request)
    assert health.metadata["submit_unknown"] == 1
    assert repo.count_by_state(OperationState.SUBMIT_UNKNOWN) == 1


def test_inference_health_reports_warnings_and_dropped_routes(tmp_path: Path) -> None:
    document = _required_pair()
    # A provider-managed remote planning route logs a warning but stays active.
    document["routes"][0] = _planning_route(
        model_revision="provider-managed", allow_provider_managed_revision=True
    )
    runtime, _ = _runtime(tmp_path, document)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(inference_runtime=runtime))
    )
    health = _inference_health(request)
    codes = {w["code"] for w in health.metadata["validation_warnings"]}
    assert "revision_provider_managed" in codes
    assert health.status == "ok"


# ---------------------------------------------------------------------------
# Routing / auth guards
# ---------------------------------------------------------------------------
def test_capabilities_route_is_public_and_registered() -> None:
    routes = [
        route
        for route in capabilities_router.routes
        if isinstance(route, APIRoute) and route.path == "/capabilities"
    ]
    assert len(routes) == 1
    # Public: no actor dependency.
    assert get_actor not in {
        dependency.call for dependency in routes[0].dependant.dependencies
    }


def test_health_dependencies_stays_authenticated() -> None:
    protected = [
        route
        for route in health_router.routes
        if isinstance(route, APIRoute) and route.path == "/health/dependencies"
    ]
    assert len(protected) == 1
    assert get_actor in {
        dependency.call for dependency in protected[0].dependant.dependencies
    }


def test_inference_health_without_runtime_is_ok() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    health = _inference_health(request)
    assert health.status == "ok"


# ---------------------------------------------------------------------------
# OpenAPI contract
# ---------------------------------------------------------------------------
def test_openapi_contract_includes_capabilities_route() -> None:
    contract = json.loads(
        (ROOT / "packages" / "shared" / "openapi.json").read_text(encoding="utf-8")
    )
    assert "/capabilities" in contract["paths"], (
        "regenerate the contract with scripts/generate_openapi_contract.py"
    )
    assert "get" in contract["paths"]["/capabilities"]
