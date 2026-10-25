"""The Tier 1 gate (plan Task 27): scripts/verify/tier1_gate.py.

The gate runs the plan's required checks and classifies every finding as Task 19
item 2 material (``gated``) or ``unexpected``. These tests plant small trees and
check the three outcomes (pass, gated only, unexpected), and keep the real gated
list honest: every entry must still exist and carry a reason.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location(
        "motionly_tier1_gate", ROOT / "scripts" / "verify" / "tier1_gate.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(tmp_path: Path, *, gated_paths: list[str]) -> Path:
    _write(
        tmp_path,
        "scripts/verify/vendor-neutrality-allowlist.txt",
        "cutoff: 202610230301\ndocs/** | docs | prose\n"
        "scripts/verify/tier1-gated-items.json | scanner | gate data\n",
    )
    _write(
        tmp_path,
        "scripts/verify/tier1-gated-items.json",
        json.dumps(
            {
                "vendors": ["lalal"],
                "env_tokens": ["LALAL"],
                "paths": [{"path": p, "reason": "test"} for p in gated_paths],
            }
        ),
    )
    _write(tmp_path, "apps/api/app/services/neutral.py", "def plan():\n    return 1\n")
    _write(tmp_path, "docs/notes.md", "Mentions runway and lalal in prose.\n")
    return tmp_path


def test_clean_tree_passes(gate, tmp_path: Path) -> None:
    root = _tree(tmp_path, gated_paths=[])
    results = gate.run_gate(root, skip_pytest=True)
    text_checks = results[:4]
    assert [r.status for r in text_checks] == ["PASS"] * 4
    # Skipping the tests never lets the gate pass.
    assert gate.exit_code(results) == 1
    assert gate.exit_code(text_checks) == 0


def test_gated_material_only_blocks_with_exit_2(gate, tmp_path: Path) -> None:
    root = _tree(tmp_path, gated_paths=["apps/api/app/services/stems_vendor.py"])
    _write(
        root,
        "apps/api/app/services/stems_vendor.py",
        'LALAL_BASE_URL = "https://www.lalal.ai/api/v1"\n',
    )
    results = gate.run_gate(root, skip_pytest=True)[:4]
    assert {r.status for r in results} == {"GATED", "PASS"}
    assert all(f.classification == gate.GATED for r in results for f in r.findings)
    assert gate.exit_code(results) == 2


def test_vendor_outside_the_gated_list_is_unexpected(gate, tmp_path: Path) -> None:
    root = _tree(tmp_path, gated_paths=["apps/api/app/services/stems_vendor.py"])
    # Same gated vendor, but in a file the list does not name.
    _write(root, "apps/api/app/services/other.py", 'KEY = "LALAL_LICENSE_KEY"\n')
    # A non-gated vendor anywhere is unexpected.
    _write(root, "infra/compose.yml", "RUNWAY_API_KEY: x\n")
    results = gate.run_gate(root, skip_pytest=True)[:4]
    unexpected = {(f.path, f.token) for r in results for f in r.unexpected}
    assert ("apps/api/app/services/other.py", "LALAL") in unexpected
    assert ("infra/compose.yml", "RUNWAY") in unexpected
    assert gate.exit_code(results) == 1


def test_old_payload_keys_and_sdk_imports_are_never_gated(gate, tmp_path: Path) -> None:
    root = _tree(tmp_path, gated_paths=["apps/api/app/services/legacy.py"])
    _write(
        root,
        "apps/api/app/services/legacy.py",
        'import openai\npayload = {"runway_task_id": 1}\n',
    )
    results = {r.name: r for r in gate.run_gate(root, skip_pytest=True)[:4]}
    assert results["old payload keys"].unexpected
    assert results["vendor SDK imports"].unexpected


def test_lock_files_and_ignored_patterns_are_excluded(gate, tmp_path: Path) -> None:
    root = _tree(tmp_path, gated_paths=[])
    _write(root, "apps/web/pnpm.lock", "RUNWAY\n")
    _write(root, "apps/api/app/__pycache__/x.py", "GROQ\n")
    env = {r.name: r for r in gate.run_gate(root, skip_pytest=True)[:4]}
    assert env["vendor environment and secret names"].findings == []


def test_real_gated_list_is_absent(gate) -> None:
    """Task 19 item 2 is complete: the gated-item data file is deleted, not
    emptied. ``load_gated_items`` then returns an empty :class:`GatedItems`, so
    nothing is ever classified ``gated`` against the real tree and the gate turns
    unexpected findings into failures (exit 1)."""
    assert not (ROOT / "scripts" / "verify" / "tier1-gated-items.json").exists(), (
        "scripts/verify/tier1-gated-items.json must be deleted when Task 19 item 2 lands"
    )
    empty = gate.load_gated_items(ROOT)
    assert empty.vendors == frozenset()
    assert empty.env_tokens == frozenset()
    assert empty.paths == ()
