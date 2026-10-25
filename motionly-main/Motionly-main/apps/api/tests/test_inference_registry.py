"""Tests for the declarative inference registry (plan Task 4, Section 2.6).

These are table-driven over every Section 2.6 rule, with one valid and one
invalid case per rule, plus the loader (strict parsing, version rejection,
deterministic order), ``to_model_route`` conversion, the ModelRoute revision
rule, the example file, and the validator CLI exit codes via subprocess.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.inference_registry import (
    DIAGNOSTIC_CODES,
    AdapterRecord,
    Diagnostic,
    DiagnosticCode,
    is_self_hosted,
    load_registry_document,
    route_locality,
    to_model_route,
    validate_registry,
)
from app.services.model_gateway import ModelCapability, ModelOperation, ModelRoute

_API_DIR = Path(__file__).resolve().parents[1]
_EXAMPLE_FILE = _API_DIR.parent.parent / "docs" / "ai-registry-example.json"


# -- Fixture builders --------------------------------------------------------
def _remote_chat_adapter(**overrides) -> dict:
    adapter = {
        "id": "chat-endpoint",
        "type": "openai_compatible",
        "base_url": "https://chat.example.invalid/v1",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": "bundle:chat-endpoint"},
        "data_processor": {
            "display_name": "Chat processor",
            "purpose": "Inference",
            "data_categories": ["text"],
            "policy_url": "https://chat.example.invalid/policy",
        },
    }
    adapter.update(overrides)
    return adapter


def _remote_transcription_adapter(**overrides) -> dict:
    adapter = {
        "id": "stt-endpoint",
        "type": "openai_compatible",
        "base_url": "https://stt.example.invalid/v1",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": "file:stt-endpoint"},
        "data_processor": {
            "display_name": "STT processor",
            "purpose": "Transcription",
            "data_categories": ["audio"],
            "policy_url": "https://stt.example.invalid/policy",
        },
    }
    adapter.update(overrides)
    return adapter


def _planning_route(**overrides) -> dict:
    route = {
        "id": "planning-remote",
        "capability": "planning.chat",
        "adapter_id": "chat-endpoint",
        "model_id": "chat-model-x",
        "model_revision": "2026-01-01",
        "priority": 100,
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
        "id": "stt-remote",
        "capability": "transcription.speech_to_text",
        "adapter_id": "stt-endpoint",
        "model_id": "stt-model-x",
        "model_revision": "2026-01-02",
        "pricing": {"status": "priced", "unit": "seconds", "per_unit_usd": 0.0001},
        "budget": {"feature": "transcription"},
    }
    route.update(overrides)
    return route


def _base_document(**overrides) -> dict:
    """A document that validates cleanly in production (both required caps)."""
    document = {
        "version": 1,
        "adapters": [_remote_chat_adapter(), _remote_transcription_adapter()],
        "models": [],
        "routes": [_planning_route(), _transcription_route()],
    }
    document.update(overrides)
    return document


def _validate(document: dict, *, app_env="test", gpu=True, block_nc=False, **kw):
    doc, diags = load_registry_document(document)
    assert doc is not None, diags
    assert diags == []
    return validate_registry(
        doc,
        app_env=app_env,
        gpu_workers_enabled=gpu,
        block_noncommercial=block_nc,
        **kw,
    )


def _codes(diagnostics: tuple[Diagnostic, ...]) -> set[str]:
    return {d.code for d in diagnostics}


# -- Loader / structural -----------------------------------------------------
def test_base_document_is_clean_in_production():
    result = _validate(_base_document(), app_env="production")
    assert result.errors() == ()
    assert set(result.valid_route_ids) == {"planning-remote", "stt-remote"}


def test_version_rejection():
    doc, diags = load_registry_document({"version": 2, "adapters": [], "models": [], "routes": []})
    assert doc is None
    assert diags[0].code == DiagnosticCode.UNSUPPORTED_VERSION


def test_invalid_json_string():
    doc, diags = load_registry_document("{not json")
    assert doc is None
    assert diags[0].code == DiagnosticCode.INVALID_JSON


def test_unknown_top_level_key_rejected_with_path():
    doc, diags = load_registry_document(
        {"version": 1, "adapters": [], "models": [], "routes": [], "extra": 1}
    )
    assert doc is None
    assert diags[0].code == DiagnosticCode.UNKNOWN_KEY
    assert diags[0].path == "$.extra"


def test_unknown_adapter_key_rejected_with_path():
    document = _base_document()
    document["adapters"][0]["mystery"] = True
    doc, diags = load_registry_document(document)
    assert doc is None
    assert diags[0].code == DiagnosticCode.UNKNOWN_KEY
    assert "adapters[0].mystery" == diags[0].path


def test_unknown_route_key_rejected_with_path():
    document = _base_document()
    document["routes"][0]["mystery"] = True
    doc, diags = load_registry_document(document)
    assert doc is None
    assert diags[0].code == DiagnosticCode.UNKNOWN_KEY
    assert diags[0].path == "routes[0].mystery"


def test_reserved_adapter_type_rejected():
    document = _base_document()
    document["adapters"][0]["type"] = "agent_pool"
    doc, diags = load_registry_document(document)
    assert doc is None
    assert diags[0].code == DiagnosticCode.ADAPTER_TYPE_NOT_AVAILABLE


def test_legacy_capability_alias_rejected():
    document = _base_document()
    document["routes"].append(
        {
            "id": "caption-legacy",
            "capability": "vision.prompt",
            "adapter_id": "chat-endpoint",
            "model_id": "m",
            "model_revision": "2026-01-01",
        }
    )
    doc, diags = load_registry_document(document)
    assert doc is None
    assert diags[0].code == DiagnosticCode.CAPABILITY_LEGACY_ALIAS


def test_embeddings_input_style_accepted_on_openai_compatible_adapter():
    # Tier 2 (plan Task 12): ``embeddings_input_style`` is a closed adapter
    # option on openai_compatible/azure_openai adapters.
    document = _base_document()
    document["adapters"][0]["embeddings_input_style"] = "typed_objects"
    doc, diags = load_registry_document(document)
    assert doc is not None
    assert not [d for d in diags if d.is_error]
    assert doc.adapters[0].embeddings_input_style == "typed_objects"


def test_embeddings_input_style_rejects_unknown_value():
    document = _base_document()
    document["adapters"][0]["embeddings_input_style"] = "nonstandard"
    doc, diags = load_registry_document(document)
    assert doc is None
    assert diags[0].code == DiagnosticCode.OPTION_NOT_AVAILABLE


def test_deterministic_load_preserves_order():
    document = _base_document()
    doc1, _ = load_registry_document(copy.deepcopy(document))
    doc2, _ = load_registry_document(copy.deepcopy(document))
    assert [a.id for a in doc1.adapters] == [a.id for a in doc2.adapters]
    assert [r.id for r in doc1.routes] == [r.id for r in doc2.routes]
    assert doc1.routes[0].id == "planning-remote"
    assert doc1.routes[1].id == "stt-remote"


def test_all_codes_registered():
    # Every code referenced in DiagnosticCode is in the exported constant.
    assert DiagnosticCode.REQUIRED_CAPABILITY_MISSING in DIAGNOSTIC_CODES
    assert len(DIAGNOSTIC_CODES) >= 40


# -- Duplicate ids and unknown references ------------------------------------
def test_duplicate_adapter_id():
    document = _base_document()
    document["adapters"].append(_remote_chat_adapter(id="chat-endpoint"))
    result = _validate(document)
    assert DiagnosticCode.DUPLICATE_ID in _codes(result.errors())


def test_duplicate_route_id():
    document = _base_document()
    document["routes"].append(_planning_route(id="planning-remote"))
    result = _validate(document)
    assert DiagnosticCode.DUPLICATE_ID in _codes(result.errors())


def test_unknown_adapter_reference():
    document = _base_document()
    document["routes"][0]["adapter_id"] = "nope"
    result = _validate(document)
    errors = _codes(result.errors())
    assert DiagnosticCode.UNKNOWN_REFERENCE in errors
    assert "planning-remote" in result.dropped_route_ids
    # planning required capability also missing because the route dropped.
    assert DiagnosticCode.REQUIRED_CAPABILITY_MISSING in errors


def test_unknown_model_reference():
    document = _base_document()
    document["routes"][0]["model_ref"] = "ghost-model"
    result = _validate(document)
    assert DiagnosticCode.UNKNOWN_REFERENCE in _codes(result.errors())


# -- base_url rules ----------------------------------------------------------
def test_base_url_with_credentials():
    document = _base_document()
    document["adapters"][0]["base_url"] = "https://user:pw@chat.example.invalid/v1"
    result = _validate(document)
    assert DiagnosticCode.BASE_URL_HAS_CREDENTIALS in _codes(result.errors())


def test_base_url_with_query_string():
    document = _base_document()
    document["adapters"][0]["base_url"] = "https://chat.example.invalid/v1?token=x"
    result = _validate(document)
    assert DiagnosticCode.BASE_URL_HAS_QUERY in _codes(result.errors())


def test_http_base_url_rejected_on_public_network():
    document = _base_document()
    document["adapters"][0]["base_url"] = "http://chat.example.invalid/v1"
    result = _validate(document)
    assert DiagnosticCode.BASE_URL_SCHEME_INVALID in _codes(result.errors())


def test_public_adapter_private_ip_literal_rejected():
    document = _base_document()
    document["adapters"][0]["base_url"] = "https://10.0.0.5/v1"
    result = _validate(document)
    assert DiagnosticCode.BASE_URL_PUBLIC_IP_PRIVATE_NETWORK in _codes(result.errors())


def test_private_adapter_public_ip_literal_rejected():
    # A private-network adapter with a public IP literal is an error.
    document = _base_document()
    document["adapters"][0]["network"] = "private"
    document["adapters"][0]["base_url"] = "https://8.8.8.8/v1"
    # private self-hosted route: auth none allowed, no data_processor needed.
    document["adapters"][0]["auth"] = {"type": "none"}
    result = _validate(document)
    assert DiagnosticCode.BASE_URL_PRIVATE_IP_PUBLIC_NETWORK in _codes(result.errors())


def test_base_url_required_for_openai_compatible():
    document = _base_document()
    document["adapters"][0].pop("base_url")
    result = _validate(document)
    assert DiagnosticCode.BASE_URL_REQUIRED in _codes(result.errors())


# -- secret refs -------------------------------------------------------------
def test_db_secret_ref_rejected():
    document = _base_document()
    document["adapters"][0]["auth"] = {"type": "bearer", "secret_ref": "db:x"}
    result = _validate(document)
    assert DiagnosticCode.SECRET_REF_NOT_AVAILABLE in _codes(result.errors())


def test_env_secret_ref_rejected_in_production():
    document = _base_document()
    document["adapters"][0]["auth"] = {"type": "bearer", "secret_ref": "env:X"}
    result = _validate(document, app_env="production")
    assert DiagnosticCode.SECRET_REF_ENV_REJECTED in _codes(result.errors())


def test_env_secret_ref_warns_outside_production():
    document = _base_document()
    document["adapters"][0]["auth"] = {"type": "bearer", "secret_ref": "env:X"}
    result = _validate(document, app_env="test")
    assert DiagnosticCode.SECRET_REF_ENV_WARNING in _codes(result.warnings())


# -- auth --------------------------------------------------------------------
def test_remote_route_requires_auth():
    document = _base_document()
    document["adapters"][0]["auth"] = {"type": "none"}
    result = _validate(document)
    assert DiagnosticCode.AUTH_NONE_REMOTE in _codes(result.errors())


def test_remote_route_requires_secret_ref():
    document = _base_document()
    document["adapters"][0]["auth"] = {"type": "bearer"}
    result = _validate(document)
    assert DiagnosticCode.AUTH_REQUIRED in _codes(result.errors())


def test_custom_header_requires_header_name():
    document = _base_document()
    document["adapters"][0]["auth"] = {
        "type": "custom_header",
        "secret_ref": "file:x",
    }
    result = _validate(document)
    assert DiagnosticCode.AUTH_MISSING_HEADER_NAME in _codes(result.errors())


def test_self_hosted_allows_auth_none():
    # A private local route with auth none is valid.
    document = {
        "version": 1,
        "adapters": [
            _remote_chat_adapter(),
            _remote_transcription_adapter(),
            {
                "id": "local-stems",
                "type": "local",
                "auth": {"type": "none"},
            },
        ],
        "models": [
            {
                "id": "demucs",
                "model_id": "demucs-htdemucs",
                "revision": "sha256:" + "a" * 64,
                "licence": "MIT",
                "commercial_use": True,
            }
        ],
        "routes": [
            _planning_route(),
            _transcription_route(),
            {
                "id": "stems-local",
                "capability": "audio.stem_separation",
                "adapter_id": "local-stems",
                "model_id": "demucs-htdemucs",
                "model_ref": "demucs",
                "model_revision": "sha256:" + "a" * 64,
                "explicitly_free": True,
                "pricing": {"status": "free"},
            },
        ],
    }
    result = _validate(document)
    assert result.errors() == ()
    assert "stems-local" in result.valid_route_ids


# -- revision policy ---------------------------------------------------------
def test_provider_managed_without_flag_is_error():
    document = _base_document()
    document["routes"][0]["model_revision"] = "provider-managed"
    result = _validate(document)
    assert DiagnosticCode.REVISION_PROVIDER_MANAGED_NOT_ALLOWED in _codes(result.errors())


def test_provider_managed_with_flag_is_warning():
    document = _base_document()
    document["routes"][0]["model_revision"] = "provider-managed"
    document["routes"][0]["allow_provider_managed_revision"] = True
    result = _validate(document)
    assert result.errors() == ()
    assert DiagnosticCode.REVISION_PROVIDER_MANAGED in _codes(result.warnings())


def test_latest_revision_is_error():
    document = _base_document()
    document["routes"][0]["model_revision"] = "latest"
    result = _validate(document)
    assert DiagnosticCode.REVISION_FLOATING in _codes(result.errors())


def test_revision_equal_to_model_id_is_error():
    document = _base_document()
    document["routes"][0]["model_id"] = "chat-model-x"
    document["routes"][0]["model_revision"] = "chat-model-x"
    result = _validate(document)
    assert DiagnosticCode.REVISION_EQUALS_MODEL_ID in _codes(result.errors())


def test_provider_managed_on_self_hosted_is_error():
    document = _base_document()
    document["adapters"][0]["network"] = "private"
    document["adapters"][0]["base_url"] = "https://internal.example.invalid/v1"
    document["adapters"][0]["auth"] = {"type": "none"}
    document["routes"][0]["model_revision"] = "provider-managed"
    document["routes"][0]["allow_provider_managed_revision"] = True
    result = _validate(document)
    assert DiagnosticCode.REVISION_PROVIDER_MANAGED_SELF_HOSTED in _codes(result.errors())


# -- licence -----------------------------------------------------------------
def test_self_hosted_without_licence_is_error():
    document = _base_document()
    document["adapters"][0]["network"] = "private"
    document["adapters"][0]["base_url"] = "https://internal.example.invalid/v1"
    document["adapters"][0]["auth"] = {"type": "none"}
    document["models"].append(
        {"id": "m1", "model_id": "chat-model-x", "revision": "2026-01-01"}
    )
    document["routes"][0]["model_ref"] = "m1"
    result = _validate(document)
    assert DiagnosticCode.LICENCE_REQUIRED in _codes(result.errors())


def test_remote_without_licence_is_warning():
    document = _base_document()
    document["models"].append(
        {"id": "m1", "model_id": "chat-model-x", "revision": "2026-01-01"}
    )
    document["routes"][0]["model_ref"] = "m1"
    result = _validate(document)
    assert DiagnosticCode.LICENCE_MISSING in _codes(result.warnings())


def test_noncommercial_licence_warns_by_default():
    document = _base_document()
    document["models"].append(
        {
            "id": "m1",
            "model_id": "chat-model-x",
            "revision": "2026-01-01",
            "licence": "CC-BY-NC-4.0",
            "commercial_use": False,
        }
    )
    document["routes"][0]["model_ref"] = "m1"
    result = _validate(document)
    assert DiagnosticCode.LICENCE_NONCOMMERCIAL in _codes(result.warnings())


def test_noncommercial_licence_errors_when_blocked():
    document = _base_document()
    document["models"].append(
        {
            "id": "m1",
            "model_id": "chat-model-x",
            "revision": "2026-01-01",
            "licence": "CC-BY-NC-4.0",
            "commercial_use": False,
        }
    )
    document["routes"][0]["model_ref"] = "m1"
    result = _validate(document, block_nc=True)
    assert DiagnosticCode.LICENCE_NONCOMMERCIAL in _codes(result.errors())


# -- data processor ----------------------------------------------------------
def test_data_processor_missing_warns_in_dev():
    document = _base_document()
    document["adapters"][0].pop("data_processor")
    result = _validate(document, app_env="test")
    assert DiagnosticCode.DATA_PROCESSOR_MISSING in _codes(result.warnings())


def test_data_processor_missing_errors_in_production():
    document = _base_document()
    document["adapters"][0].pop("data_processor")
    result = _validate(document, app_env="production")
    assert DiagnosticCode.DATA_PROCESSOR_MISSING in _codes(result.errors())


# -- pricing -----------------------------------------------------------------
def test_zero_price_without_explicitly_free_is_error():
    document = _base_document()
    document["routes"][0]["pricing"] = {
        "status": "priced",
        "input_per_million_usd": 0,
        "output_per_million_usd": 0,
    }
    result = _validate(document)
    assert DiagnosticCode.PRICING_ZERO_NOT_FREE in _codes(result.errors())


def test_unknown_pricing_without_hard_cap_is_error():
    document = _base_document()
    document["routes"][0]["pricing"] = {"status": "unknown"}
    result = _validate(document)
    assert DiagnosticCode.PRICING_UNKNOWN_NEEDS_HARD_CAP in _codes(result.errors())


def test_unknown_pricing_with_hard_cap_is_valid():
    document = _base_document()
    document["routes"][0]["pricing"] = {"status": "unknown"}
    document["routes"][0]["budget"] = {
        "feature": "planning",
        "hard_cap": {"max_requests_per_month": 1000},
    }
    result = _validate(document)
    assert result.errors() == ()


def test_free_planning_remote_in_production_is_error():
    document = _base_document()
    document["routes"][0]["pricing"] = {"status": "free"}
    document["routes"][0]["explicitly_free"] = True
    result = _validate(document, app_env="production")
    assert DiagnosticCode.FREE_PLANNING_REMOTE in _codes(result.errors())


def test_free_planning_private_adapter_is_valid():
    document = _base_document()
    document["adapters"][0]["network"] = "private"
    document["adapters"][0]["base_url"] = "https://internal.example.invalid/v1"
    document["adapters"][0]["auth"] = {"type": "none"}
    document["models"].append(
        {
            "id": "m1",
            "model_id": "chat-model-x",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        }
    )
    document["routes"][0]["model_ref"] = "m1"
    document["routes"][0]["pricing"] = {"status": "free"}
    document["routes"][0]["explicitly_free"] = True
    result = _validate(document, app_env="production")
    assert result.errors() == ()


# -- fake adapter / gpu ------------------------------------------------------
def test_fake_adapter_in_production_is_error():
    document = _base_document()
    document["adapters"][0] = {"id": "chat-endpoint", "type": "fake"}
    result = _validate(document, app_env="production")
    assert DiagnosticCode.FAKE_ADAPTER_IN_PRODUCTION in _codes(result.errors())


def test_requires_gpu_route_dropped_when_gpu_disabled():
    document = _base_document()
    document["routes"][0]["requires_gpu"] = True
    result = _validate(document, gpu=False)
    assert DiagnosticCode.ROUTE_REQUIRES_GPU in _codes(result.warnings())


# -- support matrix / mapping ------------------------------------------------
def test_capability_adapter_unsupported():
    # openai_compatible cannot serve vision.object_detection.
    document = _base_document()
    document["routes"].append(
        {
            "id": "objects-bad",
            "capability": "vision.object_detection",
            "adapter_id": "chat-endpoint",
            "model_id": "m",
            "model_revision": "2026-01-01",
            "pricing": {"status": "priced", "per_request_usd": 0.01},
        }
    )
    result = _validate(document)
    assert DiagnosticCode.CAPABILITY_ADAPTER_UNSUPPORTED in _codes(result.errors())


def test_generic_http_requires_mapping():
    document = _base_document()
    document["adapters"].append(
        {
            "id": "objects-endpoint",
            "type": "generic_http",
            "base_url": "https://objects.example.invalid",
            "auth": {"type": "bearer", "secret_ref": "file:objects"},
            "data_processor": {
                "display_name": "Objects",
                "purpose": "detect",
                "data_categories": ["images"],
                "policy_url": "https://objects.example.invalid/policy",
            },
        }
    )
    document["routes"].append(
        {
            "id": "objects-remote",
            "capability": "vision.object_detection",
            "adapter_id": "objects-endpoint",
            "model_id": "det-model",
            "model_revision": "2026-01-01",
            "pricing": {"status": "priced", "per_request_usd": 0.01},
        }
    )
    result = _validate(document)
    assert DiagnosticCode.MAPPING_REQUIRED in _codes(result.errors())


def test_mapping_not_allowed_on_openai_compatible():
    document = _base_document()
    document["routes"][0]["mapping"] = {"request": {}}
    result = _validate(document)
    assert DiagnosticCode.MAPPING_NOT_ALLOWED in _codes(result.errors())


def test_injected_mapping_validator_runs():
    document = _base_document()
    document["adapters"].append(
        {
            "id": "objects-endpoint",
            "type": "generic_http",
            "base_url": "https://objects.example.invalid",
            "auth": {"type": "bearer", "secret_ref": "file:objects"},
            "data_processor": {
                "display_name": "Objects",
                "purpose": "detect",
                "data_categories": ["images"],
                "policy_url": "https://objects.example.invalid/policy",
            },
        }
    )
    document["routes"].append(
        {
            "id": "objects-remote",
            "capability": "vision.object_detection",
            "adapter_id": "objects-endpoint",
            "model_id": "det-model",
            "model_revision": "2026-01-01",
            "pricing": {"status": "priced", "per_request_usd": 0.01},
            "mapping": {"request": {"method": "POST", "path": "/detect"}},
        }
    )
    doc, _ = load_registry_document(document)

    def validator(adapter_type, mapping):
        assert adapter_type == "generic_http"
        return ["bad key foo"]

    result = validate_registry(
        doc,
        app_env="test",
        gpu_workers_enabled=True,
        block_noncommercial=False,
        mapping_validator=validator,
    )
    assert DiagnosticCode.MAPPING_INVALID in _codes(result.errors())


# -- embedding space ---------------------------------------------------------
def test_embedding_space_mismatch_is_error():
    document = _base_document()
    for cap, rid, space in (
        ("embeddings.text", "emb-text", "space-a"),
        ("embeddings.image", "emb-image", "space-b"),
    ):
        document["routes"].append(
            {
                "id": rid,
                "capability": cap,
                "adapter_id": "chat-endpoint",
                "model_id": "emb-model",
                "model_revision": "2026-01-01",
                "pricing": {"status": "priced", "per_request_usd": 0.01},
                "embedding_space": space,
            }
        )
    result = _validate(document)
    assert DiagnosticCode.EMBEDDING_SPACE_MISMATCH in _codes(result.errors())


def test_embedding_space_match_is_valid():
    document = _base_document()
    for cap, rid in (("embeddings.text", "emb-text"), ("embeddings.image", "emb-image")):
        document["routes"].append(
            {
                "id": rid,
                "capability": cap,
                "adapter_id": "chat-endpoint",
                "model_id": "emb-model",
                "model_revision": "2026-01-01",
                "pricing": {"status": "priced", "per_request_usd": 0.01},
                "embedding_space": "shared-space",
            }
        )
    result = _validate(document)
    assert DiagnosticCode.EMBEDDING_SPACE_MISMATCH not in _codes(result.errors())


# -- scope -------------------------------------------------------------------
def test_workspace_scope_rejected_when_flag_off():
    document = _base_document()
    document["routes"][0]["scope"] = "workspace:abc"
    result = _validate(document)
    assert DiagnosticCode.SCOPE_WORKSPACE_DISABLED in _codes(result.errors())


def test_workspace_scope_allowed_when_flag_on():
    document = _base_document()
    document["routes"][0]["scope"] = "workspace:abc"
    result = _validate(document, workspace_scoped_routes_enabled=True)
    assert DiagnosticCode.SCOPE_WORKSPACE_DISABLED not in _codes(result.errors())


# -- required capability: invalid optional dropped, required still reported --
def test_invalid_optional_route_dropped_without_hiding_required_error():
    document = _base_document()
    # Break the required planning route (floating revision) -> drops + required
    # missing. Add an invalid OPTIONAL route; it drops with its own error.
    document["routes"][0]["model_revision"] = "latest"
    document["routes"].append(
        {
            "id": "caption-bad",
            "capability": "vision.caption",
            "adapter_id": "chat-endpoint",
            "model_id": "cap-model",
            "model_revision": "latest",
            "pricing": {"status": "priced", "per_request_usd": 0.01},
        }
    )
    result = _validate(document)
    errors = _codes(result.errors())
    assert DiagnosticCode.REQUIRED_CAPABILITY_MISSING in errors
    assert DiagnosticCode.REVISION_FLOATING in errors
    assert "planning-remote" in result.dropped_route_ids
    assert "caption-bad" in result.dropped_route_ids


# -- to_model_route ----------------------------------------------------------
def test_to_model_route_maps_capability_operation_costs_host():
    doc, _ = load_registry_document(_base_document())
    route = doc.routes[0]
    adapter = doc.adapter_by_id(route.adapter_id)
    mr = to_model_route(route, adapter)
    assert isinstance(mr, ModelRoute)
    assert mr.capability == ModelCapability.PLANNING
    assert mr.operations == (ModelOperation.PLANNING_CHAT,)
    assert mr.input_cost_per_million_usd == 0.5
    assert mr.output_cost_per_million_usd == 1.0
    assert mr.endpoint_host == "chat.example.invalid"
    assert mr.requires_live_effects_gate is True
    assert mr.capability_id == "planning.chat"
    assert mr.budget_feature == "planning"


def test_to_model_route_per_unit_and_per_request_costs():
    doc, _ = load_registry_document(_base_document())
    stt = doc.routes[1]
    adapter = doc.adapter_by_id(stt.adapter_id)
    mr = to_model_route(stt, adapter)
    # per_unit_usd 0.0001 -> input_cost_per_million = 0.0001 * 1e6 = 100.0
    assert mr.input_cost_per_million_usd == pytest.approx(100.0)


def test_to_model_route_provider_managed_accepted_only_with_flag():
    document = _base_document()
    document["routes"][0]["model_revision"] = "provider-managed"
    document["routes"][0]["allow_provider_managed_revision"] = True
    doc, _ = load_registry_document(document)
    route = doc.routes[0]
    adapter = doc.adapter_by_id(route.adapter_id)
    mr = to_model_route(route, adapter)
    assert mr.model_revision == "provider-managed"
    assert mr.allow_provider_managed_revision is True


def test_to_model_route_operation_remap_for_caption():
    document = _base_document()
    document["routes"].append(
        {
            "id": "caption-remote",
            "capability": "vision.caption",
            "adapter_id": "chat-endpoint",
            "model_id": "cap-model",
            "model_revision": "2026-01-01",
            "pricing": {"status": "priced", "per_request_usd": 0.01},
            "budget": {"feature": "vision_captions"},
        }
    )
    doc, _ = load_registry_document(document)
    route = next(r for r in doc.routes if r.id == "caption-remote")
    mr = to_model_route(route, doc.adapter_by_id(route.adapter_id))
    assert mr.operations == (ModelOperation.VISION_PROMPT,)
    assert mr.fixed_cost_usd == 0.01


# -- ModelRoute revision rule ------------------------------------------------
def test_model_route_accepts_provider_managed_only_with_flag():
    with pytest.raises(ValueError):
        ModelRoute(
            route_id="r",
            capability=ModelCapability.PLANNING,
            adapter="openai_compatible",
            provider="p",
            model="m",
            model_revision="provider-managed",
        )
    route = ModelRoute(
        route_id="r",
        capability=ModelCapability.PLANNING,
        adapter="openai_compatible",
        provider="p",
        model="m",
        model_revision="provider-managed",
        allow_provider_managed_revision=True,
    )
    assert route.model_revision == "provider-managed"


def test_model_route_still_rejects_latest_with_flag():
    with pytest.raises(ValueError):
        ModelRoute(
            route_id="r",
            capability=ModelCapability.PLANNING,
            adapter="openai_compatible",
            provider="p",
            model="m",
            model_revision="latest",
            allow_provider_managed_revision=True,
        )


# -- locality helpers --------------------------------------------------------
def test_is_self_hosted_and_route_locality():
    local = AdapterRecord(id="a", type="local")
    private = AdapterRecord(id="b", type="openai_compatible", network="private")
    remote = AdapterRecord(id="c", type="openai_compatible", network="public")
    assert is_self_hosted(local) and route_locality(local) == "self_hosted"
    assert is_self_hosted(private) and route_locality(private) == "self_hosted"
    assert not is_self_hosted(remote) and route_locality(remote) == "remote"


# -- example file ------------------------------------------------------------
def test_example_file_validates_in_production():
    doc, diags = load_registry_document(_EXAMPLE_FILE)
    assert doc is not None, diags
    assert diags == []
    result = validate_registry(
        doc,
        app_env="production",
        gpu_workers_enabled=True,
        block_noncommercial=False,
    )
    assert result.errors() == (), result.errors()
    assert len(result.warnings()) == 1
    assert result.warnings()[0].code == DiagnosticCode.REVISION_PROVIDER_MANAGED


# -- CLI (subprocess, venv python, cwd apps/api) -----------------------------
def _run_cli(*args: str):
    return subprocess.run(
        [sys.executable, "-m", "app.ops.validate_inference_registry", *args],
        cwd=_API_DIR,
        capture_output=True,
        text=True,
    )


def test_cli_exits_zero_on_clean_registry():
    result = _run_cli(str(_EXAMPLE_FILE), "--app-env", "production")
    assert result.returncode == 0, result.stderr
    assert "revision_provider_managed" in result.stdout


def test_cli_exits_one_on_errors(tmp_path):
    bad = tmp_path / "bad.json"
    document = _base_document()
    document["routes"][0]["model_revision"] = "latest"
    bad.write_text(json.dumps(document), encoding="utf-8")
    result = _run_cli(str(bad), "--app-env", "production")
    assert result.returncode == 1
    assert "revision_floating" in result.stdout


def test_cli_validates_a_fragment_list_like_ai_registry_file():
    # The same comma-separated form AI_REGISTRY_FILE accepts at startup.
    cpu_local = _API_DIR.parents[1] / "infra" / "ai-registry" / "cpu-local.json"
    merged = _run_cli(f"{_EXAMPLE_FILE},{cpu_local}", "--app-env", "production")
    assert merged.returncode == 0, merged.stdout + merged.stderr
    collision = _run_cli(f"{_EXAMPLE_FILE},{_EXAMPLE_FILE}", "--app-env", "production")
    assert collision.returncode == 1
    assert "registry_fragment_collision" in collision.stdout


def test_cli_never_prints_secret_value(tmp_path):
    # The loader never reads a secret value; only the reference name appears.
    bad = tmp_path / "secret.json"
    document = _base_document()
    document["adapters"][0]["auth"] = {
        "type": "bearer",
        "secret_ref": "bundle:do-not-leak-sentinel",
    }
    # Force an error so diagnostics print, but no secret value is involved.
    document["routes"][0]["model_revision"] = "latest"
    bad.write_text(json.dumps(document), encoding="utf-8")
    result = _run_cli(str(bad), "--app-env", "production", "--json")
    # The ref name may appear; no secret value exists, so assert the sentinel
    # value is treated only as a reference name (never a resolved value).
    assert "resolved" not in result.stdout.lower()
    assert result.returncode == 1


# -- huggingface_endpoint / aws_bedrock adapter types (plan Tasks 24/25) ------
def _hf_adapter(**config) -> dict:
    cfg = {"mode": "tgi"}
    cfg.update(config)
    return {
        "id": "hf-endpoint",
        "type": "huggingface_endpoint",
        "base_url": "https://endpoint.example.invalid",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": "bundle:hf-token"},
        "config": cfg,
        "data_processor": {
            "display_name": "Serverless endpoint",
            "purpose": "Chat",
            "data_categories": ["text"],
            "policy_url": "https://endpoint.example.invalid/policy",
        },
    }


def _bedrock_adapter(**config) -> dict:
    cfg = {"region": "us-east-1", "api": "converse"}
    cfg.update(config)
    return {
        "id": "bedrock-endpoint",
        "type": "aws_bedrock",
        "network": "public",
        "auth": {"type": "none"},
        "config": cfg,
        "data_processor": {
            "display_name": "Managed model service",
            "purpose": "Chat",
            "data_categories": ["text"],
            "policy_url": "https://cloud.example.invalid/policy",
        },
    }


def test_huggingface_endpoint_route_validates():
    document = _base_document()
    document["adapters"].append(_hf_adapter(mode="tgi"))
    document["routes"].append(
        {
            "id": "planning-hf",
            "capability": "planning.chat",
            "adapter_id": "hf-endpoint",
            "model_id": "hf-chat",
            "model_revision": "2026-02-01",
            "pricing": {"status": "priced", "per_request_usd": 0.001},
            "budget": {"feature": "planning"},
        }
    )
    result = _validate(document, app_env="production")
    assert result.errors() == ()
    assert "planning-hf" in result.valid_route_ids


def test_huggingface_endpoint_pipeline_requires_task():
    document = _base_document()
    document["adapters"].append(_hf_adapter(mode="pipeline"))
    doc, diags = load_registry_document(document)
    # structural parse passes; config invalidity surfaces in validation
    assert doc is not None
    result = validate_registry(
        doc, app_env="test", gpu_workers_enabled=True, block_noncommercial=False
    )
    assert DiagnosticCode.ADAPTER_CONFIG_INVALID in _codes(result.diagnostics)


def test_huggingface_endpoint_rejects_non_bearer_auth():
    document = _base_document()
    adapter = _hf_adapter()
    adapter["auth"] = {"type": "custom_header", "header_name": "x-key", "secret_ref": "bundle:k"}
    document["adapters"].append(adapter)
    doc, _ = load_registry_document(document)
    assert doc is not None
    result = validate_registry(
        doc, app_env="test", gpu_workers_enabled=True, block_noncommercial=False
    )
    assert DiagnosticCode.ADAPTER_CONFIG_INVALID in _codes(result.diagnostics)


def test_huggingface_endpoint_requires_config_block():
    document = _base_document()
    adapter = _hf_adapter()
    del adapter["config"]
    document["adapters"].append(adapter)
    doc, diags = load_registry_document(document)
    assert doc is None
    assert diags[0].code == DiagnosticCode.ADAPTER_CONFIG_REQUIRED


def test_huggingface_endpoint_unsupported_capability():
    document = _base_document()
    document["adapters"].append(_hf_adapter(mode="tgi"))
    document["routes"].append(
        {
            "id": "objects-hf",
            "capability": "vision.object_detection",
            "adapter_id": "hf-endpoint",
            "model_id": "hf-det",
            "model_revision": "2026-02-01",
            "pricing": {"status": "priced", "per_request_usd": 0.001},
        }
    )
    result = _validate(document, app_env="test")
    assert DiagnosticCode.CAPABILITY_ADAPTER_UNSUPPORTED in _codes(result.diagnostics)


def test_aws_bedrock_route_needs_no_secret():
    document = _base_document()
    document["adapters"].append(_bedrock_adapter(api="converse"))
    document["routes"].append(
        {
            "id": "planning-bedrock",
            "capability": "planning.chat",
            "adapter_id": "bedrock-endpoint",
            "model_id": "cloud-chat",
            "model_revision": "2026-02-01",
            "pricing": {"status": "priced", "per_request_usd": 0.001},
            "budget": {"feature": "planning"},
        }
    )
    result = _validate(document, app_env="production")
    # No AUTH_NONE_REMOTE / AUTH_REQUIRED for the IAM-chain adapter.
    assert DiagnosticCode.AUTH_NONE_REMOTE not in _codes(result.diagnostics)
    assert DiagnosticCode.AUTH_REQUIRED not in _codes(result.diagnostics)
    assert "planning-bedrock" in result.valid_route_ids


def test_aws_bedrock_invoke_model_requires_embedding_family():
    document = _base_document()
    document["adapters"].append(_bedrock_adapter(api="invoke_model"))
    doc, _ = load_registry_document(document)
    assert doc is not None
    result = validate_registry(
        doc, app_env="test", gpu_workers_enabled=True, block_noncommercial=False
    )
    assert DiagnosticCode.ADAPTER_CONFIG_INVALID in _codes(result.diagnostics)


def test_aws_bedrock_rejects_base_url_not_needed_but_unknown_config_key():
    document = _base_document()
    adapter = _bedrock_adapter()
    adapter["config"]["mystery"] = "x"
    document["adapters"].append(adapter)
    doc, _ = load_registry_document(document)
    assert doc is not None
    result = validate_registry(
        doc, app_env="test", gpu_workers_enabled=True, block_noncommercial=False
    )
    assert DiagnosticCode.ADAPTER_CONFIG_INVALID in _codes(result.diagnostics)


def test_config_block_rejected_on_other_adapter_types():
    document = _base_document()
    document["adapters"][0]["config"] = {"mode": "tgi"}
    doc, diags = load_registry_document(document)
    assert doc is None
    assert diags[0].code == DiagnosticCode.ADAPTER_CONFIG_NOT_ALLOWED


# -- New registry examples (plan Tasks 24/25/33) -----------------------------
_EXAMPLES_DIR = _API_DIR.parent.parent / "docs" / "ai-registry" / "examples"
_NEW_EXAMPLES = (
    _EXAMPLES_DIR / "huggingface-endpoint.json",
    _EXAMPLES_DIR / "aws-bedrock.json",
    _EXAMPLES_DIR / "self-hosted" / "vllm-chat.json",
    _EXAMPLES_DIR / "self-hosted" / "openai-compatible-speech-to-text.json",
    _EXAMPLES_DIR / "self-hosted" / "tei-embeddings.json",
    _EXAMPLES_DIR / "self-hosted" / "tgi.json",
)


@pytest.mark.parametrize("example", _NEW_EXAMPLES, ids=lambda p: p.name)
def test_new_example_validates_in_production(example: Path):
    doc, diags = load_registry_document(example)
    assert doc is not None, diags
    assert diags == []
    result = validate_registry(
        doc,
        app_env="production",
        gpu_workers_enabled=True,
        block_noncommercial=False,
    )
    assert result.errors() == (), result.errors()


@pytest.mark.parametrize("example", _NEW_EXAMPLES, ids=lambda p: p.name)
def test_new_example_cli_validates_combined_with_main_example(example: Path):
    # Mirrors the ops check: each new example merged with the main example in a
    # comma list (as AI_REGISTRY_FILE accepts) validates in production.
    result = _run_cli(f"{example},{_EXAMPLE_FILE}", "--app-env", "production")
    assert result.returncode == 0, result.stdout + result.stderr
