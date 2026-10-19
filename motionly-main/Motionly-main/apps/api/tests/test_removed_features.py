"""Removed features stay removed (Task 4).

Motionly has no social publishing (users play, download, share and export
instead), no performance insights, no Google sign-in, and no external error
reporter (Sentry/GlitchTip). These tests fail if a runtime surface for any of
them comes back: API routes, modules, settings, the ``publish`` queue, the
publishing worker service and image, its secrets, web UI, dependencies, or
configuration. The database objects are dropped by a forward migration and
checked by ``supabase/tests/task_4_removed_features_test.sql``.

Localized publish packages (translated captions and title/description
suggestions for exports) are an export feature and are not affected.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import yaml

from app.core.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[3]
SELF = Path(__file__).resolve()
WEB = ROOT / "apps" / "web"

REMOVED_MODULES = (
    "app.api.publishing",
    "app.repositories.publishing",
    "app.schemas.publishing",
    "app.services.publishing",
    "app.services.token_cipher",
    "app.api.performance_feedback",
    "app.repositories.performance_feedback",
    "app.schemas.performance_feedback",
    "app.services.performance_feedback",
)

# Compared after lowercasing and removing "-" and "_", so PublishJob,
# publish_job and PUBLISH-JOB all match.
REMOVED_TOKENS = (
    "sentry",
    "glitchtip",
    "publishjob",
    "publishconnection",
    "publishingworker",
    "socialpublishing",
    "publishoauth",
    "publishtoken",
    "publishsynccompletion",
    "performancefeedback",
    "captureperformancemetric",
    "loginwithgoogle",
    "googleoauth",
    "signinwithoauth",
)

# Files that name a removed token only to assert it stays absent, or (the
# migration coverage contract) to check that the drop migration is tested.
TOKEN_ALLOWLIST = {
    ("apps/web/tests/service-hardening.test.mjs", "sentry"),
    ("apps/web/tests/service-hardening.test.mjs", "glitchtip"),
    ("apps/web/tests/service-hardening.test.mjs", "googleoauth"),
    ("apps/web/tests/service-hardening.test.mjs", "signinwithoauth"),
    ("apps/api/tests/test_migration_contract_coverage.py", "publishjob"),
    ("apps/api/tests/test_migration_contract_coverage.py", "performancefeedback"),
}

SCAN_ROOTS = (
    "apps",
    "packages",
    "infra",
    "scripts",
    "tests",
    ".github",
    "docs/ops",
    "docs/security",
    "docs/reference",
    "docs/workers",
)
SCAN_FILES = (
    ".env.example",
    "prod.env.example",
    "setup-local-env.sh",
    "setup-local-env.ps1",
    "package.json",
    "docs/operations.md",
)
SKIP_PARTS = {
    ".git",
    "node_modules",
    ".next",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    "test-results",
    "playwright-report",
    "dist",
    "build",
    # Applied migration history is immutable; the drop migration removes the
    # objects and the pgTAP test proves they are gone.
    "migrations_legacy",
}
SKIP_NAMES = {"pnpm-lock.yaml", "package-lock.json", ".env"}
TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".mjs",
    ".js",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".md",
    ".sh",
    ".ps1",
    ".sql",
    ".example",
    ".tmpl",
    ".txt",
    "",
}


def _scan_files():
    candidates = [ROOT / name for name in SCAN_FILES]
    for directory in SCAN_ROOTS:
        candidates.extend((ROOT / directory).rglob("*"))
    for path in candidates:
        if not path.is_file() or path.resolve() == SELF:
            continue
        relative = path.relative_to(ROOT)
        if set(relative.parts) & SKIP_PARTS or path.name in SKIP_NAMES:
            continue
        if path.suffix not in TEXT_SUFFIXES and not path.name.startswith("Dockerfile"):
            continue
        yield relative.as_posix(), path


def _queue_names(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    block = source.split("export const allQueueNames = [", 1)[1].split("];", 1)[0]
    return re.findall(r'"([a-z_]+)"', block)


def test_no_removed_feature_names_remain_in_code_configuration_or_operations_docs() -> None:
    found = []
    for relative, path in _scan_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        normalized = text.lower().replace("-", "").replace("_", "")
        for token in REMOVED_TOKENS:
            if token in normalized and (relative, token) not in TOKEN_ALLOWLIST:
                found.append(f"{relative}: {token}")
    assert not found, "removed features are still referenced:\n" + "\n".join(sorted(found))


def _route_paths(app) -> set[str]:
    """Every registered path, including routes inside wrapped routers."""
    paths: set[str] = set(app.openapi()["paths"])
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        if hasattr(route, "path"):
            paths.add(route.path)
        if hasattr(route, "original_router"):
            pending.extend(route.original_router.routes)
    return paths


def test_api_serves_no_social_publishing_or_performance_insight_routes() -> None:
    paths = sorted(_route_paths(create_app()))
    removed = [
        path
        for path in paths
        if re.search(r"/publish(/|$)", path)
        or "performance-feedback" in path
        or "performance-metrics" in path
    ]

    # The scan must see the real route table, not only the docs routes.
    assert "/health/live" in paths
    assert removed == []
    # The export feature that prepares translated captions stays.
    assert "/projects/{project_id}/localized-publish-packages" in paths


def test_removed_modules_and_settings_are_gone() -> None:
    present = [name for name in REMOVED_MODULES if importlib.util.find_spec(name) is not None]
    fields = [
        name
        for name in Settings.model_fields
        if name.startswith(("publish_", "performance_feedback_", "sentry_"))
        or name in {"local_audit_publishing_accounts", "local_audit_allowed_publish_visibilities"}
    ]

    assert present == []
    assert fields == []


def test_no_publish_queue_worker_service_image_or_secret_remains() -> None:
    for registry in (
        ROOT / "apps" / "worker" / "src" / "queue-common.mjs",
        ROOT / "apps" / "api" / "worker-bridge" / "queue-common.mjs",
    ):
        assert "publish" not in _queue_names(registry), registry
    compose = yaml.safe_load((ROOT / "infra" / "docker-compose.gpu.yml").read_text("utf-8"))
    assert "publishing-worker" not in compose["services"]
    assert not {"publish_token_encryption_key", "publish_oauth_state_secret"} & set(
        compose["secrets"]
    )
    schema = json.loads((ROOT / "infra" / "release-manifest.schema.json").read_text("utf-8"))
    assert "MOTIONLY_PUBLISHING_WORKER_IMAGE" not in json.dumps(schema)


def test_web_has_no_error_reporter_google_sign_in_or_direct_publishing() -> None:
    package = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    global_error = (WEB / "app" / "global-error.tsx").read_text(encoding="utf-8")
    export_step = (WEB / "components" / "editor" / "export-step.tsx").read_text("utf-8")
    dashboard = (WEB / "components" / "DashboardClient.tsx").read_text(encoding="utf-8")

    assert not [name for name in dependencies if name.startswith("@sentry/")]
    # No-op stubs left behind when Sentry was dropped are dead code.
    for stub in ("instrumentation.ts", "instrumentation-client.ts"):
        assert not (WEB / stub).exists(), stub
    assert not (WEB / "app" / "api" / "glitchtip-tunnel").exists()
    # Nothing reports the error, so the page must not say it was reported.
    assert "has been reported" not in global_error
    assert not (WEB / "components" / "editor" / "performance-feedback-panel.tsx").exists()
    assert "/publish" not in export_step.replace("localized-publish-packages", "")
    assert "publish directly" not in dashboard.lower()
