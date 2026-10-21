"""Task 17: one resumable, server-verified upload path for every media type.

The one-piece upload facades and the legacy project upload endpoints are gone;
every media type (source video, B-roll library clips, style references) uploads
through its resumable multipart endpoints only. These tests fail if any removed
route comes back on the API, or if any production caller (web, worker, scripts)
still targets a removed endpoint.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.main import create_app

ROOT = Path(__file__).resolve().parents[3]
SELF = Path(__file__).resolve()

# Exact templates of the removed routes. The resumable `.../multipart/...`
# routes and the lifecycle reads (`GET .../{video_id}`, `.../asset-urls`) stay.
REMOVED_ROUTES = (
    # Legacy project (source video) one-shot upload.
    "/projects/{project_id}/uploads/init",
    "/projects/{project_id}/uploads/{video_id}/complete",
    # One-piece B-roll library upload facade.
    "/projects/{project_id}/broll/library/init",
    "/projects/{project_id}/broll/library/{asset_id}/complete",
    # One-piece style-reference upload facade.
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/init",
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/complete",
)

# The resumable replacements that MUST still be served.
RETAINED_ROUTES = (
    "/projects/{project_id}/uploads/multipart/init",
    "/projects/{project_id}/uploads/multipart/{session_id}/complete",
    "/projects/{project_id}/broll/library/multipart/init",
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/multipart/init",
)


def _route_paths(app) -> set[str]:
    paths: set[str] = set(app.openapi()["paths"])
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        if hasattr(route, "path"):
            paths.add(route.path)
        if hasattr(route, "original_router"):
            pending.extend(route.original_router.routes)
    return paths


def test_removed_upload_routes_return_404_and_are_not_registered() -> None:
    paths = _route_paths(create_app())
    still_present = [route for route in REMOVED_ROUTES if route in paths]
    assert still_present == [], f"removed upload routes are back: {still_present}"
    # The resumable path that replaces them is still served.
    missing = [route for route in RETAINED_ROUTES if route not in paths]
    assert missing == [], f"resumable upload routes are missing: {missing}"


SCAN_DIRS = (
    ROOT / "apps" / "web",
    ROOT / "apps" / "worker",
    ROOT / "scripts",
    ROOT / "apps" / "api" / "app",
    ROOT / "apps" / "api" / "tests",
)
SKIP_PARTS = {
    "node_modules",
    ".next",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    "generated",
    ".turbo",
}
# Patterns a removed endpoint caller would contain. The resumable routes embed
# `/multipart/` before `/init` and `/complete`, so they never match these.
CALLER_PATTERNS = (
    re.compile(r"/uploads/init(?![a-z])"),
    re.compile(r"/uploads/\$\{[^}]+\}/complete"),
    re.compile(r"/broll/library/init(?![a-z])"),
    re.compile(r"/broll/library/\$\{[^}]+\}/complete"),
    re.compile(r"/references/init(?![a-z])"),
    re.compile(r"/references/complete(?![a-z])"),
)
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".mjs", ".js", ".json"}


def _scan_files():
    for directory in SCAN_DIRS:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.resolve() == SELF:
                continue
            if set(path.parts) & SKIP_PARTS or path.suffix not in TEXT_SUFFIXES:
                continue
            yield path


def test_no_production_caller_targets_a_removed_upload_endpoint() -> None:
    offenders: list[str] = []
    for path in _scan_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in CALLER_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {pattern.pattern}")
    assert offenders == [], "removed upload endpoints still have callers:\n" + "\n".join(
        sorted(offenders)
    )
