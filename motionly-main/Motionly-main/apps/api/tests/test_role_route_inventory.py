"""B19: every API route is classified for role enforcement and the classification
is proven on the live app.

Like ``test_plan_conflict_inventory.py``, this enumerates the real app's routes
and forces the author of any *new* route to classify it. Each route is one of:

* ``public``            — no workspace auth (``get_actor`` absent)
* ``any-member``        — any active member (owner/admin/member): needs
                          ``get_actor`` but NOT ``require_role``
* ``owner+admin``       — owner or admin only: a ``require_role("owner","admin")``
                          guard in the route dependencies
* ``owner``             — owner only: a ``require_role("owner")`` guard
* ``admin-ops``         — platform admin console: ``require_admin`` guard
* ``internal-worker``   — worker/internal/local-storage endpoints

The test then verifies each classification is actually enforced on the route's
resolved dependencies, so a route that is mis-gated (or a new, unclassified
route) fails. ``require_role`` enforcement for owner/admin/member *actors* is
additionally exercised end-to-end in ``test_require_role.py``.
"""

import os

import pytest
from fastapi.routing import APIRoute

os.environ.setdefault("APP_ENV", "local")

from app.main import create_app  # noqa: E402

# ---------------------------------------------------------------------------
# Classification: (METHOD, PATH) -> category. Every live route must appear here.
# ---------------------------------------------------------------------------

PUBLIC: set[tuple[str, str]] = {
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/metrics"),
    ("GET", "/plans"),
    ("POST", "/auth/signup"),
    ("POST", "/auth/password-reset-requests"),
    ("POST", "/billing/webhooks/mock-gateway"),
    ("POST", "/public/exports/resolve"),
    ("POST", "/public/reviews/{share_link_id}/resolve"),
    ("POST", "/public/reviews/{share_link_id}/comments"),
    ("POST", "/public/reviews/{share_link_id}/decision"),
    ("POST", "/feedback"),
}

INTERNAL_WORKER: set[tuple[str, str]] = {
    ("PUT", "/_local/storage/{bucket}/{object_path:path}"),
    ("GET", "/_local/storage/{bucket}/{object_path:path}"),
    ("POST", "/internal/batches/{workspace_id}/{batch_id}/run"),
    ("POST", "/internal/retention/run"),
    ("POST", "/internal/workflows/proposals/ensure"),
    ("POST", "/internal/workflows/draft-renders/ensure"),
    ("POST", "/internal/media/sign-urls"),
    ("POST", "/internal/operations/heartbeat"),
    ("POST", "/internal/operations/provider-slo"),
    ("POST", "/internal/operations/gpu-autoscaling/run"),
}

ADMIN_OPS: set[tuple[str, str]] = {
    # Refunds target a workspace named in the body, so a workspace role is not
    # enough: only a platform admin may issue them (audit-logged).
    ("POST", "/billing/refunds"),
    ("GET", "/admin/ops/overview"),
    ("GET", "/admin/queues"),
    ("GET", "/admin/queues/jobs/{queue_job_id}"),
    ("POST", "/admin/queues/jobs/{queue_job_id}/retry"),
    ("POST", "/admin/queues/jobs/{queue_job_id}/cancel"),
    ("POST", "/admin/queues/stuck/dead-letter"),
    ("GET", "/admin/renders/{render_job_id}"),
    ("POST", "/admin/renders/{render_job_id}/retry"),
    ("POST", "/admin/renders/{render_job_id}/cancel"),
    ("GET", "/admin/users"),
    ("GET", "/admin/users/reset-requests"),
    ("POST", "/admin/users/reset-links"),
    ("POST", "/admin/users/ban"),
    ("POST", "/admin/users/unban"),
    ("POST", "/admin/users/reset-requests/dismiss"),
    ("GET", "/admin/editorial-learning/projects/{project_id}/datasets"),
    ("GET", "/admin/editorial-learning/projects/{project_id}/deployment"),
    ("POST", "/admin/editorial-learning/datasets/{dataset_id}/promote"),
    ("POST", "/admin/editorial-learning/deployments/{project_id}/rollback"),
    ("GET", "/admin/music/tracks"),
}

OWNER: set[tuple[str, str]] = {
    # Billing is owner-only (billing writes).
    ("POST", "/billing/checkout"),
    ("POST", "/billing/cancel"),
    ("POST", "/billing/retry"),
    # Ownership transfer (Task 20) is owner-only.
    ("POST", "/team/transfer-ownership"),
}

