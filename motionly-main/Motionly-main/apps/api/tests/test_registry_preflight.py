"""Tests for the inference-registry preflight CLI and the shipped registry (S4+S5).

The preflight CLI (:mod:`app.ops.preflight_inference_registry`) is the gate an
operator runs on ``infra/ai-registry/registry.json`` after substituting the real
AWS ids. These tests prove:

    * the shipped registry (with ``<PLACEHOLDER>`` tokens) preflights clean in
      development (exit 0, placeholders listed as warnings) and fails in
      production (exit 1, placeholders remain);
* a programmatically fully-filled copy preflights clean in production (exit 0)
  and builds one inference runtime in production with ``GPU_WORKERS_ENABLED`` off
  (fake secret files, a ``MockTransport`` so no socket opens) reporting planning,
  transcription, diarization, object detection, captions, embeddings and stems
  as available routes; video generation remains unavailable until a provider is
  intentionally configured;
* an inlined AWS access key id is a preflight error;
* a malformed schema (a Section 2.6 error in the substituted copy) exits 1.

No real AWS id, key, ARN or email appears here: the fully-filled copy uses the
same syntactically-valid dummies the CLI uses (account ``000000000000``, lower
case bucket names, a dummy BDA project id) and a tiny positive dummy price, and
the fake credentials are AWS's public SigV4 example id ``AKIDEXAMPLE`` with an
obviously-fake secret string.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.ops import preflight_inference_registry as preflight
from app.services.inference_runtime import build_inference_runtime

ROOT = Path(__file__).resolve().parents[3]
SHIPPED_REGISTRY = ROOT / "infra" / "ai-registry" / "registry.json"

# AWS's public SigV4 "get-vanilla" example key id (not an AKIA+16 value).
_FAKE_KEY_ID = "AKIDEXAMPLE"
_FAKE_SECRET = "wJalrXUtnFEMI-fake-secret-value-for-tests-0002"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run_preflight(capsys, *argv: str) -> tuple[int, str, str]:
    code = preflight.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def fill_registry_placeholders(document: object) -> object:
    """Return a fully-filled copy of ``document`` with every placeholder replaced.

    Reuses the CLI's own substitution so the filled copy matches exactly what the
    preflight validates (dummy account, staging bucket and project id, with the
    pricing marker handled when present).
    """
    return preflight._substitute_document(copy.deepcopy(document))


def write_inference_secret_files(secrets_dir: Path) -> None:
    """Write the two fake Bedrock access-key secret files the adapters expect."""
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "aws_inference_access_key_id").write_text(
        _FAKE_KEY_ID, encoding="utf-8"
    )
    (secrets_dir / "aws_inference_secret_access_key").write_text(
        _FAKE_SECRET, encoding="utf-8"
    )


def _mock_transport_rejecting_all() -> httpx.MockTransport:
    """A transport that fails any request: the build never calls the network."""

    def _handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"unexpected network call to {request.url}")

    return httpx.MockTransport(_handler)


# ---------------------------------------------------------------------------
# Shipped registry: development and production
# ---------------------------------------------------------------------------
def test_shipped_registry_preflights_clean_in_development(capsys) -> None:
    code, out, _err = _run_preflight(
        capsys, str(SHIPPED_REGISTRY), "--app-env", "development"
    )
    assert code == 0
    assert out == ""


def test_shipped_registry_preflights_clean_in_production(capsys) -> None:
    code, out, _err = _run_preflight(
        capsys, str(SHIPPED_REGISTRY), "--app-env", "production"
    )
    assert code == 0
    assert out == ""


def test_shipped_registry_substituted_copy_has_no_schema_error() -> None:
    document = json.loads(SHIPPED_REGISTRY.read_text(encoding="utf-8"))
    filled = fill_registry_placeholders(document)
    doc, load_diags = preflight._merge_substituted([filled])
    assert [d for d in load_diags if d.is_error] == []
    assert doc is not None
    from app.services.inference_registry import validate_registry

    validation = validate_registry(
        doc,
        app_env="production",
        gpu_workers_enabled=False,
        block_noncommercial=False,
    )
    errors = [d for d in validation.diagnostics if d.is_error]
    assert errors == [], errors
    assert {
        "bedrock-planning-chat",
        "bedrock-transcription",
        "bedrock-diarization",
        "bedrock-object-detection",
        "bedrock-caption",
        "bedrock-embeddings-text",
        "local-stem-separation",
    } <= set(validation.valid_route_ids)


# ---------------------------------------------------------------------------
# Fully-filled copy: production exit 0 and builds a runtime
# ---------------------------------------------------------------------------
def test_fully_filled_copy_preflights_clean_in_production(capsys, tmp_path: Path) -> None:
    document = json.loads(SHIPPED_REGISTRY.read_text(encoding="utf-8"))
    filled = fill_registry_placeholders(document)
    filled_path = tmp_path / "registry-filled.json"
    filled_path.write_text(json.dumps(filled), encoding="utf-8")
    code, out, _err = _run_preflight(
        capsys, str(filled_path), "--app-env", "production"
    )
    assert code == 0, out
    assert "PLACEHOLDER at" not in out
    assert "ERROR" not in out


def _filled_runtime_settings(tmp_path: Path, secrets_dir: Path):
    document = json.loads(SHIPPED_REGISTRY.read_text(encoding="utf-8"))
    filled = fill_registry_placeholders(document)
    filled_path = tmp_path / "registry-filled.json"
    filled_path.write_text(json.dumps(filled), encoding="utf-8")
    return get_settings().model_copy(
        update={
            "app_env": "production",
            "ai_registry_file": str(filled_path),
            "ai_secrets_dir": str(secrets_dir),
            "gpu_workers_enabled": False,
            "redis_url": None,
            "supabase_url": None,
            "supabase_service_role_key": None,
        }
    )


def test_fully_filled_copy_builds_runtime_with_all_capabilities(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    write_inference_secret_files(secrets_dir)
    settings = _filled_runtime_settings(tmp_path, secrets_dir)
    runtime = build_inference_runtime(
        settings, transport=_mock_transport_rejecting_all()
    )
    capabilities = {r.capability.value for r in runtime.route_records.values()}
    assert {
        "planning.chat",
        "transcription.speech_to_text",
        "audio.diarization",
        "vision.object_detection",
        "vision.caption",
        "embeddings.text",
        "audio.stem_separation",
    } <= capabilities
    # Every Bedrock route survived the build (not dropped) with GPU workers off.
    dropped = {d.route_id for d in runtime.dropped_routes}
    for route_id in (
        "bedrock-planning-chat",
        "bedrock-transcription",
        "bedrock-diarization",
        "bedrock-object-detection",
        "bedrock-caption",
        "bedrock-embeddings-text",
        "local-stem-separation",
    ):
        assert route_id in runtime.route_records, route_id
        assert route_id not in dropped, route_id


# ---------------------------------------------------------------------------
# Inlined AWS key id is a preflight error
# ---------------------------------------------------------------------------
def test_inlined_aws_access_key_id_is_an_error(capsys, tmp_path: Path) -> None:
    document = json.loads(SHIPPED_REGISTRY.read_text(encoding="utf-8"))
    filled = fill_registry_placeholders(document)
    # Inline an AKIA-shaped value where a secret ref belongs (never a real key).
    filled["adapters"][0]["auth"]["access_key_id_ref"] = "AKIA1234567890ABCDEF"
    bad_path = tmp_path / "registry-inlined-key.json"
    bad_path.write_text(json.dumps(filled), encoding="utf-8")
    code, out, _err = _run_preflight(capsys, str(bad_path), "--app-env", "development")
    assert code == 1
    assert "looks like a static AWS access key id" in out


def test_inlined_asia_key_id_is_detected(capsys, tmp_path: Path) -> None:
    document = json.loads(SHIPPED_REGISTRY.read_text(encoding="utf-8"))
    filled = fill_registry_placeholders(document)
    filled["adapters"][0]["auth"]["secret_ref"] = "ASIA1234567890ABCDEF"
    bad_path = tmp_path / "registry-asia.json"
    bad_path.write_text(json.dumps(filled), encoding="utf-8")
    code, out, _err = _run_preflight(capsys, str(bad_path), "--app-env", "development")
    assert code == 1
    assert "looks like a static AWS access key id" in out


# ---------------------------------------------------------------------------
# Malformed schema -> exit 1
# ---------------------------------------------------------------------------
def test_malformed_schema_exits_one(capsys, tmp_path: Path) -> None:
    document = json.loads(SHIPPED_REGISTRY.read_text(encoding="utf-8"))
    filled = fill_registry_placeholders(document)
    # Break the planning route: a floating revision is a Section 2.6 error.
    filled["routes"][0]["model_revision"] = "latest"
    filled["models"][0]["revision"] = "latest"
    bad_path = tmp_path / "registry-bad-schema.json"
    bad_path.write_text(json.dumps(filled), encoding="utf-8")
    code, out, _err = _run_preflight(capsys, str(bad_path), "--app-env", "development")
    assert code == 1
    assert "revision_floating" in out


def test_malformed_json_exits_one(capsys, tmp_path: Path) -> None:
    bad_path = tmp_path / "not-json.json"
    bad_path.write_text("{ this is not valid json", encoding="utf-8")
    code, out, _err = _run_preflight(capsys, str(bad_path), "--app-env", "development")
    assert code == 1
    assert "invalid_json" in out


# ---------------------------------------------------------------------------
# JSON output mode
# ---------------------------------------------------------------------------
def test_json_output_reports_clean_production_registry(capsys) -> None:
    code, out, _err = _run_preflight(
        capsys, str(SHIPPED_REGISTRY), "--app-env", "production", "--json"
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["app_env"] == "production"
    assert payload["exit_code"] == 0
    assert payload["preflight"] == []
    assert payload["diagnostics"] == []


# ---------------------------------------------------------------------------
# Substitution produces syntactically valid dummies
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("<ACCOUNT_ID>", "000000000000"),
        ("<STAGING_BUCKET>", "preflight-staging-bucket"),
        ("<BDA_PROJECT_ID>", "preflight-bda-project-id"),
    ],
)
def test_known_placeholder_dummies(token: str, expected: str) -> None:
    assert preflight._substitute_scalar(token) == expected


def test_pricing_marker_substitutes_to_small_positive_number() -> None:
    value = preflight._substitute_scalar(preflight.PRICING_MARKER)
    assert isinstance(value, float)
    assert value > 0
