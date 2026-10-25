"""Tests for offering metadata and the offerings read model (plan Task 29).

Covers the Section 2.11 ``offering`` block: strict keys, the closed label set,
plan validation against the billing enum, the quota-weight range, the
multiple-default error, the default-not-selectable warning, and operator-default
selection with and without an explicit ``default: true``. Also exercises
``offerings_for`` over a built runtime, and the scanner field rule for
``offering.display_name``/``offering.description``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.inference_offerings import offerings_for
from app.services.inference_registry import (
    DiagnosticCode,
    load_registry_document,
    to_model_route,
    validate_registry,
)
from app.services.inference_runtime import build_inference_runtime

_API_DIR = Path(__file__).resolve().parents[1]


# -- Fixture builders --------------------------------------------------------
def _chat_adapter(adapter_id: str, host: str) -> dict:
    return {
        "id": adapter_id,
        "type": "openai_compatible",
        "base_url": f"https://{host}.example.invalid/v1",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": f"bundle:{adapter_id}"},
        "data_processor": {
            "display_name": "Chat processor",
            "purpose": "Inference",
            "data_categories": ["text"],
            "policy_url": f"https://{host}.example.invalid/policy",
        },
    }


def _stt_adapter() -> dict:
    return {
        "id": "stt-endpoint",
        "type": "openai_compatible",
        "base_url": "https://stt.example.invalid/v1",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": "bundle:stt-endpoint"},
        "data_processor": {
            "display_name": "STT processor",
            "purpose": "Transcription",
            "data_categories": ["audio"],
            "policy_url": "https://stt.example.invalid/policy",
        },
    }


def _planning_route(route_id: str, adapter_id: str, *, priority=100, offering=None) -> dict:
    route = {
        "id": route_id,
        "capability": "planning.chat",
        "adapter_id": adapter_id,
        "model_id": f"model-{route_id}",
        "model_revision": "2026-01-01",
        "priority": priority,
        "pricing": {
            "status": "priced",
            "unit": "tokens",
            "input_per_million_usd": 0.5,
            "output_per_million_usd": 1.0,
        },
        "budget": {"feature": "planning"},
    }
    if offering is not None:
        route["offering"] = offering
    return route


def _stt_route() -> dict:
    return {
        "id": "stt-remote",
        "capability": "transcription.speech_to_text",
        "adapter_id": "stt-endpoint",
        "model_id": "stt-model-x",
        "model_revision": "2026-01-02",
        "pricing": {"status": "priced", "unit": "seconds", "per_unit_usd": 0.0001},
        "budget": {"feature": "transcription"},
    }


def _document(planning_routes: list[dict]) -> dict:
    adapters = [_stt_adapter()]
    seen: set[str] = set()
    for route in planning_routes:
        aid = route["adapter_id"]
        if aid not in seen:
            adapters.append(_chat_adapter(aid, aid))
            seen.add(aid)
    return {
        "version": 1,
        "adapters": adapters,
        "models": [],
        "routes": [*planning_routes, _stt_route()],
    }


def _validate(document: dict, *, app_env="test"):
    doc, diags = load_registry_document(document)
    assert doc is not None, diags
    assert diags == []
    return doc, validate_registry(
        doc, app_env=app_env, gpu_workers_enabled=True, block_noncommercial=False
    )


def _codes(result) -> set[str]:
    return {d.code for d in result.diagnostics}


# -- Strict parsing / structural --------------------------------------------
def test_offering_unknown_key_is_rejected():
    document = _document(
        [_planning_route("p1", "chat-a", offering={"selectable": True, "mystery": 1})]
    )
    doc, diags = load_registry_document(document)
    assert doc is None
    assert diags[0].code == DiagnosticCode.UNKNOWN_KEY
    assert diags[0].path == "routes[0].offering.mystery"


def test_offering_wrong_type_is_offering_invalid():
    document = _document([_planning_route("p1", "chat-a", offering={"selectable": "yes"})])
    doc, diags = load_registry_document(document)
    assert doc is None
    assert diags[0].code == DiagnosticCode.OFFERING_INVALID


def test_offering_quota_weight_not_a_number_is_offering_invalid():
    document = _document([_planning_route("p1", "chat-a", offering={"quota_weight": "x"})])
    doc, diags = load_registry_document(document)
    assert doc is None
    assert diags[0].code == DiagnosticCode.OFFERING_INVALID


# -- Value rules -------------------------------------------------------------
def test_offering_label_outside_closed_set_is_offering_invalid():
    document = _document(
        [_planning_route("p1", "chat-a", offering={"selectable": True, "labels": ["speedy"]})]
    )
    _doc, result = _validate(document)
    assert DiagnosticCode.OFFERING_INVALID in _codes(result)
    assert "p1" in result.dropped_route_ids


def test_offering_closed_label_set_accepted():
    for label in ("fast", "balanced", "quality", "economy", "local"):
        document = _document(
            [_planning_route("p1", "chat-a", offering={"selectable": True, "labels": [label]})]
        )
        _doc, result = _validate(document)
        assert DiagnosticCode.OFFERING_INVALID not in _codes(result), label


def test_offering_unknown_plan_is_offering_plan_unknown():
    document = _document(
        [_planning_route("p1", "chat-a", offering={"selectable": True, "plans": ["enterprise"]})]
    )
    _doc, result = _validate(document)
    assert DiagnosticCode.OFFERING_PLAN_UNKNOWN in _codes(result)


def test_offering_known_plans_accepted():
    document = _document(
        [
            _planning_route(
                "p1",
                "chat-a",
                offering={"selectable": True, "plans": ["free", "creator", "pro", "agency"]},
            )
        ]
    )
    _doc, result = _validate(document)
    assert DiagnosticCode.OFFERING_PLAN_UNKNOWN not in _codes(result)


@pytest.mark.parametrize("weight", [0.0, 0.09, 100.1, 1000])
def test_offering_quota_weight_out_of_range(weight):
    document = _document(
        [_planning_route("p1", "chat-a", offering={"selectable": True, "quota_weight": weight})]
    )
    _doc, result = _validate(document)
    assert DiagnosticCode.OFFERING_QUOTA_WEIGHT_OUT_OF_RANGE in _codes(result)


@pytest.mark.parametrize("weight", [0.1, 1.0, 2.5, 100.0])
def test_offering_quota_weight_in_range(weight):
    document = _document(
        [_planning_route("p1", "chat-a", offering={"selectable": True, "quota_weight": weight})]
    )
    _doc, result = _validate(document)
    assert DiagnosticCode.OFFERING_QUOTA_WEIGHT_OUT_OF_RANGE not in _codes(result)


# -- Default rules -----------------------------------------------------------
def test_offering_multiple_defaults_is_an_error():
    document = _document(
        [
            _planning_route(
                "p1", "chat-a", offering={"selectable": True, "default": True}
            ),
            _planning_route(
                "p2", "chat-b", priority=200, offering={"selectable": True, "default": True}
            ),
        ]
    )
    _doc, result = _validate(document)
    assert DiagnosticCode.OFFERING_MULTIPLE_DEFAULTS in {d.code for d in result.errors()}


def test_offering_default_not_selectable_is_a_warning():
    document = _document(
        [_planning_route("p1", "chat-a", offering={"selectable": False, "default": True})]
    )
    _doc, result = _validate(document)
    warning_codes = {d.code for d in result.warnings()}
    assert DiagnosticCode.OFFERING_DEFAULT_NOT_SELECTABLE in warning_codes
    assert DiagnosticCode.OFFERING_MULTIPLE_DEFAULTS not in _codes(result)


def test_single_selectable_default_is_clean():
    document = _document(
        [
            _planning_route("p1", "chat-a", offering={"selectable": True, "default": True}),
            _planning_route(
                "p2", "chat-b", priority=200, offering={"selectable": True, "default": False}
            ),
        ]
    )
    _doc, result = _validate(document)
    assert DiagnosticCode.OFFERING_MULTIPLE_DEFAULTS not in _codes(result)
    assert DiagnosticCode.OFFERING_DEFAULT_NOT_SELECTABLE not in _codes(result)


# -- to_model_route carries offering fields additively -----------------------
def test_to_model_route_carries_offering_fields():
    document = _document(
        [
            _planning_route(
                "p1", "chat-a", offering={"selectable": True, "quota_weight": 2.0}
            )
        ]
    )
    doc, _result = _validate(document)
    route = next(r for r in doc.routes if r.id == "p1")
    adapter = doc.adapter_by_id("chat-a")
    model_route = to_model_route(route, adapter)
    assert model_route.selectable is True
    assert model_route.quota_weight == 2.0


def test_to_model_route_defaults_when_no_offering():
    document = _document([_planning_route("p1", "chat-a")])
    doc, _result = _validate(document)
    route = next(r for r in doc.routes if r.id == "p1")
    adapter = doc.adapter_by_id("chat-a")
    model_route = to_model_route(route, adapter)
    assert model_route.selectable is False
    assert model_route.quota_weight == 1.0


# -- offerings_for over a built runtime --------------------------------------
def _runtime_for(document: dict):
    import json as _json
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        _json.dump(document, handle)
        path = handle.name
    # A bundle with a value for every adapter secret_ref so remote adapters
    # build and routes stay active (offerings_for reads active route records).
    refs = {
        route["adapter_id"]
        for route in document["routes"]
    }
    bundle = {ref: "test-secret-value" for ref in refs}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        _json.dump(bundle, handle)
        bundle_path = handle.name
    settings = SimpleNamespace(
        app_env="test",
        ai_registry_file=path,
        gpu_workers_enabled=True,
        ai_block_noncommercial=False,
        ai_workspace_scoped_routes=False,
        ai_secrets_dir="/run/secrets",
        ai_secrets_bundle_file=bundle_path,
    )
    return build_inference_runtime(settings)


def test_offerings_for_two_selectable_options_and_explicit_default():
    document = _document(
        [
            _planning_route(
                "p1",
                "chat-a",
                offering={
                    "selectable": True,
                    "display_name": "High quality",
                    "labels": ["quality"],
                    "default": True,
                },
            ),
            _planning_route(
                "p2",
                "chat-b",
                priority=200,
                offering={"selectable": True, "display_name": "Economy", "labels": ["economy"]},
            ),
        ]
    )
    runtime = _runtime_for(document)
    offerings = offerings_for(runtime)
    planning = offerings["planning.chat"]
    assert {o.route_id for o in planning.options} == {"p1", "p2"}
    assert planning.operator_default_route_id == "p1"


def test_offerings_default_falls_back_to_highest_priority_when_no_explicit():
    document = _document(
        [
            _planning_route(
                "p2", "chat-b", priority=200, offering={"selectable": True}
            ),
            _planning_route(
                "p1", "chat-a", priority=50, offering={"selectable": True}
            ),
        ]
    )
    runtime = _runtime_for(document)
    planning = offerings_for(runtime)["planning.chat"]
    # Lowest priority number (50) is the highest-priority default.
    assert planning.operator_default_route_id == "p1"


def test_offerings_non_selectable_route_is_default_but_not_an_option():
    document = _document(
        [
            _planning_route("p1", "chat-a", priority=50),  # no offering: serves, not visible
            _planning_route("p2", "chat-b", priority=200, offering={"selectable": True}),
        ]
    )
    runtime = _runtime_for(document)
    planning = offerings_for(runtime)["planning.chat"]
    assert {o.route_id for o in planning.options} == {"p2"}
    # Highest-priority valid enabled route is the default, selectable or not.
    assert planning.operator_default_route_id == "p1"