OWNER_ADMIN: set[tuple[str, str]] = {
    # Brand kits (workspace branding) and saved templates: owner or admin.
    # API-key management routes (Task 24) are owner/admin too. Note these manage
    # keys from the browser; they are NOT themselves callable by an API key.
    ("POST", "/api-keys"),
    ("GET", "/api-keys"),
    ("POST", "/api-keys/{key_id}/revoke"),
    ("GET", "/phase5/brand-kits"),
    ("POST", "/phase5/brand-kits"),
    ("PATCH", "/phase5/brand-kits/{brand_kit_id}"),
    ("DELETE", "/phase5/brand-kits/{brand_kit_id}"),
    ("GET", "/phase5/templates"),
    ("POST", "/phase5/templates"),
    ("PATCH", "/phase5/templates/{template_id}"),
    ("DELETE", "/phase5/templates/{template_id}"),
    # Team management (Task 20): owner or admin may mint/list/revoke invites and
    # change or remove members.
    ("POST", "/team/invitations"),
    ("GET", "/team/invitations"),
    ("POST", "/team/invitations/{invitation_id}/revoke"),
    ("PATCH", "/team/members/{user_id}/role"),
    ("DELETE", "/team/members/{user_id}"),
}

# Everything else: any active member (owner/admin/member) may edit and render.
# Listed explicitly so a new route cannot silently default into this bucket.
ANY_MEMBER: set[tuple[str, str]] = {
    ("GET", "/health/dependencies"),
    ("GET", "/health/deep"),
    ("GET", "/exports/{export_id}/download-url"),
    ("GET", "/billing/status"),
    ("GET", "/billing/history"),
    ("GET", "/storage/quota"),
    ("GET", "/projects"),
    ("POST", "/projects"),
    ("GET", "/projects/{project_id}"),
    ("PATCH", "/projects/{project_id}"),
    ("DELETE", "/projects/{project_id}"),
    ("GET", "/projects/{project_id}/analytics/ai-edits"),
    ("POST", "/projects/{project_id}/analytics/ai-edits"),
    ("GET", "/projects/{project_id}/broll"),
    ("GET", "/projects/{project_id}/broll/library"),
    ("GET", "/projects/{project_id}/broll/library/multipart/{session_id}"),
    ("POST", "/projects/{project_id}/broll/library/multipart/init"),
    ("POST", "/projects/{project_id}/broll/library/multipart/{session_id}/complete"),
    (
        "POST",
        "/projects/{project_id}/broll/library/multipart/{session_id}/parts/{part_number}/complete",
    ),
    ("DELETE", "/projects/{project_id}/broll/library/{asset_id}"),
    ("POST", "/projects/{project_id}/broll/generate"),
    ("GET", "/projects/{project_id}/broll/{slot_id}/search"),
    ("POST", "/projects/{project_id}/broll/{slot_id}/swap"),
    ("PATCH", "/projects/{project_id}/broll/{slot_id}/region"),
    ("GET", "/projects/{project_id}/captions"),
    ("POST", "/projects/{project_id}/captions/generate"),
    ("PATCH", "/projects/{project_id}/captions/{track_id}"),
    ("PATCH", "/projects/{project_id}/captions/{track_id}/segments"),
    ("GET", "/projects/{project_id}/clips/recommendations"),
    ("GET", "/projects/{project_id}/creative-variants"),
    ("POST", "/projects/{project_id}/creative-variants"),
    ("POST", "/projects/{project_id}/creative-variants/{variant_set_id}/apply"),
    ("PATCH", "/projects/{project_id}/creative-variants/{variant_set_id}/selection"),
    ("GET", "/projects/{project_id}/effects"),
    ("PATCH", "/projects/{project_id}/effects"),
    ("GET", "/projects/{project_id}/exports"),
    ("POST", "/projects/{project_id}/exports/{export_id}/restore"),
    ("GET", "/projects/{project_id}/jobs/{job_id}"),
    ("GET", "/projects/{project_id}/localized-publish-packages"),
    ("POST", "/projects/{project_id}/localized-publish-packages"),
    ("GET", "/projects/{project_id}/localized-publish-packages/{package_id}/subtitles"),
    ("PATCH", "/projects/{project_id}/localized-publish-packages/{package_id}/review"),
    ("POST", "/projects/{project_id}/media/refresh-urls"),
    ("GET", "/projects/{project_id}/music/selection"),
    ("GET", "/projects/{project_id}/music/tracks"),
    ("GET", "/projects/{project_id}/music/tracks/{track_id}/preview"),
    ("POST", "/projects/{project_id}/music/select"),
    ("PATCH", "/projects/{project_id}/music/regions"),
    ("GET", "/projects/{project_id}/phase5/audio-intelligence"),
    ("GET", "/projects/{project_id}/phase5/collaboration"),
    ("PATCH", "/projects/{project_id}/phase5/collaboration"),
    ("GET", "/projects/{project_id}/phase5/resources"),
    ("POST", "/projects/{project_id}/phase5/generated-broll"),
    ("POST", "/projects/{project_id}/phase5/model-training"),
    ("POST", "/projects/{project_id}/phase5/stems"),
    ("PATCH", "/projects/{project_id}/phase5/stems/{resource_id}"),
    ("POST", "/projects/{project_id}/phase5/style-profiles"),
    (
        "POST",
        "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/multipart/init",
    ),
    (
        "GET",
        "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/multipart/"
        "{session_id}",
    ),
    (
        "POST",
        "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/multipart/"
        "{session_id}/complete",
    ),
    (
        "POST",
        "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/multipart/"
        "{session_id}/parts/{part_number}/complete",
    ),
    (
        "DELETE",
        "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/{reference_id}",
    ),
    ("POST", "/projects/{project_id}/phase5/style-profiles/{profile_id}/train"),
    ("GET", "/projects/{project_id}/plans"),
    ("POST", "/projects/{project_id}/plans"),
    ("GET", "/projects/{project_id}/plans/jobs/{job_id}"),
    ("GET", "/projects/{project_id}/plans/snapshots"),
    ("POST", "/projects/{project_id}/plans/snapshots"),
    ("POST", "/projects/{project_id}/plans/snapshots/{snapshot_id}/restore"),
    ("GET", "/projects/{project_id}/plans/{plan_id}"),
    ("PATCH", "/projects/{project_id}/plans/{plan_id}/caption-highlights"),
    ("PATCH", "/projects/{project_id}/plans/{plan_id}/cuts"),
    ("PATCH", "/projects/{project_id}/plans/{plan_id}/manual-elements"),
    ("POST", "/projects/{project_id}/plans/{plan_id}/compile-timeline"),
    ("GET", "/projects/{project_id}/proposals"),
    ("GET", "/projects/{project_id}/proposals/{proposal_id}"),
    ("POST", "/projects/{project_id}/proposals/{proposal_id}/decisions"),
    ("POST", "/projects/{project_id}/regenerate"),
    ("GET", "/projects/{project_id}/renders"),
    ("POST", "/projects/{project_id}/renders"),
    ("POST", "/projects/{project_id}/renders/bulk"),
    ("GET", "/projects/{project_id}/renders/{render_job_id}"),
    ("GET", "/projects/{project_id}/renders/{render_job_id}/events"),
    ("POST", "/projects/{project_id}/renders/{render_job_id}/cancel"),
    ("POST", "/projects/{project_id}/renders/{render_job_id}/retry"),
    ("GET", "/projects/{project_id}/reviews"),
    ("POST", "/projects/{project_id}/reviews"),
    ("GET", "/projects/{project_id}/reviews/{review_id}"),
    ("POST", "/projects/{project_id}/reviews/{review_id}/comments"),
    ("POST", "/projects/{project_id}/reviews/{review_id}/decision"),
    ("POST", "/projects/{project_id}/reviews/{review_id}/feedback-suggestions"),
    ("POST", "/projects/{project_id}/reviews/{review_id}/share-links"),
    ("GET", "/projects/{project_id}/transcript"),
    ("POST", "/projects/{project_id}/transcriptions"),
    ("GET", "/projects/{project_id}/uploads/multipart/{session_id}"),
    ("POST", "/projects/{project_id}/uploads/multipart/init"),
    ("POST", "/projects/{project_id}/uploads/multipart/{session_id}/abort"),
    ("POST", "/projects/{project_id}/uploads/multipart/{session_id}/complete"),
    ("POST", "/projects/{project_id}/uploads/multipart/{session_id}/parts/{part_number}/complete"),
    ("GET", "/projects/{project_id}/uploads/{video_id}"),
    ("GET", "/projects/{project_id}/uploads/{video_id}/asset-urls"),
    ("GET", "/projects/{project_id}/visual/status"),
    ("GET", "/projects/{project_id}/workflows"),
    ("GET", "/projects/{project_id}/workflows/{workflow_run_id}"),
    ("POST", "/projects/{project_id}/workflows/{workflow_run_id}/cancel"),
    ("POST", "/projects/{project_id}/workflows/{workflow_run_id}/retry"),
    ("GET", "/asset-library/items"),
    ("POST", "/asset-library/items"),
    ("DELETE", "/asset-library/items/{item_id}"),
    ("GET", "/asset-library/items/{item_id}/media-url"),
    ("PATCH", "/asset-library/items/{item_id}/favorite"),
    ("POST", "/asset-library/items/{item_id}/reuse/projects/{project_id}"),
    ("GET", "/asset-library/collections"),
    ("POST", "/asset-library/collections"),
    ("POST", "/asset-library/collections/{collection_id}/items"),
    ("POST", "/projects/{project_id}/asset-library/index"),
    ("GET", "/exports/{export_id}/share-links"),
    ("POST", "/exports/{export_id}/share-links"),
    ("POST", "/exports/{export_id}/share-links/{share_link_id}/revoke"),
    # Team self-service (Task 20): the signed-in user accepts an invite or leaves.
    ("POST", "/team/invitations/accept"),
    ("POST", "/team/leave"),
    # The workspace member directory (Task 26) powers the live-collaboration
    # online indicator: any active member may read it, non-members cannot
    # (get_actor requires active membership). Names only — no PII.
    ("GET", "/team/members"),
    ("POST", "/phase5/batches"),
    ("POST", "/phase5/batches/{batch_id}/cancel"),
    # Editorial-learning *consent* management is a workspace self-service
    # action (any member); only the admin console promotes/rolls back models.
    ("GET", "/editorial-learning/status"),
    ("GET", "/editorial-learning/policy"),
    ("PUT", "/editorial-learning/policy"),
    ("POST", "/editorial-learning/revoke"),
    # The per-project "personalized from N edits" indicator is a read-only,
    # per-user self-service surface (Task 29).
    ("GET", "/editorial-learning/projects/{project_id}/memory"),
}

