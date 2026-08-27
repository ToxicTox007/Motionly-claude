"""Release-only Compose integration workflow contract."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "supply-chain.yml"


def test_full_compose_integration_is_release_tag_only_and_always_cleans_up() -> None:
    workflow = WORKFLOW_PATH.read_text()

    assert "release-compose-integration:" in workflow
    assert "if: github.ref_type == 'tag' && startsWith(github.ref_name, 'v')" in workflow
    assert "runs-on: [self-hosted, linux, x64, gpu]" in workflow
    assert "needs: [release-manifest]" in workflow
    assert "write_compose_test_env.py" in workflow
    assert "docker compose --env-file" in workflow
    assert "up -d --wait --wait-timeout 240 --remove-orphans" in workflow
    assert "release-compose-logs-${{ github.sha }}" in workflow
    assert "if: always()" in workflow
    assert "down -v --remove-orphans || true" in workflow
    assert 'rm -rf "$RELEASE_INPUT_ROOT"' in workflow
