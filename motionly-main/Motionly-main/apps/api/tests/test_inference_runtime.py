"""Tests for the one registry-driven inference runtime (plan Task 6).

Covers the acceptance criteria: the API and both worker entrypoints build
identical catalogs from one registry file; a mismatch fails closed; planning and
transcription are served by fake routes through the real gateway; a missing
registry raises in production and yields an empty runtime locally; an invalid
optional route is dropped without hiding a required route; a missing secret file
drops a route without leaking; and every adapter type is constructed from
registry records with no vendor name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.inference_operations import InMemoryInferenceOperationRepository
from app.services.inference_runtime import (
    LOCAL_RUNNER_UNAVAILABLE,
    build_inference_runtime,
)
from app.services.model_adapters import AzureOpenAIAdapter, OpenAICompatibleAdapter
from app.services.model_gateway import ChatMessage, PlanningRequest

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inference_registry"
_FAKE_REGISTRY = _FIXTURES / "fake_planning_transcription.json"


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "APP_ENV": "local",
        "SKIP_API_AUTH": "true",
    }
    base.update(overrides)
    return Settings(**base)


def _write_registry(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# A fully-remote registry whose two required routes use an openai-compatible
# adapter with a bundle secret. No vendor name anywhere.
def _remote_document(secret_ref: str = "bundle:endpoint-key") -> dict:
    return {
        "version": 1,
        "adapters": [
            {
                "id": "chat-endpoint",
                "type": "openai_compatible",
                "base_url": "https://chat.example.invalid/v1",
                "network": "public",
                "auth": {"type": "bearer", "secret_ref": secret_ref},
                "data_processor": {
                    "display_name": "Operator endpoint",
                    "purpose": "Inference",
                    "data_categories": ["text", "audio"],
                    "policy_url": "https://chat.example.invalid/policy",
                },
            }
        ],
        "models": [],
        "routes": [
            {
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
            },
            {
                "id": "transcription-remote",
                "capability": "transcription.speech_to_text",
                "adapter_id": "chat-endpoint",
                "model_id": "stt-model-x",
                "model_revision": "2026-01-01",
                "pricing": {"status": "priced", "per_request_usd": 0.25},
                "budget": {"feature": "transcription"},
            },
        ],
    }


def _bundle(tmp_path: Path, **keys) -> Path:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Identical catalogs across API and worker builds
# ---------------------------------------------------------------------------
def test_api_and_both_workers_build_identical_catalogs():
    s = _settings(AI_REGISTRY_FILE=str(_FAKE_REGISTRY))
    ops = InMemoryInferenceOperationRepository()
    api = build_inference_runtime(s, operations_repository=ops)
    visual = build_inference_runtime(s, operations_repository=ops)
    matte = build_inference_runtime(s, operations_repository=ops)
    assert api.registry_version == visual.registry_version == matte.registry_version
    assert (
        api.catalog_fingerprint
        == visual.catalog_fingerprint
        == matte.catalog_fingerprint
    )
    assert api.registry_version.startswith("sha256:")


def test_catalog_fingerprint_changes_when_a_route_changes(tmp_path: Path):
    document = _remote_document()
    s1 = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(_bundle(tmp_path, **{"endpoint-key": "secret"})),
    )
    rt1 = build_inference_runtime(s1)
    document["routes"][0]["model_revision"] = "2026-02-02"
    path2 = tmp_path / "registry2.json"
    path2.write_text(json.dumps(document), encoding="utf-8")
    s2 = _settings(
        AI_REGISTRY_FILE=str(path2),
        AI_SECRETS_BUNDLE_FILE=str(_bundle(tmp_path, **{"endpoint-key": "secret"})),
    )
    rt2 = build_inference_runtime(s2)
    assert rt1.catalog_fingerprint != rt2.catalog_fingerprint
    assert rt1.registry_version != rt2.registry_version


# ---------------------------------------------------------------------------
# Worker fail-closed on fingerprint mismatch
# ---------------------------------------------------------------------------
def test_worker_fails_closed_on_registry_fingerprint_mismatch():
    from app.workers.inference_fingerprint import (
        MATTE_CAPABILITY_IDS,
        InferenceRegistryMismatch,
        enforce_registry_fingerprint,
    )

    s = _settings(AI_REGISTRY_FILE=str(_FAKE_REGISTRY))
    rt = build_inference_runtime(s)
    # A payload stamped with a stale fingerprint for a required capability.
    payload = {"registry_fingerprint": "catalog:stale", "video_id": "x"}
    with pytest.raises(InferenceRegistryMismatch):
        enforce_registry_fingerprint(
            payload, runtime=rt, capability_ids=MATTE_CAPABILITY_IDS
        )


def test_worker_accepts_matching_fingerprint_and_unstamped_payload():
    from app.workers.inference_fingerprint import (
        VISUAL_CAPABILITY_IDS,
        enforce_registry_fingerprint,
    )

    s = _settings(AI_REGISTRY_FILE=str(_FAKE_REGISTRY))
    rt = build_inference_runtime(s)
    matching = rt.capability_fingerprint(VISUAL_CAPABILITY_IDS)
    # A matching stamp passes.
    enforce_registry_fingerprint(
        {"registry_fingerprint": matching},
        runtime=rt,
        capability_ids=VISUAL_CAPABILITY_IDS,
    )
    # An unstamped payload passes (the check is additive).
    enforce_registry_fingerprint(
        {}, runtime=rt, capability_ids=VISUAL_CAPABILITY_IDS
    )


# ---------------------------------------------------------------------------
# Planning and transcription served by fake routes through the real gateway
# ---------------------------------------------------------------------------
def test_planning_served_by_fake_route_through_real_gateway():
    s = _settings(AI_REGISTRY_FILE=str(_FAKE_REGISTRY))
    rt = build_inference_runtime(s)
    assert {"planning-fake", "transcription-fake"} == {
        r.route_id for r in rt.gateway.routes
    }
    result = rt.gateway.invoke(
        PlanningRequest(messages=(ChatMessage(role="user", content="hi"),))
    )
    assert result.output.content == "fake plan"


# ---------------------------------------------------------------------------
# Path B startup rule
# ---------------------------------------------------------------------------
def test_missing_registry_raises_in_production():
    s = _settings(
        APP_ENV="production",
        SKIP_API_AUTH="false",
        SUPABASE_JWT_SECRET="x",
        SUPABASE_URL="https://db.example.invalid",
        SUPABASE_SERVICE_ROLE_KEY="k",
    )
    with pytest.raises(RuntimeError, match="AI_REGISTRY_FILE is required"):
        build_inference_runtime(s)


def test_missing_registry_yields_empty_runtime_locally():
    # Task 10: with no AI_REGISTRY_FILE in a dev/test env, the runtime serves the
    # built-in fake planning and transcription routes so create_app works out of
    # the box. Production still fails closed (see the production test above).
    rt = build_inference_runtime(_settings())
    route_ids = {r.route_id for r in rt.gateway.routes}
    assert route_ids == {"dev-planning-fake", "dev-transcription-fake"}
    assert rt.registry_version != "empty"
    assert {r.capability.value for r in rt.gateway.routes} == {"planning", "transcription"}


def test_unloadable_registry_raises(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="could not be loaded"):
        build_inference_runtime(_settings(AI_REGISTRY_FILE=str(bad)))


# ---------------------------------------------------------------------------
# Invalid optional route dropped without hiding a required route
# ---------------------------------------------------------------------------
def test_invalid_optional_route_dropped_required_route_survives(tmp_path: Path):
    document = _remote_document()
    # An optional captions route with a floating revision (an error) is dropped;
    # the required planning and transcription routes survive.
    document["routes"].append(
        {
            "id": "captions-bad",
            "capability": "vision.caption",
            "adapter_id": "chat-endpoint",
            "model_id": "cap-model",
            "model_revision": "latest",
            "pricing": {"status": "priced", "per_request_usd": 0.01},
            "budget": {"feature": "vision_captions"},
        }
    )
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(_bundle(tmp_path, **{"endpoint-key": "secret"})),
    )
    rt = build_inference_runtime(s)
    route_ids = {r.route_id for r in rt.gateway.routes}
    assert "planning-remote" in route_ids
    assert "transcription-remote" in route_ids
    assert "captions-bad" not in route_ids


# ---------------------------------------------------------------------------
# Missing secret file drops the route without leaking
# ---------------------------------------------------------------------------
def test_missing_secret_drops_route_without_leaking(tmp_path: Path):
    # A bundle that does not contain the referenced key: auth construction fails
    # for the adapter, so both routes on it are dropped with a diagnostic.
    document = _remote_document(secret_ref="bundle:absent-key")
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(_bundle(tmp_path, **{"other-key": "secret"})),
    )
    rt = build_inference_runtime(s)
    assert rt.gateway.routes == ()
    dropped = {d.route_id: d for d in rt.dropped_routes}
    # Both routes dropped because their shared adapter could not be built.
    assert "planning-remote" in dropped
    assert dropped["planning-remote"].code == "secret"
    # The reason never contains the secret value or key material.
    for d in rt.dropped_routes:
        assert "secret" not in d.message.lower() or "could not be resolved" in d.message


# ---------------------------------------------------------------------------
# Adapter construction from registry records (every type)
# ---------------------------------------------------------------------------
def test_openai_compatible_and_azure_adapters_built(tmp_path: Path):
    document = _remote_document()
    document["adapters"].append(
        {
            "id": "azure-endpoint",
            "type": "azure_openai",
            "base_url": "https://azure.example.invalid",
            "network": "public",
            "auth": {"type": "azure_api_key", "secret_ref": "bundle:azure-key"},
            "api_version": "2025-01-01-preview",
            "data_processor": {
                "display_name": "Azure-style endpoint",
                "purpose": "Inference",
                "data_categories": ["text"],
                "policy_url": "https://azure.example.invalid/policy",
            },
        }
    )
    document["routes"].append(
        {
            "id": "captions-azure",
            "capability": "vision.caption",
            "adapter_id": "azure-endpoint",
            "model_id": "deployment-x",
            "model_revision": "2026-01-01",
            "pricing": {"status": "priced", "per_request_usd": 0.01},
            "budget": {"feature": "vision_captions"},
        }
    )
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(
            _bundle(tmp_path, **{"endpoint-key": "k1", "azure-key": "k2"})
        ),
    )
    rt = build_inference_runtime(s)
    adapters = rt.gateway._adapters  # noqa: SLF001 - test introspection
    assert isinstance(adapters["chat-endpoint"], OpenAICompatibleAdapter)
    assert isinstance(adapters["azure-endpoint"], AzureOpenAIAdapter)


def test_generic_http_adapter_built_from_record(tmp_path: Path):
    document = _remote_document()
    document["adapters"].append(
        {
            "id": "objects-endpoint",
            "type": "generic_http",
            "base_url": "https://objects.example.invalid",
            "network": "public",
            "auth": {"type": "bearer", "secret_ref": "bundle:objects-key"},
            "data_processor": {
                "display_name": "Objects endpoint",
                "purpose": "Detection",
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
            "strategy": "generic_mapping",
            "pricing": {"status": "priced", "per_request_usd": 0.001},
            "budget": {"feature": "vision_objects"},
            "mapping": {
                "request": {"method": "POST", "path": "/v1/detect", "body": {}},
                "items": "response.results",
                "fields": {"label": "name", "score": "score", "box": "box"},
                "box_format": "vertices_norm",
            },
        }
    )
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(
            _bundle(tmp_path, **{"endpoint-key": "k1", "objects-key": "k2"})
        ),
    )
    rt = build_inference_runtime(s)
    from app.services.generic_http_adapter import GenericHttpAdapter

    assert isinstance(rt.gateway._adapters["objects-endpoint"], GenericHttpAdapter)  # noqa: SLF001


def test_async_job_adapter_built_from_record(tmp_path: Path):
    document = _remote_document()
    document["adapters"].append(
        {
            "id": "stems-endpoint",
            "type": "async_job",
            "base_url": "https://stems.example.invalid",
            "network": "public",
            "auth": {"type": "bearer", "secret_ref": "bundle:stems-key"},
            "data_processor": {
                "display_name": "Stems endpoint",
                "purpose": "Separation",
                "data_categories": ["audio"],
                "policy_url": "https://stems.example.invalid/policy",
            },
        }
    )
    document["routes"].append(
        {
            "id": "stems-remote",
            "capability": "audio.stem_separation",
            "adapter_id": "stems-endpoint",
            "model_id": "stem-model",
            "model_revision": "2026-01-01",
            "strategy": "async_job",
            "pricing": {"status": "priced", "per_unit_usd": 0.001},
            "budget": {"feature": "audio_stems"},
            "upload_filename": "source.flac",
            "mapping": {
                "steps": [
                    {
                        "method": "POST",
                        "path": "/jobs",
                        "body": {},
                        "capture": {"job_id": "response.id"},
                        "commit": True,
                    }
                ],
                "poll": {
                    "method": "GET",
                    "path": "/jobs/{vars.job_id}",
                    "status": "response.status",
                    "status_map": {
                        "running": {"state": "running"},
                        "done": {"state": "succeeded"},
                    },
                    "max_wait_s": 60,
                },
                "artifacts": {
                    "items": "response.tracks",
                    "name": "type",
                    "url": "url",
                    "name_map": {"a": "vocals", "b": "no_vocals"},
                    "required": ["vocals", "no_vocals"],
                    "allowed_hosts": ["files.example.invalid"],
                    "force_https": True,
                    "auth": "none",
                },
            },
        }
    )
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(
            _bundle(tmp_path, **{"endpoint-key": "k1", "stems-key": "k2"})
        ),
    )
    rt = build_inference_runtime(s)
    from app.services.async_job_adapter import AsyncJobAdapter

    assert isinstance(rt.gateway._adapters["stems-endpoint"], AsyncJobAdapter)  # noqa: SLF001


def test_openai_compatible_adapter_invokes_against_mock_transport(tmp_path: Path):
    document = _remote_document()
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(_bundle(tmp_path, **{"endpoint-key": "secret"})),
    )
    rt = build_inference_runtime(s)
    adapter = rt.gateway._adapters["chat-endpoint"]  # noqa: SLF001
    # The adapter carries the registry auth and a credential-free base URL.
    assert adapter.base_url == "https://chat.example.invalid/v1"
    assert adapter.auth is not None


# ---------------------------------------------------------------------------
# local runner without a factory is dropped with the new diagnostic code
# ---------------------------------------------------------------------------
def test_local_route_without_runner_factory_is_dropped(tmp_path: Path):
    document = _remote_document()
    document["adapters"].append(
        {"id": "cpu-matte", "type": "local", "network": "private"}
    )
    document["models"].append(
        {
            "id": "matte-model",
            "model_id": "selfie-segmenter",
            "revision": "sha256:" + "a" * 64,
            "licence": "Apache-2.0",
            "commercial_use": True,
            "weights_sha256": "a" * 64,
        }
    )
    document["routes"].append(
        {
            "id": "matte-local",
            "capability": "matte.segmentation",
            "adapter_id": "cpu-matte",
            "model_id": "selfie-segmenter",
            "model_ref": "matte-model",
            "model_revision": "sha256:" + "a" * 64,
            "strategy": "local",
            "pricing": {"status": "free"},
            "explicitly_free": True,
            "params": {"runner": "mediapipe_matte"},
        }
    )
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(_bundle(tmp_path, **{"endpoint-key": "secret"})),
    )
    rt = build_inference_runtime(s)
    dropped = {d.route_id: d.code for d in rt.dropped_routes}
    assert dropped.get("matte-local") == LOCAL_RUNNER_UNAVAILABLE
    assert "matte-local" not in {r.route_id for r in rt.gateway.routes}


def test_local_route_built_when_runner_factory_supplied(tmp_path: Path):
    document = _remote_document()
    document["adapters"].append(
        {"id": "cpu-matte", "type": "local", "network": "private"}
    )
    document["models"].append(
        {
            "id": "matte-model",
            "model_id": "selfie-segmenter",
            "revision": "sha256:" + "a" * 64,
            "licence": "Apache-2.0",
            "commercial_use": True,
            "weights_sha256": "a" * 64,
        }
    )
    document["routes"].append(
        {
            "id": "matte-local",
            "capability": "matte.segmentation",
            "adapter_id": "cpu-matte",
            "model_id": "selfie-segmenter",
            "model_ref": "matte-model",
            "model_revision": "sha256:" + "a" * 64,
            "strategy": "local",
            "pricing": {"status": "free"},
            "explicitly_free": True,
            "params": {"runner": "mediapipe_matte"},
        }
    )

    class _FakeLocalAdapter:
        def invoke(self, *, route, request):  # noqa: ANN001
            raise NotImplementedError

    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(_bundle(tmp_path, **{"endpoint-key": "secret"})),
    )
    rt = build_inference_runtime(
        s,
        local_adapter_factories={"mediapipe_matte": lambda **_: _FakeLocalAdapter()},
    )
    assert "matte-local" in {r.route_id for r in rt.gateway.routes}


# ---------------------------------------------------------------------------
# huggingface_endpoint and aws_bedrock adapter construction (plan Tasks 24/25)
# ---------------------------------------------------------------------------
def test_huggingface_endpoint_adapter_built_from_record(tmp_path: Path):
    document = _remote_document()
    document["adapters"].append(
        {
            "id": "endpoint-chat",
            "type": "huggingface_endpoint",
            "base_url": "https://endpoint.example.invalid",
            "network": "public",
            "auth": {"type": "bearer", "secret_ref": "bundle:endpoint-token"},
            "config": {"mode": "tgi"},
            "data_processor": {
                "display_name": "Serverless endpoint",
                "purpose": "Chat",
                "data_categories": ["text"],
                "policy_url": "https://endpoint.example.invalid/policy",
            },
        }
    )
    document["routes"].append(
        {
            "id": "planning-endpoint",
            "capability": "planning.chat",
            "adapter_id": "endpoint-chat",
            "model_id": "endpoint-chat-model",
            "model_revision": "2026-02-01",
            "pricing": {"status": "priced", "per_request_usd": 0.001},
            "budget": {"feature": "planning"},
        }
    )
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(
            _bundle(tmp_path, **{"endpoint-key": "k1", "endpoint-token": "tok"})
        ),
    )
    rt = build_inference_runtime(s)
    from app.services.huggingface_endpoint_adapter import HuggingFaceEndpointAdapter

    assert isinstance(
        rt.gateway._adapters["endpoint-chat"], HuggingFaceEndpointAdapter  # noqa: SLF001
    )
    assert "planning-endpoint" in rt.route_records


def test_huggingface_endpoint_pipeline_mode_built(tmp_path: Path):
    document = _remote_document()
    document["adapters"].append(
        {
            "id": "endpoint-asr",
            "type": "huggingface_endpoint",
            "base_url": "https://asr.example.invalid",
            "network": "public",
            "auth": {"type": "bearer", "secret_ref": "bundle:asr-token"},
            "config": {
                "mode": "pipeline",
                "pipeline_task": "automatic-speech-recognition",
            },
            "data_processor": {
                "display_name": "ASR endpoint",
                "purpose": "Transcription",
                "data_categories": ["audio"],
                "policy_url": "https://asr.example.invalid/policy",
            },
        }
    )
    document["routes"].append(
        {
            "id": "stt-endpoint",
            "capability": "transcription.speech_to_text",
            "adapter_id": "endpoint-asr",
            "model_id": "asr-model",
            "model_revision": "2026-02-01",
            "pricing": {"status": "priced", "per_request_usd": 0.001},
            "budget": {"feature": "transcription"},
        }
    )
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(
            _bundle(tmp_path, **{"endpoint-key": "k1", "asr-token": "tok"})
        ),
    )
    rt = build_inference_runtime(s)
    from app.services.huggingface_endpoint_adapter import HuggingFaceEndpointAdapter

    assert isinstance(
        rt.gateway._adapters["endpoint-asr"], HuggingFaceEndpointAdapter  # noqa: SLF001
    )


def test_aws_bedrock_adapter_built_without_secret(tmp_path: Path):
    document = _remote_document()
    document["adapters"].append(
        {
            "id": "bedrock-chat",
            "type": "aws_bedrock",
            "network": "public",
            "auth": {"type": "none"},
            "config": {"region": "us-east-1", "api": "converse"},
            "data_processor": {
                "display_name": "Managed model service",
                "purpose": "Chat",
                "data_categories": ["text"],
                "policy_url": "https://cloud.example.invalid/policy",
            },
        }
    )
    document["routes"].append(
        {
            "id": "planning-bedrock",
            "capability": "planning.chat",
            "adapter_id": "bedrock-chat",
            "model_id": "cloud-chat-model",
            "model_revision": "2026-02-01",
            "pricing": {"status": "priced", "per_request_usd": 0.001},
            "budget": {"feature": "planning"},
        }
    )
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(_bundle(tmp_path, **{"endpoint-key": "k1"})),
    )
    rt = build_inference_runtime(s)
    from app.services.aws_bedrock_adapter import AwsBedrockAdapter

    assert isinstance(
        rt.gateway._adapters["bedrock-chat"], AwsBedrockAdapter  # noqa: SLF001
    )
    assert "planning-bedrock" in rt.route_records


def test_aws_bedrock_invoke_model_embeddings_built(tmp_path: Path):
    document = _remote_document()
    document["adapters"].append(
        {
            "id": "bedrock-embed",
            "type": "aws_bedrock",
            "network": "public",
            "auth": {"type": "none"},
            "config": {
                "region": "eu-west-1",
                "api": "invoke_model",
                "embedding_family": "titan",
            },
            "data_processor": {
                "display_name": "Managed model service",
                "purpose": "Embeddings",
                "data_categories": ["text"],
                "policy_url": "https://cloud.example.invalid/policy",
            },
        }
    )
    document["routes"].append(
        {
            "id": "embeddings-bedrock",
            "capability": "embeddings.text",
            "adapter_id": "bedrock-embed",
            "model_id": "cloud-embed-model",
            "model_revision": "2026-02-01",
            "embedding_space": "cloud-embed",
            "pricing": {"status": "priced", "per_unit_usd": 0.0001},
            "budget": {"feature": "embeddings"},
        }
    )
    s = _settings(
        AI_REGISTRY_FILE=str(_write_registry(tmp_path, document)),
        AI_SECRETS_BUNDLE_FILE=str(_bundle(tmp_path, **{"endpoint-key": "k1"})),
    )
    rt = build_inference_runtime(s)
    from app.services.aws_bedrock_adapter import AwsBedrockAdapter

    assert isinstance(
        rt.gateway._adapters["bedrock-embed"], AwsBedrockAdapter  # noqa: SLF001
    )