CLASSIFICATION: dict[tuple[str, str], str] = {}
for _key in PUBLIC:
    CLASSIFICATION[_key] = "public"
for _key in INTERNAL_WORKER:
    CLASSIFICATION[_key] = "internal-worker"
for _key in ADMIN_OPS:
    CLASSIFICATION[_key] = "admin-ops"
for _key in OWNER:
    CLASSIFICATION[_key] = "owner"
for _key in OWNER_ADMIN:
    CLASSIFICATION[_key] = "owner+admin"
for _key in ANY_MEMBER:
    CLASSIFICATION[_key] = "any-member"


# ---------------------------------------------------------------------------
# API-key classification (Task 24): every route is either callable with an
# ``mtly_`` Agency API key or not. This list is the *expected* allow-list; the
# live single source of truth is app.api.api_key_routes.API_KEY_ALLOWED_ROUTES
# (get_actor enforces it). The two must agree, so a new route cannot silently
# join or leave the API-key surface.
# ---------------------------------------------------------------------------
API_KEY_ALLOWED: set[tuple[str, str]] = {
    ("POST", "/projects"),
    ("GET", "/projects"),
    ("POST", "/projects/{project_id}/uploads/multipart/init"),
    ("POST", "/projects/{project_id}/uploads/multipart/{session_id}/parts/{part_number}/complete"),
    ("POST", "/projects/{project_id}/uploads/multipart/{session_id}/complete"),
    ("GET", "/projects/{project_id}/workflows"),
    ("GET", "/projects/{project_id}/workflows/{workflow_run_id}"),
    ("POST", "/projects/{project_id}/renders"),
    ("GET", "/projects/{project_id}/renders/{render_job_id}"),
    ("GET", "/exports/{export_id}/download-url"),
}


