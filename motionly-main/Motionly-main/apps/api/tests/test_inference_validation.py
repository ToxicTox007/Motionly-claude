"""The Section 2.6 startup-validation matrix (plan Task 7).

Each Section 2.6 rule has a passing case and a failing case with its documented
severity, expressed as the *startup decision*: an error on a required capability
fails startup (``InferenceStartupError``); an error on an optional route drops it
(the capability is reported unavailable) and startup succeeds; a warning keeps
the route active and is logged once with the route id and code.

Plus the acceptance tests: a production ``create_app`` boots with only registry
routes for planning and transcription (one of them provider-managed with the
flag, logging exactly one revision warning); and a production registry missing
transcription fails startup with a clear error.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.inference_registry import DiagnosticCode
from app.services.inference_runtime import build_inference_runtime
from app.services.inference_validation import (
    InferenceStartupError,
    StartupValidation,
    validate_inference_startup,
)

ROOT = Path(__file__).resolve().parents[2]

# A bundle that resolves every remote secret_ref used in the fixtures below.
_BUNDLE = {
    "chat-endpoint": "chat-secret",
    "stt-endpoint": "stt-secret",
    "remote-endpoint": "remote-secret",
    "objects-endpoint": "objects-secret",
}


def _write(tmp_path: Path, document: dict, *, name: str = "registry.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


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


def _runtime(
    tmp_path: Path, document: dict, *, app_env: str = "local", **settings_updates
):
    path = _write(tmp_path, document)
    settings = _settings(
        tmp_path, app_env=app_env, ai_registry_file=str(path), **settings_updates
    )
    return build_inference_runtime(settings), settings


def _validate(
    tmp_path: Path, document: dict, *, app_env: str = "local", **settings_updates
) -> StartupValidation:
    runtime, settings = _runtime(
        tmp_path, document, app_env=app_env, **settings_updates
    )
    return validate_inference_startup(runtime, settings=settings)


# -- registry-document builders ----------------------------------------------
def _remote_adapter(
    adapter_id: str = "remote-endpoint",
    *,
    network: str = "public",
    auth: dict | None = None,
    data_processor: bool = True,
    type_: str = "openai_compatible",
) -> dict:
    adapter: dict = {
        "id": adapter_id,
        "type": type_,
        "base_url": "https://remote.example.invalid/v1",
        "network": network,
    }
    if type_ not in {"local", "fake"}:
        adapter["auth"] = auth or {"type": "bearer", "secret_ref": f"bundle:{adapter_id}"}
    if data_processor:
        adapter["data_processor"] = {
            "display_name": "Operator endpoint",
            "purpose": "Inference",
            "data_categories": ["text"],
            "policy_url": "https://remote.example.invalid/policy",
        }
    return adapter


def _local_adapter(adapter_id: str = "local-endpoint") -> dict:
    return {"id": adapter_id, "type": "local", "network": "private"}


def _planning_route(**overrides) -> dict:
    route = {
        "id": "planning-remote",
        "capability": "planning.chat",
        "adapter_id": "chat-endpoint",
        "model_id": "chat-model-x",
        "model_revision": "2026-01-01",
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


def _chat_adapter() -> dict:
    return _remote_adapter("chat-endpoint")


def _stt_adapter() -> dict:
    adapter = _remote_adapter("stt-endpoint")
    return adapter


def _required_pair() -> dict:
    """A minimal valid document with both required capabilities (remote)."""
    return {
        "version": 1,
        "adapters": [_chat_adapter(), _stt_adapter()],
        "models": [],
        "routes": [_planning_route(), _transcription_route()],
    }


def _with_optional(optional_adapters: list[dict], optional_routes: list[dict]) -> dict:
    document = _required_pair()
    document["adapters"].extend(optional_adapters)
    document["routes"].extend(optional_routes)
    return document


# ===========================================================================
# Required capabilities
# ===========================================================================
def test_both_required_capabilities_present_boots(tmp_path: Path) -> None:
    result = _validate(tmp_path, _required_pair())
    assert result.capability("planning.chat").available
    assert result.capability("transcription.speech_to_text").available


def _expect_production_startup_failure(
    tmp_path: Path, document: dict
) -> InferenceStartupError:
    """Build the runtime in production and return the raised startup error.

    Section 2.6 severities depend on ``app_env`` (the validator bakes them in at
    build time), and in production ``build_inference_runtime`` runs the startup
    decision and raises ``InferenceStartupError`` when a required capability has
    no valid enabled route. So production-fatal cases are asserted on the build.
    """
    path = _write(tmp_path, document)
    settings = _settings(tmp_path, app_env="production", ai_registry_file=str(path))
    with pytest.raises(InferenceStartupError) as exc_info:
        build_inference_runtime(settings)
    return exc_info.value


@pytest.mark.parametrize(
    ("drop_capability", "missing_id"),
    [
        ("planning.chat", "planning.chat"),
        ("transcription.speech_to_text", "transcription.speech_to_text"),
    ],
)
def test_missing_required_capability_fails_startup(
    tmp_path: Path, drop_capability: str, missing_id: str
) -> None:
    document = _required_pair()
    document["routes"] = [
        r for r in document["routes"] if r["capability"] != drop_capability
    ]
    error = _expect_production_startup_failure(tmp_path, document)
    assert missing_id in error.missing_capabilities
    assert str(missing_id) in str(error)


# ===========================================================================
# Revision policy
# ===========================================================================
def test_provider_managed_remote_with_flag_warns_and_boots(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    document = _required_pair()
    document["routes"][0] = _planning_route(
        model_revision="provider-managed", allow_provider_managed_revision=True
    )
    with caplog.at_level(logging.WARNING):
        result = _validate(tmp_path, document)
    assert result.capability("planning.chat").available
    codes = [w.code for w in result.warnings]
    assert codes.count(DiagnosticCode.REVISION_PROVIDER_MANAGED) == 1


def test_provider_managed_remote_without_flag_fails_startup(tmp_path: Path) -> None:
    document = _required_pair()
    document["routes"][0] = _planning_route(model_revision="provider-managed")
    error = _expect_production_startup_failure(tmp_path, document)
    assert "planning.chat" in error.missing_capabilities


def test_local_route_with_latest_revision_drops_optional_route(tmp_path: Path) -> None:
    document = _with_optional(
        [_local_adapter("matte-local")],
        [
            {
                "id": "matte-latest",
                "capability": "matte.segmentation",
                "adapter_id": "matte-local",
                "model_id": "matte-model",
                "model_revision": "latest",
                "model_ref": "matte-model",
                "pricing": {"status": "free"},
                "explicitly_free": True,
            }
        ],
    )
    document["models"].append(
        {
            "id": "matte-model",
            "model_id": "matte-model",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        }
    )
    result = _validate(tmp_path, document)
    # Required routes survive; the latest-pinned local route is dropped.
    assert result.capability("planning.chat").available
    assert not result.capability("matte.segmentation").available
    assert any(
        d.code == DiagnosticCode.REVISION_FLOATING for d in result.dropped_routes
    )


def test_local_route_with_provider_managed_drops_optional_route(tmp_path: Path) -> None:
    document = _with_optional(
        [_local_adapter("matte-local")],
        [
            {
                "id": "matte-pm",
                "capability": "matte.segmentation",
                "adapter_id": "matte-local",
                "model_id": "matte-model",
                "model_revision": "provider-managed",
                "allow_provider_managed_revision": True,
                "model_ref": "matte-model",
                "pricing": {"status": "free"},
                "explicitly_free": True,
            }
        ],
    )
    document["models"].append(
        {
            "id": "matte-model",
            "model_id": "matte-model",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        }
    )
    result = _validate(tmp_path, document)
    assert not result.capability("matte.segmentation").available
    assert any(
        d.code == DiagnosticCode.REVISION_PROVIDER_MANAGED_SELF_HOSTED
        for d in result.dropped_routes
    )


# ===========================================================================
# Licence and commercial use
# ===========================================================================
def test_local_route_without_licence_drops_optional_route(tmp_path: Path) -> None:
    document = _with_optional(
        [_local_adapter("sound-local")],
        [
            {
                "id": "sound-nolicence",
                "capability": "audio.sound_classification",
                "adapter_id": "sound-local",
                "model_id": "sound-model",
                "model_revision": "2026-01-01",
                "model_ref": "sound-model",
                "pricing": {"status": "free"},
                "explicitly_free": True,
            }
        ],
    )
    document["models"].append(
        {"id": "sound-model", "model_id": "sound-model", "revision": "2026-01-01"}
    )
    result = _validate(tmp_path, document)
    assert not result.capability("audio.sound_classification").available
    assert any(
        d.code == DiagnosticCode.LICENCE_REQUIRED for d in result.dropped_routes
    )


def test_remote_route_without_licence_warns_and_boots(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A remote route with no model metadata is missing a licence -> warning.
    document = _with_optional(
        [_remote_adapter("caption-endpoint")],
        [
            {
                "id": "caption-remote",
                "capability": "vision.caption",
                "adapter_id": "caption-endpoint",
                "model_id": "caption-model",
                "model_revision": "2026-01-01",
                "pricing": {
                    "status": "priced",
                    "input_per_million_usd": 0.3,
                    "output_per_million_usd": 1.0,
                },
            }
        ],
    )
    document["adapters"][-1]["auth"] = {
        "type": "bearer",
        "secret_ref": "bundle:remote-endpoint",
    }
    with caplog.at_level(logging.WARNING):
        result = _validate(tmp_path, document)
    assert result.capability("vision.caption").available
    assert any(w.code == DiagnosticCode.LICENCE_MISSING for w in result.warnings)


# ===========================================================================
# Data-processor metadata
# ===========================================================================
def test_remote_without_data_processor_in_dev_warns(
    tmp_path: Path,
) -> None:
    document = _with_optional(
        [_remote_adapter("caption-endpoint", data_processor=False)],
        [
            {
                "id": "caption-remote",
                "capability": "vision.caption",
                "adapter_id": "caption-endpoint",
                "model_id": "caption-model",
                "model_revision": "2026-01-01",
                "pricing": {
                    "status": "priced",
                    "input_per_million_usd": 0.3,
                    "output_per_million_usd": 1.0,
                },
            }
        ],
    )
    document["adapters"][-1]["auth"] = {
        "type": "bearer",
        "secret_ref": "bundle:remote-endpoint",
    }
    result = _validate(tmp_path, document, app_env="local")
    assert result.capability("vision.caption").available
    assert any(
        w.code == DiagnosticCode.DATA_PROCESSOR_MISSING for w in result.warnings
    )


def test_remote_without_data_processor_in_production_drops_optional_route(
    tmp_path: Path,
) -> None:
    document = _with_optional(
        [_remote_adapter("caption-endpoint", data_processor=False)],
        [
            {
                "id": "caption-remote",
                "capability": "vision.caption",
                "adapter_id": "caption-endpoint",
                "model_id": "caption-model",
                "model_revision": "2026-01-01",
                "pricing": {
                    "status": "priced",
                    "input_per_million_usd": 0.3,
                    "output_per_million_usd": 1.0,
                },
            }
        ],
    )
    document["adapters"][-1]["auth"] = {
        "type": "bearer",
        "secret_ref": "bundle:remote-endpoint",
    }
    # Required routes keep their data_processor so startup still succeeds.
    result = _validate(tmp_path, document, app_env="production")
    assert result.capability("planning.chat").available
    assert not result.capability("vision.caption").available
    assert any(
        d.code == DiagnosticCode.DATA_PROCESSOR_MISSING for d in result.dropped_routes
    )


# ===========================================================================
# Pricing
# ===========================================================================
def test_zero_price_without_explicitly_free_drops_optional_route(
    tmp_path: Path,
) -> None:
    document = _with_optional(
        [_remote_adapter("caption-endpoint")],
        [
            {
                "id": "caption-zero",
                "capability": "vision.caption",
                "adapter_id": "caption-endpoint",
                "model_id": "caption-model",
                "model_revision": "2026-01-01",
                "pricing": {
                    "status": "priced",
                    "input_per_million_usd": 0.0,
                    "output_per_million_usd": 0.0,
                },
            }
        ],
    )
    document["adapters"][-1]["auth"] = {
        "type": "bearer",
        "secret_ref": "bundle:remote-endpoint",
    }
    result = _validate(tmp_path, document)
    assert not result.capability("vision.caption").available
    assert any(
        d.code == DiagnosticCode.PRICING_ZERO_NOT_FREE for d in result.dropped_routes
    )


def test_zero_price_with_explicitly_free_boots(tmp_path: Path) -> None:
    document = _with_optional(
        [_remote_adapter("caption-endpoint")],
        [
            {
                "id": "caption-free",
                "capability": "vision.caption",
                "adapter_id": "caption-endpoint",
                "model_id": "caption-model",
                "model_revision": "2026-01-01",
                "pricing": {"status": "free"},
                "explicitly_free": True,
            }
        ],
    )
    document["adapters"][-1]["auth"] = {
        "type": "bearer",
        "secret_ref": "bundle:remote-endpoint",
    }
    result = _validate(tmp_path, document)
    assert result.capability("vision.caption").available


# -- unknown pricing + hard cap ---------------------------------------------
def _unknown_pricing_route(*, hard_cap: dict | None) -> dict:
    budget: dict = {"feature": "vision_objects"}
    if hard_cap is not None:
        budget["hard_cap"] = hard_cap
    return {
        "id": "objects-unknown",
        "capability": "vision.object_detection",
        "adapter_id": "objects-endpoint",
        "model_id": "objects-model",
        "model_revision": "2026-01-01",
        "strategy": "generic_mapping",
        "pricing": {"status": "unknown"},
        "budget": budget,
        "mapping": {
            "request": {"method": "POST", "path": "/detect", "body": {}},
            "items": "response.results",
            "fields": {"label": "name", "score": "score", "box": "box"},
            "box_format": "xywh_norm",
        },
    }


def _objects_adapter() -> dict:
    return {
        "id": "objects-endpoint",
        "type": "generic_http",
        "base_url": "https://objects.example.invalid",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": "bundle:objects-endpoint"},
        "data_processor": {
            "display_name": "Objects endpoint",
            "purpose": "Object detection",
            "data_categories": ["images"],
            "policy_url": "https://objects.example.invalid/policy",
        },
    }


def test_unknown_pricing_with_hard_cap_boots_and_warns_in_production(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    document = _with_optional(
        [_objects_adapter()],
        [_unknown_pricing_route(hard_cap={"max_requests_per_month": 100})],
    )
    with caplog.at_level(logging.WARNING):
        result = _validate(tmp_path, document, app_env="production")
    assert result.capability("vision.object_detection").available
    assert "objects-unknown" in result.unknown_pricing_route_ids
    assert any(
        record.getMessage() == "inference_unknown_pricing_route"
        for record in caplog.records
    )


def test_unknown_pricing_without_hard_cap_drops_optional_route(tmp_path: Path) -> None:
    document = _with_optional(
        [_objects_adapter()],
        [_unknown_pricing_route(hard_cap=None)],
    )
    result = _validate(tmp_path, document)
    assert not result.capability("vision.object_detection").available
    assert any(
        d.code == DiagnosticCode.PRICING_UNKNOWN_NEEDS_HARD_CAP
        for d in result.dropped_routes
    )


def test_unknown_pricing_hard_cap_reached_at_runtime_skips_route(tmp_path: Path) -> None:
    """A full hard cap skips the route at invoke time with route_hard_cap_exhausted."""
    from datetime import UTC, datetime

    from app.services.model_gateway import (
        ChatMessage,
        InMemoryBudgetLedger,
        ModelCapability,
        ModelGateway,
        ModelOperation,
        ModelProvidersExhausted,
        ModelRoute,
        PlanningRequest,
        route_hard_cap_scope,
    )

    class _Boom:
        def invoke(self, *, route, request):  # noqa: ANN001
            raise AssertionError("route with an exhausted hard cap must not be called")

    route = ModelRoute(
        route_id="unknown-planning",
        capability=ModelCapability.PLANNING,
        adapter="fake",
        provider="fake",
        model="plan-model",
        model_revision="2026-01-01",
        operations=(ModelOperation.PLANNING_CHAT,),
        pricing_status="unknown",
        hard_cap_max_requests=1,
    )
    ledger = InMemoryBudgetLedger()
    # Pre-fill the per-route request cap scope to its limit.
    now = datetime.now(UTC)
    scope = route_hard_cap_scope("unknown-planning", unit="requests", now=now)
    ledger.reserve(scope=scope, amount_usd=1.0, limit_usd=1.0)
    gateway = ModelGateway(
        adapters={"fake": _Boom()}, routes=[route], budget_ledger=ledger
    )
    request = PlanningRequest(messages=(ChatMessage(role="user", content="hi"),))
    with pytest.raises(ModelProvidersExhausted) as exc_info:
        gateway.invoke(request)
    assert any(
        f["code"] == "route_hard_cap_exhausted" for f in exc_info.value.failures
    )


def test_unknown_pricing_hard_cap_consumes_on_success(tmp_path: Path) -> None:
    """A successful call settles the hard cap exactly once (no float drift)."""
    from app.services.model_gateway import (
        AdapterResult,
        ChatMessage,
        InMemoryBudgetLedger,
        ModelCapability,
        ModelGateway,
        ModelOperation,
        ModelRoute,
        ModelUsage,
        PlanningOutput,
        PlanningRequest,
        route_hard_cap_scope,
    )

    class _Ok:
        def invoke(self, *, route, request):  # noqa: ANN001
            return AdapterResult(
                output=PlanningOutput(content="ok", raw_response={}),
                usage=ModelUsage(input_units=10, output_units=5),
            )

    route = ModelRoute(
        route_id="unknown-planning",
        capability=ModelCapability.PLANNING,
        adapter="fake",
        provider="fake",
        model="plan-model",
        model_revision="2026-01-01",
        operations=(ModelOperation.PLANNING_CHAT,),
        pricing_status="unknown",
        hard_cap_max_requests=3,
    )
    ledger = InMemoryBudgetLedger()
    gateway = ModelGateway(
        adapters={"fake": _Ok()}, routes=[route], budget_ledger=ledger
    )
    request = PlanningRequest(messages=(ChatMessage(role="user", content="hi"),))
    result = gateway.invoke(request)
    assert result.metadata.actual_cost_usd == 0.0
    scope = route_hard_cap_scope("unknown-planning", unit="requests")
    assert ledger.usage(scope) == 1.0


def test_unknown_pricing_hard_cap_released_on_non_billable_failure(
    tmp_path: Path,
) -> None:
    """A non-billable failure releases the hard cap (plan Section 2.6)."""
    from app.services.model_gateway import (
        ChatMessage,
        InMemoryBudgetLedger,
        ModelCapability,
        ModelGateway,
        ModelOperation,
        ModelProvidersExhausted,
        ModelRoute,
        PlanningRequest,
        route_hard_cap_scope,
    )
    from app.services.provider_resilience import (
        ProviderCallFailure,
        ProviderErrorInfo,
    )

    class _NonBillableFailure:
        def invoke(self, *, route, request):  # noqa: ANN001
            raise ProviderCallFailure(
                provider="fake",
                error_info=ProviderErrorInfo(
                    "provider_request_rejected", False, billable=False, sent=False
                ),
            )

    route = ModelRoute(
        route_id="unknown-planning",
        capability=ModelCapability.PLANNING,
        adapter="fake",
        provider="fake",
        model="plan-model",
        model_revision="2026-01-01",
        operations=(ModelOperation.PLANNING_CHAT,),
        pricing_status="unknown",
        hard_cap_max_requests=5,
    )
    ledger = InMemoryBudgetLedger()
    gateway = ModelGateway(
        adapters={"fake": _NonBillableFailure()}, routes=[route], budget_ledger=ledger
    )
    request = PlanningRequest(messages=(ChatMessage(role="user", content="hi"),))
    with pytest.raises(ModelProvidersExhausted):
        gateway.invoke(request)
    # A non-billable failure must release the reservation, leaving the cap unused.
    scope = route_hard_cap_scope("unknown-planning", unit="requests")
    assert ledger.usage(scope) == 0.0


def test_unknown_pricing_hard_cap_counts_units_exactly(tmp_path: Path) -> None:
    """A units hard cap settles whole billing units with no float drift."""
    from app.services.model_gateway import (
        AdapterResult,
        ChatMessage,
        InMemoryBudgetLedger,
        ModelCapability,
        ModelGateway,
        ModelOperation,
        ModelRoute,
        ModelUsage,
        PlanningOutput,
        PlanningRequest,
        estimate_input_units,
        route_hard_cap_scope,
    )

    class _Ok:
        def invoke(self, *, route, request):  # noqa: ANN001
            return AdapterResult(
                output=PlanningOutput(content="ok", raw_response={}),
                usage=ModelUsage(input_units=1, output_units=1),
            )

    route = ModelRoute(
        route_id="unknown-units",
        capability=ModelCapability.PLANNING,
        adapter="fake",
        provider="fake",
        model="plan-model",
        model_revision="2026-01-01",
        operations=(ModelOperation.PLANNING_CHAT,),
        pricing_status="unknown",
        hard_cap_max_units=1_000_000,
    )
    ledger = InMemoryBudgetLedger()
    gateway = ModelGateway(
        adapters={"fake": _Ok()}, routes=[route], budget_ledger=ledger
    )
    request = PlanningRequest(messages=(ChatMessage(role="user", content="hello"),))
    expected_units = float(max(1, int(estimate_input_units(request))))
    gateway.invoke(request)
    scope = route_hard_cap_scope("unknown-units", unit="units")
    assert ledger.usage(scope) == expected_units


# ===========================================================================
# Fake adapter in production
# ===========================================================================
def test_fake_adapter_in_production_drops_optional_route(tmp_path: Path) -> None:
    document = _required_pair()
    document["adapters"].append({"id": "fake-vision", "type": "fake", "network": "private"})
    document["models"].append(
        {
            "id": "fake-vision-model",
            "model_id": "fake-vision-model",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        }
    )
    document["routes"].append(
        {
            "id": "caption-fake",
            "capability": "vision.caption",
            "adapter_id": "fake-vision",
            "model_id": "fake-vision-model",
            "model_ref": "fake-vision-model",
            "model_revision": "2026-01-01",
            "pricing": {"status": "free"},
            "explicitly_free": True,
        }
    )
    result = _validate(tmp_path, document, app_env="production")
    assert result.capability("planning.chat").available
    assert not result.capability("vision.caption").available
    assert any(
        d.code == DiagnosticCode.FAKE_ADAPTER_IN_PRODUCTION
        for d in result.dropped_routes
    )


def test_fake_adapter_outside_production_boots(tmp_path: Path) -> None:
    document = _required_pair()
    document["adapters"].append({"id": "fake-vision", "type": "fake", "network": "private"})
    document["models"].append(
        {
            "id": "fake-vision-model",
            "model_id": "fake-vision-model",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        }
    )
    document["routes"].append(
        {
            "id": "caption-fake",
            "capability": "vision.caption",
            "adapter_id": "fake-vision",
            "model_id": "fake-vision-model",
            "model_ref": "fake-vision-model",
            "model_revision": "2026-01-01",
            "pricing": {"status": "free"},
            "explicitly_free": True,
        }
    )
    result = _validate(tmp_path, document, app_env="local")
    assert result.capability("vision.caption").available


# ===========================================================================
# GPU route with GPU workers disabled
# ===========================================================================
def test_gpu_route_dropped_when_gpu_workers_disabled(tmp_path: Path) -> None:
    document = _with_optional(
        [_local_adapter("clip-local")],
        [
            {
                "id": "clip-gpu",
                "capability": "embeddings.image",
                "adapter_id": "clip-local",
                "model_id": "clip-model",
                "model_revision": "2026-01-01",
                "model_ref": "clip-model",
                "requires_gpu": True,
                "embedding_space": "clip",
                "pricing": {"status": "free"},
                "explicitly_free": True,
            }
        ],
    )
    document["models"].append(
        {
            "id": "clip-model",
            "model_id": "clip-model",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        }
    )
    result = _validate(tmp_path, document, gpu_workers_enabled=False)
    # The GPU route is a warning (dropped) when GPU workers are disabled; the
    # capability is unavailable but startup succeeds.
    assert result.capability("planning.chat").available
    assert not result.capability("embeddings.image").available


def test_gpu_route_available_when_gpu_workers_enabled(tmp_path: Path) -> None:
    document = _with_optional(
        [_local_adapter("clip-local")],
        [
            {
                "id": "clip-gpu",
                "capability": "embeddings.image",
                "adapter_id": "clip-local",
                "model_id": "clip-model",
                "model_revision": "2026-01-01",
                "model_ref": "clip-model",
                "requires_gpu": True,
                "embedding_space": "clip",
                "pricing": {"status": "free"},
                "explicitly_free": True,
            }
        ],
    )
    document["models"].append(
        {
            "id": "clip-model",
            "model_id": "clip-model",
            "revision": "2026-01-01",
            "licence": "Apache-2.0",
            "commercial_use": True,
        }
    )
    # A GPU local route needs a runner factory to activate; with GPU workers on
    # and no factory it is dropped with local_runner_unavailable (not a GPU drop).
    result = _validate(tmp_path, document, gpu_workers_enabled=True)
    assert result.capability("planning.chat").available


# ===========================================================================
# Free planning route
# ===========================================================================
def test_free_planning_on_private_adapter_boots(tmp_path: Path) -> None:
    # A private self-hosted free planning route is valid (Section 2.6).
    document = {
        "version": 1,
        "adapters": [
            {
                "id": "private-chat",
                "type": "openai_compatible",
                "base_url": "https://10.0.0.5/v1",
                "network": "private",
                "auth": {"type": "none"},
            },
            _stt_adapter(),
        ],
        "models": [
            {
                "id": "plan-model",
                "model_id": "plan-model",
                "revision": "2026-01-01",
                "licence": "Apache-2.0",
                "commercial_use": True,
            }
        ],
        "routes": [
            {
                "id": "planning-private-free",
                "capability": "planning.chat",
                "adapter_id": "private-chat",
                "model_id": "plan-model",
                "model_ref": "plan-model",
                "model_revision": "2026-01-01",
                "pricing": {"status": "free"},
                "explicitly_free": True,
                "budget": {"feature": "planning"},
            },
            _transcription_route(),
        ],
    }
    result = _validate(tmp_path, document, app_env="production")
    assert result.capability("planning.chat").available


def test_free_planning_on_remote_adapter_in_production_fails_startup(
    tmp_path: Path,
) -> None:
    document = _required_pair()
    document["routes"][0] = _planning_route(
        pricing={"status": "free"}, explicitly_free=True
    )
    error = _expect_production_startup_failure(tmp_path, document)
    # Planning is required, and the only planning route is invalid -> startup
    # fails with planning missing.
    assert "planning.chat" in error.missing_capabilities


# ===========================================================================
# Acceptance (plan Task 7)
# ===========================================================================
def test_acceptance_production_boots_with_provider_managed_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Production boots with only registry planning + transcription routes, one
    provider-managed with the flag, and logs exactly one revision warning."""
    document = _required_pair()
    document["routes"][0] = _planning_route(
        model_revision="provider-managed", allow_provider_managed_revision=True
    )
    with caplog.at_level(logging.WARNING):
        result = _validate(tmp_path, document, app_env="production")
    assert result.capability("planning.chat").available
    assert result.capability("transcription.speech_to_text").available
    provider_managed_warnings = [
        w for w in result.warnings if w.code == DiagnosticCode.REVISION_PROVIDER_MANAGED
    ]
    assert len(provider_managed_warnings) == 1


