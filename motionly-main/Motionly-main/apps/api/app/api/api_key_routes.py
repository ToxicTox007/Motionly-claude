"""The allow-list of routes that ``mtly_`` API keys may call (Task 24).

Agency-plan API keys are deliberately *narrow*: they authenticate automation
(create a project, upload media, kick off and watch renders, fetch the finished
export) and nothing else. Every route not on this list refuses an API key with
403 ``api_key_route_forbidden`` even though the key is otherwise valid.

This module is the single source of truth. Three things read it:

* ``app.api.dependencies.get_actor`` — refuses API-key callers off the list;
* ``tests/test_role_route_inventory.py`` — asserts every live route is either
  API-key-allowed or not, so a new route cannot silently join the list;
* the generated API reference (``scripts/api-keys/generate_reference.py`` /
  the web reference page) — documents exactly these routes, and a test fails if
  the reference and this list disagree.

Paths are the FastAPI route templates (``/projects/{project_id}/renders``), the
same form the ingress forwards after stripping the ``/api`` prefix.
"""

from __future__ import annotations

# (METHOD, PATH) pairs. Keep sorted and documented; changing this set changes
# the public API surface available to automation.
API_KEY_ALLOWED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # Projects: create and list (automation provisions and enumerates work).
        ("POST", "/projects"),
        ("GET", "/projects"),
        # Resumable (multipart) upload: initialise, upload parts, complete.
        ("POST", "/projects/{project_id}/uploads/multipart/init"),
        (
            "POST",
            "/projects/{project_id}/uploads/multipart/{session_id}/parts/{part_number}/complete",
        ),
        ("POST", "/projects/{project_id}/uploads/multipart/{session_id}/complete"),
        # Workflow status (watch the pipeline progress).
        ("GET", "/projects/{project_id}/workflows"),
        ("GET", "/projects/{project_id}/workflows/{workflow_run_id}"),
        # Renders: create and check status.
        ("POST", "/projects/{project_id}/renders"),
        ("GET", "/projects/{project_id}/renders/{render_job_id}"),
        # Export download URL (fetch the finished file).
        ("GET", "/exports/{export_id}/download-url"),
    }
)


def route_is_api_key_allowed(method: str, path: str) -> bool:
    return (method.upper(), path) in API_KEY_ALLOWED_ROUTES


def api_key_allowed_routes_sorted() -> list[tuple[str, str]]:
    """Deterministic ordering for documentation and contract tests."""
    return sorted(API_KEY_ALLOWED_ROUTES, key=lambda pair: (pair[1], pair[0]))