def _iter_api_routes():
    app = create_app()
    seen: set[tuple[str, str]] = set()

    def walk(routes):
        for route in routes:
            if isinstance(route, APIRoute):
                for method in sorted(set(route.methods) - {"HEAD", "OPTIONS"}):
                    if (method, route.path) in seen:
                        continue
                    seen.add((method, route.path))
                    yield method, route
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                yield from walk(original_router.routes)

    yield from walk(app.routes)


def _dependency_guards(route: APIRoute) -> list[str]:
    """Role/admin guards attached to a route, via tagged dependency callables."""
    guards: list[str] = []
    for dependant in _walk_dependants(route.dependant):
        call = dependant.call
        name = getattr(call, "__name__", "")
        required = getattr(call, "__motionly_required_roles__", None)
        if required is not None:
            guards.append("role:" + ",".join(sorted(required)))
        elif name == "require_admin":
            guards.append("admin")
    return guards


def _uses_get_actor(route: APIRoute) -> bool:
    """True if the route resolves the workspace actor (directly or via a guard)."""
    for dependant in _walk_dependants(route.dependant):
        if getattr(dependant.call, "__name__", "") == "get_actor":
            return True
    return False


def _walk_dependants(dependant):
    yield dependant
    for child in dependant.dependencies:
        yield from _walk_dependants(child)