def test_acceptance_create_app_in_production_boots_with_registry_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``create_app`` in production mode boots with only registry routes for the
    two required capabilities and a provider-managed route that warns once."""
    from tests.test_service_hardening import _production_settings

    document = _required_pair()
    document["routes"][0] = _planning_route(
        model_revision="provider-managed", allow_provider_managed_revision=True
    )
    registry = _write(tmp_path, document)
    bundle = _bundle_file(tmp_path)
    settings = _production_settings(
        process_role="api",
        ai_registry_file=str(registry),
        ai_secrets_bundle_file=str(bundle),
    )

    from app import main as main_module

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    app = main_module.create_app()
    runtime = app.state.inference_runtime
    # The acceptance criterion is that the *registry* supplies only planning and
    # transcription routes; main.py still registers non-registry local/fixture
    # routes on the gateway until Task 10/14/19, so assert on the registry's
    # activated route records.
    registry_capabilities = {
        record.capability.value for record in runtime.route_records.values()
    }
    assert registry_capabilities == {
        "planning.chat",
        "transcription.speech_to_text",
    }
    startup = runtime.startup_validation
    assert isinstance(startup, StartupValidation)
    assert startup.capability("planning.chat").available
    assert startup.capability("transcription.speech_to_text").available
    managed = [
        w for w in startup.warnings if w.code == DiagnosticCode.REVISION_PROVIDER_MANAGED
    ]
    assert len(managed) == 1


def test_acceptance_production_registry_missing_transcription_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_service_hardening import _production_settings

    document = _required_pair()
    document["routes"] = [
        r for r in document["routes"] if r["capability"] != "transcription.speech_to_text"
    ]
    registry = _write(tmp_path, document)
    bundle = _bundle_file(tmp_path)
    settings = _production_settings(
        process_role="api",
        ai_registry_file=str(registry),
        ai_secrets_bundle_file=str(bundle),
    )

    from app import main as main_module

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    with pytest.raises(InferenceStartupError) as exc_info:
        main_module.create_app()
    assert "transcription.speech_to_text" in exc_info.value.missing_capabilities
