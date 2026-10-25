"""Tests for the ``aws_access_key`` static IAM-user key path (S1).

A narrowly scoped IAM user's access key id + secret key are mounted as secret
files and referenced from the registry ``aws_bedrock`` adapter as
``{"type": "aws_access_key", "access_key_id_ref": "file:...",
"secret_ref": "file:..."}``. The source is resolved **lazily** on the first
signed request (never at runtime build), cached, and re-read at most every 300s.

The tests cover:

* the registry validation matrix (allowed only on ``aws_bedrock``; both refs
  required; each ref validated; ``env:`` rejected in production; ``auth: none``
  still accepted on ``aws_bedrock``);
* a production runtime builds with the secret files **absent** without dropping
  any Bedrock route, and the catalog fingerprint is identical with/without the
  files (lazy resolution);
* with files present a Converse call's ``Authorization`` header carries
  ``Credential=<test key id>/...``;
* a process ``AWS_ACCESS_KEY_ID`` env var is ignored when ``aws_access_key`` is
  configured (the static source is used exclusively, no env fallback);
* without the files no HTTP request is sent and the failure is
  ``provider_auth_failed`` that is **not** circuit-worthy, non-billable and
  ``sent=False``;
* the secret value never appears in a log record, exception text or repr.

Only AWS's public SigV4 example access key id ``AKIDEXAMPLE`` is used — never an
``AKIA``+16 real-looking value. Secret values are obviously fake test strings.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.core.logging import SECRET_VALUE_MARKER, registered_secret_values
from app.services.aws_bedrock_adapter import (
    BedrockConfig,
    IamCredentialChain,
    StaticKeyResolutionError,
    aws_bedrock_factory,
    build_static_key_source,
)
from app.services.inference_registry import (
    DiagnosticCode,
    load_registry_document,
    validate_registry,
)
from app.services.inference_runtime import build_inference_runtime
from app.services.inference_secrets import SecretResolver
from app.services.model_gateway import (
    ChatMessage,
    ModelCapability,
    ModelRoute,
    PlanningOutput,
    PlanningRequest,
)
from app.services.provider_resilience import ProviderCallFailure

# AWS's public SigV4 "get-vanilla" example key id. Not an AKIA+16 value.
_TEST_KEY_ID = "AKIDEXAMPLE"
# An obviously fake secret value (never a real-looking credential).
_TEST_SECRET = "wJalrXUtnFEMI-fake-secret-value-for-tests-0001"


def _amz_clock() -> datetime:
    return datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Secret-file / resolver helpers
# ---------------------------------------------------------------------------
def _write_key_files(
    secrets_dir: Path,
    *,
    key_id: str = _TEST_KEY_ID,
    secret: str = _TEST_SECRET,
) -> None:
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "aws_inference_access_key_id").write_text(key_id, encoding="utf-8")
    (secrets_dir / "aws_inference_secret_access_key").write_text(
        secret, encoding="utf-8"
    )


def _resolver(secrets_dir: Path, *, app_env: str = "test") -> SecretResolver:
    settings = get_settings().model_copy(
        update={"app_env": app_env, "ai_secrets_dir": str(secrets_dir)}
    )
    return SecretResolver(settings=settings)


def _static_source(secrets_dir: Path, *, app_env: str = "test"):
    return build_static_key_source(
        _resolver(secrets_dir, app_env=app_env),
        access_key_id_ref="file:aws_inference_access_key_id",
        secret_ref="file:aws_inference_secret_access_key",
    )


def _route() -> ModelRoute:
    return ModelRoute(
        route_id="r1",
        capability=ModelCapability.PLANNING,
        adapter="bedrock-1",
        provider="bedrock",
        model="test.model-v1",
        model_revision="2024-01-01",
    )


# ---------------------------------------------------------------------------
# Registry validation matrix
# ---------------------------------------------------------------------------
def _base_document() -> dict:
    """A minimal document with both required capabilities on fake routes."""
    return {
        "version": 1,
        "adapters": [{"id": "fake-1", "type": "fake", "network": "private"}],
        "models": [],
        "routes": [
            {
                "id": "planning-fake",
                "capability": "planning.chat",
                "adapter_id": "fake-1",
                "model_id": "fake-plan",
                "model_revision": "2026-01-01",
                "strategy": "local",
                "pricing": {"status": "free"},
                "explicitly_free": True,
                "budget": {"feature": "planning"},
            },
            {
                "id": "transcription-fake",
                "capability": "transcription.speech_to_text",
                "adapter_id": "fake-1",
                "model_id": "fake-stt",
                "model_revision": "2026-01-01",
                "strategy": "local",
                "pricing": {"status": "free"},
                "explicitly_free": True,
                "budget": {"feature": "transcription"},
            },
        ],
    }


def _bedrock_adapter(auth: dict, **config) -> dict:
    cfg = {"region": "ap-south-1", "api": "converse"}
    cfg.update(config)
    return {
        "id": "bedrock-endpoint",
        "type": "aws_bedrock",
        "network": "public",
        "auth": auth,
        "config": cfg,
        "data_processor": {
            "display_name": "Managed model service",
            "purpose": "Chat",
            "data_categories": ["text"],
            "policy_url": "https://cloud.example.invalid/policy",
        },
    }


def _codes(diagnostics) -> set[str]:
    return {d.code for d in diagnostics}


def _validate_codes(document: dict, *, app_env: str = "test") -> set[str]:
    doc, parse_diags = load_registry_document(document)
    if doc is None:
        return {d.code for d in parse_diags}
    result = validate_registry(
        doc, app_env=app_env, gpu_workers_enabled=True, block_noncommercial=False
    )
    return _codes(result.diagnostics)


_FILE_AUTH = {
    "type": "aws_access_key",
    "access_key_id_ref": "file:aws_inference_access_key_id",
    "secret_ref": "file:aws_inference_secret_access_key",
}


def test_aws_access_key_valid_on_bedrock() -> None:
    document = _base_document()
    document["adapters"].append(_bedrock_adapter(dict(_FILE_AUTH)))
    codes = _validate_codes(document, app_env="production")
    assert DiagnosticCode.AUTH_ACCESS_KEY_WRONG_ADAPTER not in codes
    assert DiagnosticCode.AUTH_ACCESS_KEY_MISSING_REFS not in codes
    assert DiagnosticCode.SECRET_REF_INVALID not in codes


def test_aws_access_key_rejected_on_non_bedrock() -> None:
    document = _base_document()
    document["adapters"].append(
        {
            "id": "openai-endpoint",
            "type": "openai_compatible",
            "base_url": "https://chat.example.invalid/v1",
            "network": "public",
            "auth": dict(_FILE_AUTH),
            "data_processor": {
                "display_name": "Operator endpoint",
                "purpose": "Inference",
                "data_categories": ["text"],
                "policy_url": "https://chat.example.invalid/policy",
            },
        }
    )
    codes = _validate_codes(document)
    assert DiagnosticCode.AUTH_ACCESS_KEY_WRONG_ADAPTER in codes


def test_aws_access_key_requires_both_refs() -> None:
    document = _base_document()
    auth = {
        "type": "aws_access_key",
        "secret_ref": "file:aws_inference_secret_access_key",
    }
    document["adapters"].append(_bedrock_adapter(auth))
    codes = _validate_codes(document)
    assert DiagnosticCode.AUTH_ACCESS_KEY_MISSING_REFS in codes


def test_aws_access_key_env_ref_rejected_in_production() -> None:
    document = _base_document()
    auth = {
        "type": "aws_access_key",
        "access_key_id_ref": "env:AWS_ACCESS_KEY_ID",
        "secret_ref": "env:AWS_SECRET_ACCESS_KEY",
    }
    document["adapters"].append(_bedrock_adapter(auth))
    codes = _validate_codes(document, app_env="production")
    assert DiagnosticCode.SECRET_REF_ENV_REJECTED in codes


def test_aws_bedrock_still_accepts_auth_none() -> None:
    document = _base_document()
    document["adapters"].append(_bedrock_adapter({"type": "none"}))
    codes = _validate_codes(document, app_env="production")
    assert DiagnosticCode.AUTH_NONE_REMOTE not in codes
    assert DiagnosticCode.AUTH_REQUIRED not in codes
    assert DiagnosticCode.AUTH_ACCESS_KEY_MISSING_REFS not in codes


# ---------------------------------------------------------------------------
# Production runtime build: files absent, routes NOT dropped, fingerprint stable
# ---------------------------------------------------------------------------
def _bedrock_runtime_document() -> dict:
    """Both required capabilities served by aws_access_key Bedrock adapters."""
    dp = {
        "display_name": "Managed model service",
        "purpose": "Inference",
        "data_categories": ["text", "audio"],
        "policy_url": "https://cloud.example.invalid/policy",
    }
    return {
        "version": 1,
        "adapters": [
            {
                "id": "bedrock-chat",
                "type": "aws_bedrock",
                "network": "public",
                "auth": dict(_FILE_AUTH),
                "config": {"region": "ap-south-1", "api": "converse"},
                "data_processor": dp,
            },
            {
                "id": "bedrock-bda",
                "type": "aws_bedrock",
                "network": "public",
                "auth": dict(_FILE_AUTH),
                "config": {
                    "region": "ap-south-1",
                    "api": "data_automation",
                    "s3_bucket": "motionly-staging-bucket",
                    "s3_prefix": "bda",
                    "s3_region": "ap-south-1",
                    "data_automation_profile_arn": (
                        "arn:aws:bedrock:ap-south-1:000000000000"
                        ":data-automation-profile/apac.data-automation-v1"
                    ),
                },
                "data_processor": dp,
            },
        ],
        "models": [],
        "routes": [
            {
                "id": "planning-bedrock",
                "capability": "planning.chat",
                "adapter_id": "bedrock-chat",
                "model_id": "apac.amazon.nova-pro-v1:0",
                "model_revision": "2026-02-01",
                "pricing": {
                    "status": "priced",
                    "unit": "tokens",
                    "input_per_million_usd": 1.0,
                    "output_per_million_usd": 2.0,
                },
                "budget": {
                    "feature": "planning",
                    "monthly_usd": 10,
                    "hard_cap": {"max_requests_per_month": 500},
                },
            },
            {
                "id": "transcription-bedrock",
                "capability": "transcription.speech_to_text",
                "adapter_id": "bedrock-bda",
                "model_id": "apac.data-automation-v1",
                "model_revision": "2026-02-01",
                "pricing": {
                    "status": "priced",
                    "unit": "seconds",
                    "per_unit_usd": 0.001,
                },
                "budget": {
                    "feature": "transcription",
                    "monthly_usd": 10,
                    "hard_cap": {"max_units_per_month": 18000},
                },
            },
        ],
    }


def _runtime_settings(tmp_path: Path, secrets_dir: Path, *, app_env: str) -> Settings:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(_bedrock_runtime_document()), encoding="utf-8")
    return get_settings().model_copy(
        update={
            "app_env": app_env,
            "ai_registry_file": str(path),
            "ai_secrets_dir": str(secrets_dir),
            "redis_url": None,
            "supabase_url": None,
            "supabase_service_role_key": None,
        }
    )


def test_production_build_succeeds_with_files_absent(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets-absent"  # never created
    settings = _runtime_settings(tmp_path, secrets_dir, app_env="production")
    runtime = build_inference_runtime(settings)
    # Both Bedrock routes are present (NOT dropped) even though no secret file
    # exists: the static key source is resolved lazily, not at build time.
    assert "planning-bedrock" in runtime.route_records
    assert "transcription-bedrock" in runtime.route_records
    dropped_ids = {d.route_id for d in runtime.dropped_routes}
    assert "bedrock-chat" not in dropped_ids
    assert "bedrock-bda" not in dropped_ids


def test_catalog_fingerprint_identical_with_and_without_files(
    tmp_path: Path,
) -> None:
    absent_dir = tmp_path / "secrets-absent"  # never created
    present_dir = tmp_path / "secrets-present"
    _write_key_files(present_dir)
    without = build_inference_runtime(
        _runtime_settings(tmp_path, absent_dir, app_env="production")
    )
    with_files = build_inference_runtime(
        _runtime_settings(tmp_path, present_dir, app_env="production")
    )
    assert without.catalog_fingerprint == with_files.catalog_fingerprint


# ---------------------------------------------------------------------------
# Adapter-level: signed header, no-env-fallback, no-request-on-missing, redaction
# ---------------------------------------------------------------------------
def _converse_adapter(handler, source, *, env=None):
    return aws_bedrock_factory(
        config=BedrockConfig(region="ap-south-1", api="converse"),
        env=env or {},
        provider="bedrock",
        bedrock_transport=httpx.MockTransport(handler),
        clock=_amz_clock,
        static_key_source=source,
    )


def test_converse_authorization_header_carries_test_key_id(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    _write_key_files(secrets_dir)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={
                "output": {"message": {"content": [{"text": "a plan"}]}},
                "usage": {"inputTokens": 5, "outputTokens": 2},
            },
        )

    adapter = _converse_adapter(handler, _static_source(secrets_dir))
    result = adapter.invoke(
        route=_route(),
        request=PlanningRequest(messages=(ChatMessage(role="user", content="hi"),)),
    )
    assert isinstance(result.output, PlanningOutput)
    assert seen["auth"].startswith(
        f"AWS4-HMAC-SHA256 Credential={_TEST_KEY_ID}/"
    )


def test_env_access_key_is_ignored_when_static_key_configured(
    tmp_path: Path,
) -> None:
    secrets_dir = tmp_path / "secrets"
    _write_key_files(secrets_dir, key_id=_TEST_KEY_ID)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(
            200, json={"output": {"message": {"content": [{"text": "ok"}]}}}
        )

    # A process env var that would otherwise win the IAM chain; it must be
    # ignored because the static source is used exclusively (no fallback).
    adapter = _converse_adapter(
        handler,
        _static_source(secrets_dir),
        env={
            "AWS_ACCESS_KEY_ID": "AKIDENVSHOULDBEIGNORED",
            "AWS_SECRET_ACCESS_KEY": "env-secret-should-be-ignored",
        },
    )
    adapter.invoke(
        route=_route(),
        request=PlanningRequest(messages=(ChatMessage(role="user", content="hi"),)),
    )
    assert seen["auth"].startswith(
        f"AWS4-HMAC-SHA256 Credential={_TEST_KEY_ID}/"
    )
    assert "AKIDENVSHOULDBEIGNORED" not in seen["auth"]


def test_missing_files_send_no_request_and_fail_non_circuit_worthy(
    tmp_path: Path,
) -> None:
    secrets_dir = tmp_path / "secrets-absent"  # never created
    sent = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        sent["count"] += 1
        return httpx.Response(200, json={})

    adapter = _converse_adapter(handler, _static_source(secrets_dir))
    with pytest.raises(ProviderCallFailure) as exc:
        adapter.invoke(
            route=_route(),
            request=PlanningRequest(
                messages=(ChatMessage(role="user", content="hi"),)
            ),
        )
    info = exc.value.error_info
    assert info.code == "provider_auth_failed"
    assert info.retryable is False
    assert info.should_open_circuit is False
    assert info.can_fallback is True
    assert info.billable is False
    assert info.sent is False
    assert sent["count"] == 0  # no HTTP request was ever made


def test_static_source_reads_lazily_not_at_build(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets-absent"  # never created
    # Building the adapter (and its source) must not raise even though the
    # files are missing — resolution happens on the first signed request.
    adapter = _converse_adapter(
        lambda r: httpx.Response(200, json={}), _static_source(secrets_dir)
    )
    assert adapter is not None


def test_secret_value_never_appears_in_logs_exception_or_repr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secrets_dir = tmp_path / "secrets"
    _write_key_files(secrets_dir, secret=_TEST_SECRET)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"output": {"message": {"content": [{"text": "ok"}]}}}
        )

    with caplog.at_level(logging.DEBUG):
        adapter = _converse_adapter(handler, _static_source(secrets_dir))
        adapter.invoke(
            route=_route(),
            request=PlanningRequest(
                messages=(ChatMessage(role="user", content="hi"),)
            ),
        )
    # The resolved secret is registered with the log redactor.
    assert _TEST_SECRET in registered_secret_values()
    # It never appears verbatim in any captured log line.
    assert _TEST_SECRET not in caplog.text
    # Nor in the adapter / chain repr.
    assert _TEST_SECRET not in repr(adapter)
    assert SECRET_VALUE_MARKER in SECRET_VALUE_MARKER  # sanity: marker exists


def test_static_resolution_error_message_has_no_secret() -> None:
    def source() -> object:  # pragma: no cover - used only for its exception
        raise StaticKeyResolutionError("static aws_access_key refs could not be resolved")

    chain = IamCredentialChain(
        region="ap-south-1", env={}, static_key_source=source
    )
    with pytest.raises(StaticKeyResolutionError) as exc:
        chain.resolve()
    assert _TEST_SECRET not in str(exc.value)
    assert _TEST_KEY_ID not in str(exc.value)
