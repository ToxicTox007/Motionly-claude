"""Zero-reference assertion for the removed Lightning AI integration.

Task 8 requires that Lightning AI has no remaining presence in Motionly's
runtime code, configuration, deployment definitions, or tests. This test scans
the application tree and fails if any case-insensitive ``lightning`` token
survives.

The only allowed occurrence is ``lightningcss`` — an unrelated CSS toolchain
dependency pulled in transitively by the web app — which is stripped before the
check. Generated lockfiles, virtualenvs, caches, build artifacts, secret
``.env`` files, and this test file itself are excluded from the scan.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SELF = Path(__file__).resolve()

SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    ".venv",
    ".turbo",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".next",
    ".screenshots",
    "test-results",
    "test-storage",
    "dist",
    "build",
    ".temp",
    ".branches",
}

SKIP_FILE_NAMES = {
    "pnpm-lock.yaml",
    ".env",
}

SKIP_SUFFIXES = {
    ".tsbuildinfo",
    ".zst",
    ".tar",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".pyc",
}


def _iter_scan_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() == SELF:
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.name in SKIP_FILE_NAMES or path.suffix == ".lock":
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def test_no_lightning_references_remain_in_runtime_config_or_tests():
    offenders = []
    for path in _iter_scan_files():
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        sanitized = content.lower().replace("lightningcss", "")
        if "lightning" in sanitized:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"Lightning references still present in: {sorted(offenders)}"


def test_lightning_service_module_is_deleted():
    assert not (ROOT / "apps/api/app/services/lightning_scaling.py").exists()


def test_compose_replaces_retired_gpu_deployment_scripts() -> None:
    compose = (ROOT / "infra" / "docker-compose.gpu.yml").read_text(encoding="utf-8")

    for service in (
        "cpu-ffmpeg-worker",
        "gpu-inference-worker",
        "gpu-ffmpeg-worker",
        "matte-worker",
        "demucs-worker",
        "provider-worker",
        "planning-worker",
        "remotion-worker",
        "publishing-worker",
    ):
        assert f"  {service}:" in compose
    assert "stock_asset_enrichment" in compose
    assert "render_gpu" in compose
    assert not (ROOT / "scripts" / "deploy_gpu_workers.sh").exists()
    assert not (ROOT / "scripts" / "gpu_start_worker.sh").exists()


def test_provider_neutral_compute_backend_exists():
    assert (ROOT / "apps/api/app/services/compute_backend.py").exists()
