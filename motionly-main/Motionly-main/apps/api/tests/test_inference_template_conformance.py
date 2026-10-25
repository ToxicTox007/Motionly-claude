"""Conformance suite for the Task 17 template ports.

Parametrized over every scenario found by glob (plus the harness smoke example).
For each scenario it runs the template side through the real gateway and checks
the scenario's ``expect`` block. It also asserts the registry-example invariants
Task 17 requires:

* each ported capability that has scenarios has at least one example registry
  route for it;
* every example registry document validates with the CLI rules in production
  mode (0 errors; warnings are allowed and listed);
* no example route id, adapter id or model_revision contains a vendor token
  (reusing the Task 2 scanner's matcher, imported the way the scanner test
  imports it);
* each ported capability has scenarios for at least two differently-shaped
  providers (two distinct example registry documents).

Until the three capability agents add scenarios and example documents, the
per-capability assertions skip cleanly (not fail); they activate automatically
once files appear. The harness smoke example proves the runner works today.

This file contains no vendor names.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.support import template_scenarios as ts

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "docs" / "ai-registry" / "examples"

# The three capabilities Task 17 ports. Each becomes active once its agent adds
# scenarios and example documents.
PORTED_CAPABILITIES = tuple(ts.CAPABILITY_SLUGS)


# ---------------------------------------------------------------------------
# Scenario parametrization (glob + smoke)
# ---------------------------------------------------------------------------
def _all_scenarios() -> list[Path]:
    return [*ts.discover_scenarios(), *ts.discover_smoke_scenarios()]


def _scenario_id(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return path.name


@pytest.mark.parametrize("scenario_path", _all_scenarios(), ids=_scenario_id)
def test_scenario_conformance(scenario_path: Path, tmp_path: Path) -> None:
    scenario = ts.load_scenario(scenario_path)
    observation = ts.run_template_scenario(scenario_path, tmp_path=tmp_path)
    problems = ts.check_expectations(scenario, observation)
    assert not problems, "scenario expectations failed:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# The scanner matcher, imported the way the scanner test imports it.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def vn():
    spec = importlib.util.spec_from_file_location(
        "motionly_vendor_neutrality",
        REPO_ROOT / "scripts" / "verify" / "vendor_neutrality.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_document(path: Path):
    from app.services.inference_registry import load_registry_document

    document, diagnostics = load_registry_document(path)
    return document, diagnostics


def _example_documents() -> list[Path]:
    if not EXAMPLES_DIR.exists():
        return []
    return sorted(p for p in EXAMPLES_DIR.glob("*.json") if p.is_file())


def _example_documents_for(capability: str) -> list[Path]:
    """Example registry documents that declare a route for ``capability``."""
    matches: list[Path] = []
    for path in _example_documents():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        routes = document.get("routes", []) if isinstance(document, dict) else []
        if any(r.get("capability") == capability for r in routes):
            matches.append(path)
    return matches


# ---------------------------------------------------------------------------
# Every example document validates in production mode (0 errors).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "example_path", _example_documents(), ids=lambda p: p.name
)
def test_example_registry_validates_in_production(example_path: Path) -> None:
    from app.services.inference_registry import validate_registry
    from app.services.inference_templates import validate_mapping

    document, load_diagnostics = _load_document(example_path)
    assert document is not None, (
        f"{example_path.name} failed to load: "
        + "; ".join(f"{d.code}: {d.message}" for d in load_diagnostics)
    )
    validation = validate_registry(
        document,
        app_env="production",
        gpu_workers_enabled=True,
        block_noncommercial=False,
        mapping_validator=validate_mapping,
    )
    errors = [f"{d.object_id}:{d.code}: {d.message}" for d in validation.errors()]
    assert not errors, f"{example_path.name} has validation errors:\n" + "\n".join(errors)
    # Warnings are allowed, but surface them so a reviewer sees them.
    warnings = [f"{d.object_id}:{d.code}" for d in validation.warnings()]
    if warnings:
        print(f"{example_path.name} warnings (allowed): {warnings}")


# ---------------------------------------------------------------------------
# No vendor token in id / adapter_id / model_revision of any example route.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "example_path", _example_documents(), ids=lambda p: p.name
)
def test_example_ids_have_no_vendor_token(example_path: Path, vn) -> None:
    document = json.loads(example_path.read_text(encoding="utf-8"))
    offenders: list[str] = []

    def check(field: str, value: object) -> None:
        if isinstance(value, str) and vn.match_vendors(value):
            offenders.append(f"{field}={value!r} -> {dict(vn.match_vendors(value))}")

    for adapter in document.get("adapters", []):
        check("adapter.id", adapter.get("id"))
    for model in document.get("models", []):
        check("model.id", model.get("id"))
        check("model.revision", model.get("revision"))
    for route in document.get("routes", []):
        check("route.id", route.get("id"))
        check("route.adapter_id", route.get("adapter_id"))
        check("route.model_revision", route.get("model_revision"))
    assert not offenders, (
        f"{example_path.name} has vendor tokens in id/revision fields:\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Per-capability activation gates (skip cleanly until an agent lands files).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("capability", PORTED_CAPABILITIES)
def test_capability_has_example_route(capability: str) -> None:
    if not ts.discover_scenarios(capability):
        pytest.skip(f"{capability}: no scenarios yet (capability not ported)")
    documents = _example_documents_for(capability)
    assert documents, f"{capability}: no example registry route found in docs/ai-registry/examples"


@pytest.mark.parametrize("capability", PORTED_CAPABILITIES)
def test_capability_has_two_shaped_providers(capability: str) -> None:
    scenarios = ts.discover_scenarios(capability)
    if not scenarios:
        pytest.skip(f"{capability}: no scenarios yet (capability not ported)")
    documents = _example_documents_for(capability)
    assert len(documents) >= 2, (
        f"{capability}: needs scenarios for at least two differently-shaped "
        f"providers (two example registry documents); found {len(documents)}: "
        f"{[p.name for p in documents]}"
    )


def test_harness_smoke_scenario_present() -> None:
    # The harness itself is exercised now, before any capability agent lands.
    smoke = ts.discover_smoke_scenarios()
    assert smoke, "the harness smoke scenario is missing"
