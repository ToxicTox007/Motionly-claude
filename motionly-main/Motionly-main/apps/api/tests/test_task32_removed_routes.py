"""Task 32 removed routes stay removed.

Phase G deleted a batch of dead/orphan API routes. This regression test pins
that every one of them is:

* absent from the live route surface (the OpenAPI contract built from the real
  app), so no handler silently comes back; and
* unreachable over HTTP — a request to the removed path returns 404, i.e. there
  is no matching route left (not a 401/403/405 from a surviving handler).

The ten routes the Phase-G final checkpoint deleted are listed in
report-final-checkpoint.md ("FIX 2"); the eight the internal-routes batch
deleted are the dead internal control-plane routes and the orphan quota/resource
/style-abort routes. The behaviour each thin route used to wrap is still covered
at the service/repository layer it genuinely lives in (see
report-internal-routes.md and report-final-checkpoint.md).
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("APP_ENV", "local")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

# (METHOD, OpenAPI path template) for every route Task 32 removed.
REMOVED_ROUTES: tuple[tuple[str, str], ...] = (
    # --- the ten the final checkpoint deleted (report-final-checkpoint.md) ---
    ("GET", "/projects/{project_id}/uploads/active"),
    ("POST", "/projects/{project_id}/uploads/{video_id}/cancel"),
    ("GET", "/projects/{project_id}/proposals/{proposal_id}/status"),
    ("GET", "/projects/{project_id}/analytics/fine-tuning-dataset"),
    ("POST", "/projects/{project_id}/visual/analyze"),
    ("GET", "/projects/{project_id}/visual/manifest"),
    ("POST", "/projects/{project_id}/broll/library/multipart/{session_id}/abort"),
    ("POST", "/admin/music/import"),
    ("POST", "/projects/{project_id}/plans/{plan_id}/activate"),
    ("PATCH", "/projects/{project_id}/plans/{plan_id}/timeline"),
    # --- the eight the internal-routes batch deleted (report-internal-routes.md) ---
    ("POST", "/internal/jobs/{queue_job_id}/run"),
    ("GET", "/internal/renders/{render_job_id}"),
    ("POST", "/internal/renders/{render_job_id}/complete"),
    ("POST", "/internal/renders/{render_job_id}/progress"),
    ("POST", "/internal/renders/{render_job_id}/failed"),
    ("GET", "/phase5/resources/{resource_id}"),
    ("GET", "/projects/{project_id}/media/quota"),
    (
        "POST",
        "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/"
        "multipart/{session_id}/abort",
    ),
)

# A concrete value for every path parameter so we can issue a real HTTP request.
_PARAM = re.compile(r"\{[^}]+\}")
_DUMMY_ID = "00000000-0000-0000-0000-000000000000"


def _concrete(path: str) -> str:
    """Substitute a dummy value for each ``{param}`` so the path is requestable."""
    return _PARAM.sub(_DUMMY_ID, path)


def _route_templates(app) -> set[tuple[str, str]]:
    """Every (METHOD, path template) actually mounted on the app, including
    routes inside wrapped routers."""
    templates: set[tuple[str, str]] = set()
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods:
            for method in methods:
                if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    templates.add((method, path))
        if hasattr(route, "original_router"):
            pending.extend(route.original_router.routes)
    return templates


def test_removed_routes_are_absent_from_the_openapi_contract() -> None:
    paths = create_app().openapi()["paths"]
    present = [
        f"{method} {path}"
        for method, path in REMOVED_ROUTES
        if path in paths and method.lower() in paths[path]
    ]
    assert present == [], (
        "Task 32 removed these routes but they are back in the contract: " + ", ".join(present)
    )


def test_removed_routes_have_no_mounted_handler() -> None:
    """No handler is mounted for any removed route template. This is exact and
    collision-free: a removed literal route (e.g. /uploads/active) whose path is
    now absorbed by a surviving dynamic sibling (/uploads/{video_id}) must not
    reappear as its own route."""
    mounted = _route_templates(create_app())
    present = [
        f"{method} {path}" for method, path in REMOVED_ROUTES if (method, path) in mounted
    ]
    assert present == [], (
        "Task 32 removed routes still have a mounted handler: " + ", ".join(present)
    )


def test_removed_dynamic_routes_return_404_over_http() -> None:
    """For the removed routes whose concrete path does not collide with a
    surviving dynamic sibling, a real HTTP request 404s (no matching route)."""
    app = create_app()
    mounted = _route_templates(app)
    client = TestClient(app)

    def _matches_surviving_template(method: str, concrete: str) -> bool:
        segments = concrete.split("/")
        for m, template in mounted:
            if m != method:
                continue
            tsegs = template.split("/")
            if len(tsegs) != len(segments):
                continue
            if all(
                tseg == seg or (tseg.startswith("{") and tseg.endswith("}"))
                for tseg, seg in zip(tsegs, segments, strict=True)
            ):
                return True
        return False

    still_routed: list[str] = []
    for method, path in REMOVED_ROUTES:
        concrete = _concrete(path)
        # Skip removed literal routes now absorbed by a surviving {param} sibling
        # — their absence is proven exactly by the two tests above.
        if _matches_surviving_template(method, concrete):
            continue
        response = client.request(method, concrete)
        if response.status_code != 404:
            still_routed.append(f"{method} {path} -> {response.status_code}")
    assert still_routed == [], (
        "Task 32 removed routes are still reachable: " + ", ".join(still_routed)
    )
