"""Tests for ``GET /public/data-processors`` (plan Task 9A, Section 2.6).

The endpoint returns one entry per distinct data processor (deduped by display
name + policy URL) of the *enabled remote* routes in the live registry. A
self-hosted route must not appear, and the response must carry only display
metadata — never a base URL, model id, route id, provider label or secret
(serialized and grepped here).

Acceptance (plan Task 9A): with a registry of two remote routes from two
different processors, the endpoint returns exactly those two.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.routing import APIRoute

from app.api.capabilities import public_data_processors
from app.api.capabilities import router as capabilities_router
from app.api.dependencies import get_actor
from app.core.config import get_settings
from app.services.inference_runtime import build_inference_runtime

# A bundle that resolves every secret ref used by the fixtures below.
_BUNDLE = {
    "chat-endpoint": "chat-secret",
    "stt-endpoint": "stt-secret",
    "objects-endpoint": "objects-secret",
    "local-matte": "unused",
}


def _bundle_file(tmp_path: Path) -> Path:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(_BUNDLE), encoding="utf-8")
    return path


def _settings(tmp_path: Path, registry: Path, *, app_env: str = "local", **updates):
    values = {
        "app_env": app_env,
        "ai_secrets_bundle_file": str(_bundle_file(tmp_path)),
        "ai_registry_file": str(registry),
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


def _runtime(tmp_path: Path, document: dict, *, app_env="local", **updates):
    registry = _write(tmp_path, document)
    settings = _settings(tmp_path, registry, app_env=app_env, **updates)
    return build_inference_runtime(settings)


def _remote_adapter(adapter_id: str, processor: dict, *, categories) -> dict:
    return {
        "id": adapter_id,
        "type": "openai_compatible",
        "base_url": f"https://{adapter_id}.example.invalid/v1",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": f"bundle:{adapter_id}"},
        "data_processor": {
            "display_name": processor["display_name"],
            "purpose": processor["purpose"],
            "data_categories": categories,
            "policy_url": processor["policy_url"],
        },
    }


_PROCESSOR_A = {
    "display_name": "Processor Alpha",
    "purpose": "Writes short scene descriptions",
    "policy_url": "https://alpha.example.invalid/privacy",
}
_PROCESSOR_B = {
    "display_name": "Processor Beta",
    "purpose": "Transcribes the speech track",
    "policy_url": "https://beta.example.invalid/privacy",
}


def _planning_route(**overrides) -> dict:
    route = {
        "id": "planning-remote",
        "capability": "planning.chat",
        "adapter_id": "chat-endpoint",
        "model_id": "planning-model-x",
        "model_revision": "2026-01-01",
        "provider": "operator-label-a",
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
        "provider": "operator-label-b",
        "pricing": {"status": "priced", "per_request_usd": 0.25},
        "budget": {"feature": "transcription"},
    }
    route.update(overrides)
    return route


def _two_processor_document() -> dict:
    """Two enabled remote routes from two distinct processors."""
    return {
        "version": 1,
        "adapters": [
            _remote_adapter("chat-endpoint", _PROCESSOR_A, categories=["images", "text"]),
            _remote_adapter("stt-endpoint", _PROCESSOR_B, categories=["audio"]),
        ],
        "models": [],
        "routes": [_planning_route(), _transcription_route()],
    }


def _call(runtime) -> dict:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(inference_runtime=runtime)))
    response = asyncio.run(public_data_processors(request))
    return response.model_dump()


# ---------------------------------------------------------------------------
# Acceptance: exactly the two configured processors
# ---------------------------------------------------------------------------
def test_two_remote_processors_appear_exactly_once(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _two_processor_document())
    payload = _call(runtime)
    processors = payload["processors"]
    assert len(processors) == 2
    names = sorted(p["display_name"] for p in processors)
    assert names == ["Processor Alpha", "Processor Beta"]
    # Full display metadata is present for each entry.
    alpha = next(p for p in processors if p["display_name"] == "Processor Alpha")
    assert alpha["purpose"] == _PROCESSOR_A["purpose"]
    assert alpha["policy_url"] == _PROCESSOR_A["policy_url"]
    assert alpha["data_categories"] == ["images", "text"]


def test_self_hosted_route_is_excluded(tmp_path: Path) -> None:
    document = _two_processor_document()
    # A self-hosted local matte route whose adapter also carries data-processor
    # metadata: it must not appear, because self-hosted routes are excluded.
    document["adapters"].append(
        {
            "id": "local-matte",
            "type": "local",
            "network": "private",
            "data_processor": {
                "display_name": "Should Not Appear",
                "purpose": "local only",
                "data_categories": ["video"],
                "policy_url": "https://local.example.invalid/privacy",
            },
        }
    )
    document["models"].append(
        {
            "id": "matte-model",
            "model_id": "local-matte-model",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        }
    )
    document["routes"].append(
        {
            "id": "matte-local",
            "capability": "matte.segmentation",
            "adapter_id": "local-matte",
            "model_id": "local-matte-model",
            "model_ref": "matte-model",
            "model_revision": "2026-01-01",
            "pricing": {"status": "free"},
            "explicitly_free": True,
            "compute_requirement": "cpu",
            "params": {"runner": "matte_runner"},
        }
    )
    runtime = _runtime(tmp_path, document)
    payload = _call(runtime)
    names = {p["display_name"] for p in payload["processors"]}
    assert "Should Not Appear" not in names
    assert names == {"Processor Alpha", "Processor Beta"}


def test_distinct_processors_deduped_by_name_and_policy(tmp_path: Path) -> None:
    document = _two_processor_document()
    # A third remote route whose adapter shares Processor Alpha's display name and
    # policy URL: it must collapse into the single Alpha entry.
    document["adapters"].append(
        _remote_adapter("objects-endpoint", _PROCESSOR_A, categories=["images"])
    )
    document["routes"].append(
        {
            "id": "objects-remote",
            "capability": "vision.object_detection",
            "adapter_id": "objects-endpoint",
            "model_id": "objects-model-x",
            "model_revision": "2026-02-02",
            "provider": "operator-label-a",
            "pricing": {"status": "priced", "per_request_usd": 0.1},
            "mapping": {
                "request": {"method": "POST", "path": "/detect", "body": {}},
                "items": "response.results",
                "fields": {"label": "name", "score": "score", "box": "box"},
                "box_format": "xywh_norm",
            },
        }
    )
    runtime = _runtime(tmp_path, document)
    payload = _call(runtime)
    assert len(payload["processors"]) == 2


def test_disabled_remote_route_does_not_appear(tmp_path: Path) -> None:
    document = _two_processor_document()
    # Disable the transcription route's adapter processor via a disabled route by
    # adding a disabled third processor; a disabled route is not an enabled route.
    document["adapters"].append(
        _remote_adapter(
            "objects-endpoint",
            {
                "display_name": "Disabled Processor",
                "purpose": "never shown",
                "policy_url": "https://disabled.example.invalid/privacy",
            },
            categories=["images"],
        )
    )
    document["routes"].append(
        {
            "id": "objects-remote",
            "capability": "vision.object_detection",
            "adapter_id": "objects-endpoint",
            "model_id": "objects-model-x",
            "model_revision": "2026-02-02",
            "enabled": False,
            "pricing": {"status": "priced", "per_request_usd": 0.1},
            "mapping": {
                "request": {"method": "POST", "path": "/detect", "body": {}},
                "items": "response.results",
                "fields": {"label": "name", "score": "score", "box": "box"},
                "box_format": "xywh_norm",
            },
        }
    )
    runtime = _runtime(tmp_path, document)
    payload = _call(runtime)
    names = {p["display_name"] for p in payload["processors"]}
    assert "Disabled Processor" not in names


def test_response_leaks_no_url_model_or_secret(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _two_processor_document())
    payload = _call(runtime)
    blob = json.dumps(payload)
    # No base URL host, model id, route id, provider label or secret value.
    for forbidden in (
        "example.invalid/v1",
        "chat-endpoint",
        "stt-endpoint",
        "planning-model-x",
        "stt-model-x",
        "planning-remote",
        "transcription-remote",
        "operator-label-a",
        "operator-label-b",
        "chat-secret",
        "stt-secret",
    ):
        assert forbidden not in blob, forbidden
    # Only the operator-authored policy URLs (safe, public) appear.
    assert _PROCESSOR_A["policy_url"] in blob
    assert _PROCESSOR_B["policy_url"] in blob


def test_empty_registry_returns_no_processors(tmp_path: Path) -> None:
    # An empty (no-registry) runtime: no remote routes, so no processors.
    settings = get_settings().model_copy(
        update={
            "app_env": "local",
            "ai_secrets_bundle_file": str(_bundle_file(tmp_path)),
            "redis_url": None,
            "supabase_url": None,
            "supabase_service_role_key": None,
        }
    )
    runtime = build_inference_runtime(settings)
    payload = _call(runtime)
    assert payload["processors"] == []


# ---------------------------------------------------------------------------
# Routing / auth guard
# ---------------------------------------------------------------------------
def test_public_data_processors_route_is_public_and_registered() -> None:
    routes = [
        route
        for route in capabilities_router.routes
        if isinstance(route, APIRoute) and route.path == "/public/data-processors"
    ]
    assert len(routes) == 1
    assert get_actor not in {dependency.call for dependency in routes[0].dependant.dependencies}