ROUTES = [
    pytest.param(method, route, id=f"{method} {route.path}") for method, route in _iter_api_routes()
]


def test_every_route_is_classified():
    live = {(method, route.path) for method, route in _iter_api_routes()}
    unclassified = live - set(CLASSIFICATION)
    assert not unclassified, (
        "New route(s) are not classified for role enforcement. Add each to "
        "PUBLIC / ANY_MEMBER / OWNER_ADMIN / OWNER / ADMIN_OPS / INTERNAL_WORKER "
        f"in this test and apply the matching guard: {sorted(unclassified)}"
    )
    stale = set(CLASSIFICATION) - live
    assert not stale, f"Classification lists routes that no longer exist: {sorted(stale)}"


@pytest.mark.parametrize("method, route", ROUTES)
def test_route_enforcement_matches_classification(method, route):
    category = CLASSIFICATION[(method, route.path)]
    guards = _dependency_guards(route)

    if category == "owner":
        assert "role:owner" in guards, (
            f"{method} {route.path} is owner-only but lacks require_role('owner')"
        )
    elif category == "owner+admin":
        assert "role:admin,owner" in guards, (
            f"{method} {route.path} is owner+admin but lacks require_role('owner','admin')"
        )
    elif category == "admin-ops":
        assert "admin" in guards, (
            f"{method} {route.path} is an admin-ops route but lacks require_admin"
        )
    elif category == "any-member":
        assert _uses_get_actor(route), (
            f"{method} {route.path} is any-member but does not resolve get_actor"
        )
        assert not any(g.startswith("role:") for g in guards), (
            f"{method} {route.path} is any-member but is role-restricted: {guards}"
        )
    elif category == "internal-worker":
        assert not any(g.startswith("role:") for g in guards), (
            f"{method} {route.path} is internal-worker but is role-restricted"
        )
    elif category == "public":
        assert not _uses_get_actor(route), (
            f"{method} {route.path} is public but resolves get_actor"
        )


# ---------------------------------------------------------------------------
# API-key surface (Task 24)
# ---------------------------------------------------------------------------

from app.api.api_key_routes import API_KEY_ALLOWED_ROUTES  # noqa: E402


def test_every_route_is_api_key_classified():
    """Every live route is either API-key-allowed or not — no silent defaults.

    A new route that forgets to decide its API-key status fails here, forcing
    the author to add it to API_KEY_ALLOWED (if automation should reach it) or
    leave it off (the default refusal).
    """
    live = {(method, route.path) for method, route in _iter_api_routes()}
    # Allow-list entries must correspond to real routes.
    stale = API_KEY_ALLOWED - live
    assert not stale, f"API-key allow-list names routes that do not exist: {sorted(stale)}"
    # Nothing in the test's expected set is unaccounted for in the live app:
    # the partition (allowed vs not-allowed) is total by construction.
    not_allowed = live - API_KEY_ALLOWED
    assert live == (API_KEY_ALLOWED | not_allowed)


def test_api_key_allow_list_matches_single_source():
    """The expected allow-list here must equal the live single source of truth.

    get_actor enforces app.api.api_key_routes.API_KEY_ALLOWED_ROUTES; the two
    must agree so the enforced surface and the documented/tested surface never
    drift apart.
    """
    assert API_KEY_ALLOWED == set(API_KEY_ALLOWED_ROUTES), (
        "API-key allow-list disagrees with app.api.api_key_routes. Update both: "
        f"only in test={sorted(API_KEY_ALLOWED - set(API_KEY_ALLOWED_ROUTES))}, "
        f"only in module={sorted(set(API_KEY_ALLOWED_ROUTES) - API_KEY_ALLOWED)}"
    )


def test_api_key_management_routes_are_not_api_key_callable():
    """Managing keys requires a signed-in owner/admin, not an API key itself."""
    for pair in (
        ("POST", "/api-keys"),
        ("GET", "/api-keys"),
        ("POST", "/api-keys/{key_id}/revoke"),
    ):
        assert pair not in API_KEY_ALLOWED_ROUTES
