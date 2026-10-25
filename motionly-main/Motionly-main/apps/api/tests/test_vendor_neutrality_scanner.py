"""Unit tests for the vendor-neutrality scanner (scripts/verify/vendor_neutrality.py).

The scanner enforces plan Task 2: no audited vendor name (runway, openrouter,
groq, jina, google vision, pyannoteAI, lalal) and no direct openai/anthropic SDK
import may appear in active source. Docs, lock files, historical migrations and
explicitly allowlisted files are exempt; registry JSON is scanned per field.

These tests build small ``tmp_path`` trees and drive the pure helpers and the
``scan``/mode functions directly, so behaviour is verified without the real
repository, plus one smoke test that runs ``--report`` on the real tree and
asserts exit 0.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def vn():
    spec = importlib.util.spec_from_file_location(
        "motionly_vendor_neutrality", ROOT / "scripts" / "verify" / "vendor_neutrality.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Token boundaries
# ---------------------------------------------------------------------------
MUST_MATCH = [
    ("RunwayGen3Provider", "runway"),
    ("RUNWAY_API_KEY", "runway"),
    ("runway_task_id", "runway"),
    ("OpenRouterAudioEmotionProvider", "openrouter"),
    ("openrouter.ai", "openrouter"),
    ("GOOGLE_VISION_API_KEY", "google-vision"),
    ("google-vision-objects", "google-vision"),
    ("Google Cloud Vision", "google-vision"),
    ("PyannoteAIDiarizationProvider", "pyannoteai"),
    ("pyannoteai_diarization", "pyannoteai"),
    ("api.pyannote.ai", "pyannoteai"),
    ("lalal_stems", "lalal"),
    ("LALAL.AI", "lalal"),
    ("JinaEmbeddingsAdapter", "jina"),
    ("jina ai embeddings", "jina"),
    ("GROQ_API_KEY", "groq"),
]

MUST_NOT_MATCH = [
    "openai_compatible",
    "pyannote/speaker-diarization",
    "jinja",
    "groqish",
    "a pyannote local model",
    "runwayish_but_not",  # token runwayish != runway
]


@pytest.mark.parametrize(("text", "vendor"), MUST_MATCH)
def test_must_match(vn, text, vendor):
    found = dict(vn.match_vendors(text))
    assert vendor in found, (text, found)


@pytest.mark.parametrize("text", MUST_NOT_MATCH)
def test_must_not_match(vn, text):
    assert vn.match_vendors(text) == [], (text, vn.tokenize(text))


def test_tokenize_splits_camel_snake_and_digits(vn):
    assert vn.tokenize("RunwayGen3Provider") == ["runway", "gen", "3", "provider"]
    assert vn.tokenize("google-vision") == ["google", "vision"]
    assert vn.tokenize("openRouter") == ["open", "router"]
    assert vn.tokenize("api.pyannote.ai") == ["api", "pyannote", "ai"]


def test_bare_pyannote_is_not_a_finding(vn):
    # Bare 'pyannote' is a local model id (Task 14), only 'pyannote ai' matches.
    assert vn.match_vendors("pyannote") == []
    assert dict(vn.match_vendors("pyannoteAI")).get("pyannoteai")


# ---------------------------------------------------------------------------
# Allowlist glob matching and reasons
# ---------------------------------------------------------------------------
def test_allowlist_parsing_and_glob(vn, tmp_path):
    allow_file = tmp_path / "allow.txt"
    allow_file.write_text(
        "cutoff: 202610230301\n"
        "docs/** | docs | prose names vendors\n"
        "*.md | docs | markdown prose\n",
        encoding="utf-8",
    )
    allow = vn.load_allowlist(allow_file)
    assert allow.migration_cutoff == "202610230301"
    entry = allow.match("docs/guide/vendors.txt")
    assert entry is not None and entry.category == "docs"
    assert entry.reason == "prose names vendors"
    assert allow.match("README.md") is not None
    assert allow.match("apps/api/app/x.py") is None


def test_allowlist_entry_requires_reason(vn, tmp_path):
    allow_file = tmp_path / "allow.txt"
    allow_file.write_text("docs/** | docs | \n", encoding="utf-8")
    with pytest.raises(ValueError):
        vn.load_allowlist(allow_file)


def test_allowlisted_match_reports_category_and_reason(vn, tmp_path):
    _write(tmp_path, "docs/notes.txt", "we call the runway api\n")
    allow = _allow(tmp_path, ["docs/** | docs | prose names vendors"])
    result = vn.scan(tmp_path, allow)
    allowlisted = result.by_category(vn.ALLOWLISTED)
    assert len(allowlisted) == 1
    assert "docs: prose names vendors" in allowlisted[0].reason


# ---------------------------------------------------------------------------
# Historical-migration cutoff
# ---------------------------------------------------------------------------
def test_historical_migration_before_cutoff_is_separated(vn, tmp_path):
    _write(
        tmp_path,
        "supabase/migrations/202606210003_phase_5_scale.sql",
        "alter table add column runway_task_id text;\n",
    )
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    assert result.by_category(vn.ACTIVE) == []
    hist = result.by_category(vn.HISTORICAL)
    assert len(hist) == 1 and hist[0].vendor == "runway"


def test_migration_newer_than_cutoff_is_active(vn, tmp_path):
    _write(
        tmp_path,
        "supabase/migrations/202611010101_new_thing.sql",
        "-- references runway again\n",
    )
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    assert result.by_category(vn.HISTORICAL) == []
    active = result.by_category(vn.ACTIVE)
    assert len(active) == 1 and active[0].category == vn.ACTIVE


def test_exact_allowlist_entry_covers_one_new_migration_only(vn, tmp_path):
    # Plan Changelog rev 4 item 7: one named migration may carry the legacy
    # value; a glob never covers migrations, and other new migrations stay ACTIVE.
    named = "supabase/migrations/202611010101_backfill.sql"
    _write(tmp_path, named, "-- moves rows off runway\n")
    _write(tmp_path, "supabase/migrations/202611010102_other.sql", "-- runway again\n")
    allow = _allow(
        tmp_path,
        [
            f"{named} | migration | names the legacy value to move rows off it",
            "supabase/migrations/* | migration | a glob must not cover migrations",
        ],
    )
    result = vn.scan(tmp_path, allow)
    assert [f.path for f in result.by_category(vn.ALLOWLISTED)] == [named]
    assert [f.path for f in result.by_category(vn.ACTIVE)] == [
        "supabase/migrations/202611010102_other.sql"
    ]


# ---------------------------------------------------------------------------
# Registry field rule
# ---------------------------------------------------------------------------
def _registry_doc() -> dict:
    return {
        "version": 1,
        "adapters": [
            {
                "id": "audio-stems-endpoint",
                "base_url": "https://api.lalal.ai/v1",
                "provider": "lalal",
                "data_processor": {
                    "display_name": "LALAL.AI",
                    "policy_url": "https://lalal.ai/policy",
                },
            }
        ],
        "models": [{"id": "stems-model", "model_id": "lalal/andromeda-v6"}],
        "routes": [
            {
                "id": "audio-stems-remote",
                "adapter_id": "audio-stems-endpoint",
                "model_revision": "andromeda-v6",
            }
        ],
    }


def test_registry_allows_vendor_in_value_fields_and_processor(vn, tmp_path):
    _write(
        tmp_path,
        "infra/ai-registry/registry.json",
        json.dumps(_registry_doc(), indent=2),
    )
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    # base_url, provider, model_id and everything in data_processor are allowed;
    # ids/adapter_id/model_revision carry no vendor name here, so zero findings.
    assert result.by_category(vn.ACTIVE) == []


def test_registry_allows_vendor_in_offering_display_fields(vn, tmp_path):
    # Plan Section 2.11 (Task 29): offering.display_name and offering.description
    # are operator display data, like data_processor, so a vendor name there is
    # allowed. The route id/adapter_id/model_revision carry none, so zero
    # ACTIVE findings; a vendor token in another offering field (labels) is still
    # ACTIVE.
    doc = {
        "version": 1,
        "routes": [
            {
                "id": "planning-chat-premium",
                "adapter_id": "chat-endpoint",
                "model_revision": "2026-01-01",
                "offering": {
                    "selectable": True,
                    "display_name": "Runway premium planning",
                    "description": "Powered by Groq hardware",
                    "labels": ["quality"],
                },
            }
        ],
    }
    _write(tmp_path, "docs/ai-registry/examples/offering.json", json.dumps(doc))
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    assert result.by_category(vn.ACTIVE) == []


def test_registry_flags_vendor_in_offering_non_display_field(vn, tmp_path):
    # A vendor token outside the display fields (an object key, or a label) is
    # still ACTIVE even inside an offering block.
    doc = {
        "version": 1,
        "routes": [
            {
                "id": "clean-route",
                "adapter_id": "clean-endpoint",
                "model_revision": "v1",
                "offering": {"selectable": True, "labels": ["runway"]},
            }
        ],
    }
    _write(tmp_path, "docs/ai-registry/examples/offering-bad.json", json.dumps(doc))
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    active = result.by_category(vn.ACTIVE)
    assert {f.vendor for f in active} == {"runway"}
    assert active[0].pointer.endswith("/offering/labels/0")


def test_registry_flags_vendor_in_id_adapter_and_revision(vn, tmp_path):
    doc = {
        "version": 1,
        "routes": [
            {
                "id": "lalal-stem-separation",
                "adapter_id": "pyannoteai-diarization",
                "model_revision": "runway-gen3",
                "base_url": "https://api.groq.com",
            }
        ],
    }
    _write(tmp_path, "infra/ai-registry/bad.json", json.dumps(doc, indent=2))
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    active = result.by_category(vn.ACTIVE)
    vendors = {f.vendor for f in active}
    # id/adapter_id/model_revision are ACTIVE; base_url (groq) is allowed.
    assert "lalal" in vendors
    assert "pyannoteai" in vendors
    assert "runway" in vendors
    assert "groq" not in vendors
    for f in active:
        assert f.pointer.startswith("/routes/0/")


def test_registry_allows_vendor_in_artifacts_allowed_hosts(vn, tmp_path):
    # Changelog rev 4 item 8: a host string under mapping.artifacts.allowed_hosts
    # is operator configuration, like base_url, so a vendor host is allowed.
    doc = {
        "version": 1,
        "routes": [
            {
                "id": "audio-stems-remote",
                "adapter_id": "audio-stems-endpoint",
                "model_revision": "andromeda-v6",
                "mapping": {
                    "artifacts": {
                        "allowed_hosts": [
                            "api.lalal.ai",
                            "cdn.lalal.ai",
                            "*.files.lalal.ai",
                        ]
                    }
                },
            }
        ],
    }
    _write(tmp_path, "docs/ai-registry/examples/stem-separation.json", json.dumps(doc))
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    # The host strings carry a vendor token but are configuration data; the
    # id/adapter_id/model_revision carry none, so there are zero findings.
    assert result.by_category(vn.ACTIVE) == []


def test_registry_allowed_hosts_does_not_leak_to_other_lists(vn, tmp_path):
    # The allowance is keyed on the ``allowed_hosts`` key only; a vendor token in
    # a different list (``required``) is still ACTIVE.
    doc = {
        "version": 1,
        "routes": [
            {
                "id": "clean-route",
                "adapter_id": "clean-endpoint",
                "model_revision": "v1",
                "mapping": {
                    "artifacts": {
                        "allowed_hosts": ["api.example.invalid"],
                        "required": ["lalal-track"],
                    }
                },
            }
        ],
    }
    _write(tmp_path, "docs/ai-registry/examples/x.json", json.dumps(doc))
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    active = result.by_category(vn.ACTIVE)
    assert {f.vendor for f in active} == {"lalal"}
    assert active[0].pointer.endswith("/mapping/artifacts/required/0")


def test_registry_rule_overrides_docs_allowance(vn, tmp_path):
    # A registry file placed under docs/ai-registry is still field-scanned, so a
    # vendor id is ACTIVE despite the docs/** allowance.
    doc = {"version": 1, "routes": [{"id": "runway-video"}]}
    _write(tmp_path, "docs/ai-registry/r.json", json.dumps(doc))
    allow = _allow(tmp_path, ["docs/** | docs | prose"])
    result = vn.scan(tmp_path, allow)
    active = result.by_category(vn.ACTIVE)
    assert len(active) == 1 and active[0].vendor == "runway"


def test_non_registry_json_under_docs_follows_docs_rules(vn, tmp_path):
    # A scenario fixture (no version/adapters/routes/models) is normal JSON and
    # under docs/** it is allowlisted, not field-scanned.
    doc = {"scenario": "runway render", "steps": ["call runway"]}
    _write(tmp_path, "docs/ai-registry/scenario.json", json.dumps(doc))
    allow = _allow(tmp_path, ["docs/** | docs | prose"])
    result = vn.scan(tmp_path, allow)
    assert result.by_category(vn.ACTIVE) == []
    assert result.by_category(vn.ALLOWLISTED)


# ---------------------------------------------------------------------------
# Generated files are scanned
# ---------------------------------------------------------------------------
def test_generated_files_are_scanned(vn, tmp_path):
    _write(
        tmp_path,
        "packages/shared/src/generated/api.ts",
        "export type X = { runwayTaskId: string };\n",
    )
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    active = result.by_category(vn.ACTIVE)
    assert len(active) == 1 and active[0].vendor == "runway"


# ---------------------------------------------------------------------------
# Enforce
# ---------------------------------------------------------------------------
def test_enforce_fails_on_forbidden_string_in_core_path(vn, tmp_path, capsys):
    _write(
        tmp_path,
        "apps/api/app/services/x.py",
        "RUNWAY_API_KEY = get_secret()\n",
    )
    allow_file = tmp_path / "scripts" / "verify" / "vendor-neutrality-allowlist.txt"
    allow_file.parent.mkdir(parents=True, exist_ok=True)
    allow_file.write_text("cutoff: 202610230301\n", encoding="utf-8")
    code = vn.main(["--enforce", "--root", str(tmp_path)])
    assert code == 1
    assert "ENFORCE FAILED" in capsys.readouterr().out


def test_enforce_passes_on_clean_tree(vn, tmp_path, capsys):
    _write(tmp_path, "apps/api/app/services/x.py", "value = compute()\n")
    allow_file = tmp_path / "scripts" / "verify" / "vendor-neutrality-allowlist.txt"
    allow_file.parent.mkdir(parents=True, exist_ok=True)
    allow_file.write_text("cutoff: 202610230301\n", encoding="utf-8")
    code = vn.main(["--enforce", "--root", str(tmp_path)])
    assert code == 0
    assert "ENFORCE OK" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# SDK import rule
# ---------------------------------------------------------------------------
def test_sdk_import_rule_flags_openai_and_anthropic(vn, tmp_path):
    _write(tmp_path, "apps/api/app/services/a.py", "import openai\n")
    _write(tmp_path, "apps/api/app/services/b.py", "from anthropic import Client\n")
    # openai_compatible as a token is not a vendor and 'import openai_x' is not
    # the SDK; a from-import of a submodule still counts.
    _write(tmp_path, "apps/api/app/services/c.py", "from openai.types import X\n")
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    paths = {f.path for f in result.by_category(vn.ACTIVE) if f.vendor == "sdk-import"}
    assert paths == {
        "apps/api/app/services/a.py",
        "apps/api/app/services/b.py",
        "apps/api/app/services/c.py",
    }


def test_sdk_import_rule_scoped_to_apps_api_app(vn, tmp_path):
    _write(tmp_path, "scripts/tool.py", "import openai\n")
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    assert [f for f in result.by_category(vn.ACTIVE) if f.vendor == "sdk-import"] == []


def test_openai_compatible_is_not_a_finding(vn, tmp_path):
    _write(tmp_path, "apps/api/app/services/x.py", 'adapter = "openai_compatible"\n')
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    assert result.by_category(vn.ACTIVE) == []


# ---------------------------------------------------------------------------
# Ratchet
# ---------------------------------------------------------------------------
def test_ratchet_fails_when_count_rises_and_passes_when_it_falls(vn, tmp_path):
    _write(tmp_path, "apps/api/app/services/x.py", "runway\nrunway\n")
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    assert result.active_counts() == {"apps/api/app/services/x.py": 2}

    # Baseline recorded 2; a rise to 3 fails, a fall to 1 passes, a new file fails.
    rose = vn.run_ratchet(result, {"apps/api/app/services/x.py": 1})
    assert rose[0] == 1 and "increased" in rose[1]

    fell = vn.run_ratchet(result, {"apps/api/app/services/x.py": 5})
    assert fell[0] == 0 and "fell" in fell[1]

    new_file = vn.run_ratchet(result, {})
    assert new_file[0] == 1 and "New files" in new_file[1]

    same = vn.run_ratchet(result, {"apps/api/app/services/x.py": 2})
    assert same[0] == 0


def test_update_baseline_is_deterministic_and_sorted(vn, tmp_path):
    _write(tmp_path, "apps/api/app/b.py", "runway\n")
    _write(tmp_path, "apps/api/app/a.py", "groq\ngroq\n")
    allow = _allow(tmp_path, [])
    result = vn.scan(tmp_path, allow)
    baseline = vn.build_baseline(result)
    assert list(baseline["active_counts"]) == sorted(baseline["active_counts"])
    assert baseline["active_counts"] == {"apps/api/app/a.py": 2, "apps/api/app/b.py": 1}


# ---------------------------------------------------------------------------
# Live smoke test
# ---------------------------------------------------------------------------
def test_report_on_real_repo_exits_zero(vn, capsys):
    code = vn.main(["--report", "--root", str(ROOT)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Vendor-neutrality scan" in out
    assert "Summary by category" in out


def test_ratchet_on_real_repo_exits_zero(vn, capsys):
    code = vn.main(["--ratchet", "--root", str(ROOT)])
    assert code == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _allow(root: Path, entries: list[str]):
    """Build an Allowlist for a tmp tree with the standard cutoff."""
    spec = importlib.util.spec_from_file_location(
        "motionly_vendor_neutrality", ROOT / "scripts" / "verify" / "vendor_neutrality.py"
    )
    assert spec and spec.loader
    module = sys.modules.get("motionly_vendor_neutrality")
    assert module is not None
    allow_file = root / "scripts" / "verify" / "vendor-neutrality-allowlist.txt"
    allow_file.parent.mkdir(parents=True, exist_ok=True)
    allow_file.write_text(
        "cutoff: 202610230301\n" + "\n".join(entries) + "\n", encoding="utf-8"
    )
    return module.load_allowlist(allow_file)
