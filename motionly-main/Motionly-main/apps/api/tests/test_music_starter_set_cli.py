"""Task 28: tests for the starter-set CLI (template / checklist / validate)."""

from __future__ import annotations

import json
from pathlib import Path

from app.ops.music_starter_set import run


def test_cli_template_writes_blank_manifest(tmp_path: Path) -> None:
    out = tmp_path / "candidates.json"
    assert run(["template", "--count", "30", "--out", str(out)]) == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert len(manifest["tracks"]) == 30
    assert manifest["tracks"][0]["provider_track_id"] == ""


def test_cli_checklist_writes_markdown(tmp_path: Path) -> None:
    out = tmp_path / "signoff.md"
    assert run(["checklist", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "sign-off checklist" in text.lower()


def test_cli_validate_rejects_empty_template(tmp_path: Path) -> None:
    out = tmp_path / "candidates.json"
    run(["template", "--out", str(out)])
    # The blank template must not pass validation (no real track yet).
    assert run(["validate", str(out)]) == 1


def test_cli_unknown_command_is_usage_error() -> None:
    assert run(["frobnicate"]) == 2
    assert run([]) == 2
