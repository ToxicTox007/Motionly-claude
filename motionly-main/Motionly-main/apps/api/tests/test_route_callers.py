"""Unit tests for the route-caller dead-code check (scripts/verify/route_callers.py).

The deadcode stage fails any live OpenAPI route that no product caller reaches
and that is not an approved external entry point (scripts/verify/external-callers.json)
or GPU-stage-exempt (tests/gpu-stage-allowlist.json). These tests plant routes,
call sites and allow-lists into the pure ``check_routes`` judge, and exercise the
call-site extractor and route matcher directly, so the behaviour is verified
without depending on the live contract.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def rc():
    spec = importlib.util.spec_from_file_location(
        "motionly_route_callers", ROOT / "scripts" / "verify" / "route_callers.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# check_routes: the pure judge
# ---------------------------------------------------------------------------


def test_planted_uncalled_unlisted_route_fails(rc):
    routes = [("GET", "/widgets/{widget_id}")]
    verdict = rc.check_routes(routes, call_paths=set(), external={}, gpu=set())
    assert verdict.passed is False
    assert "GET /widgets/{widget_id}" in verdict.dead
    assert verdict.reachable_by_caller == []


def test_route_reached_by_a_product_call_site_passes(rc):
    routes = [("GET", "/widgets/{widget_id}")]
    # A normalized call-site path: the interpolated id is the sentinel.
    call_paths = {"/widgets/\x00"}
    verdict = rc.check_routes(routes, call_paths, external={}, gpu=set())
    assert verdict.passed is True
    assert "GET /widgets/{widget_id}" in verdict.reachable_by_caller
    assert verdict.dead == []


def test_listed_external_route_passes(rc):
    routes = [("POST", "/billing/webhooks/mock-gateway")]
    external = {("POST", "/billing/webhooks/mock-gateway"): "payment gateway webhook"}
    verdict = rc.check_routes(routes, call_paths=set(), external=external, gpu=set())
    assert verdict.passed is True
    assert "POST /billing/webhooks/mock-gateway" in verdict.external
    assert verdict.dead == []


def test_gpu_exempt_route_passes(rc):
    routes = [("POST", "/internal/operations/gpu-autoscaling/run")]
    gpu = {("POST", "/internal/operations/gpu-autoscaling/run")}
    verdict = rc.check_routes(routes, call_paths=set(), external={}, gpu=gpu)
    assert verdict.passed is True
    assert "POST /internal/operations/gpu-autoscaling/run" in verdict.gpu_stage
    assert verdict.dead == []


def test_gpu_exempt_method_agnostic_entry_passes(rc):
    routes = [("GET", "/gpu/thing"), ("POST", "/gpu/thing")]
    gpu = {("*", "/gpu/thing")}
    verdict = rc.check_routes(routes, call_paths=set(), external={}, gpu=gpu)
    assert verdict.passed is True
    assert set(verdict.gpu_stage) == {"GET /gpu/thing", "POST /gpu/thing"}


def test_external_allowlist_for_nonexistent_route_fails(rc):
    routes = [("GET", "/widgets")]
    call_paths = {"/widgets"}
    external = {("POST", "/gone"): "stale"}
    verdict = rc.check_routes(routes, call_paths, external=external, gpu=set())
    assert verdict.passed is False
    assert any("does not exist" in f for f in verdict.failures)


def test_gpu_allowlist_for_nonexistent_route_fails(rc):
    routes = [("GET", "/widgets")]
    call_paths = {"/widgets"}
    gpu = {("POST", "/gone")}
    verdict = rc.check_routes(routes, call_paths, external={}, gpu=gpu)
    assert verdict.passed is False
    assert any("does not exist" in f for f in verdict.failures)


def test_literal_route_segment_is_not_matched_by_a_wrong_literal(rc):
    # A call to /widgets/active must NOT satisfy /widgets/{id}/history.
    routes = [("GET", "/widgets/{widget_id}/history")]
    call_paths = {"/widgets/active"}
    verdict = rc.check_routes(routes, call_paths, external={}, gpu=set())
    assert verdict.passed is False
    assert "GET /widgets/{widget_id}/history" in verdict.dead


# ---------------------------------------------------------------------------
# Route matcher
# ---------------------------------------------------------------------------


def test_matcher_param_segment_matches_sentinel_or_value(rc):
    m = rc._route_matcher("/projects/{project_id}/plans/{plan_id}")
    assert m.match("/projects/\x00/plans/\x00")
    assert m.match("/projects/abc/plans/def")
    assert m.match("/projects/\x00/plans/\x00?expected_revision=3")
    # Too few segments must not match.
    assert not m.match("/projects/\x00")


def test_matcher_literal_segment_is_strict(rc):
    m = rc._route_matcher("/projects/{project_id}/visual/analyze")
    assert m.match("/projects/\x00/visual/analyze")
    # A different literal tail must not match.
    assert not m.match("/projects/\x00/visual/status")


def test_matcher_catch_all_matches_remaining_slashes(rc):
    m = rc._route_matcher("/_local/storage/{bucket}/{object_path:path}")
    assert m.match("/_local/storage/\x00/a/b/c.mp4")
    assert m.match("/_local/storage/bkt/deep/nested/key")


# ---------------------------------------------------------------------------
# Call-site extraction
# ---------------------------------------------------------------------------


def test_extracts_template_literal_interpolations(rc):
    text = "`${env.apiBaseUrl}/projects/${projectId}/uploads/multipart/init`"
    paths = rc.extract_call_paths(text)
    assert "/projects/\x00/uploads/multipart/init" in paths


def test_extracts_through_encodeuricomponent(rc):
    text = "adminPost(`/admin/queues/jobs/${encodeURIComponent(jobId)}/retry`)"
    paths = rc.extract_call_paths(text)
    assert "/admin/queues/jobs/\x00/retry" in paths


def test_resolves_base_path_variable(rc):
    text = 'const BASE = "/admin/users";\nfetch(`${env.apiBaseUrl}${BASE}/reset-links`)'
    paths = rc.extract_call_paths(text)
    assert "/admin/users/reset-links" in paths


def test_resolves_template_base_variable_with_suffix(rc):
    text = (
        "const base = `${env.apiBaseUrl}/projects/${id}/broll/library/multipart`;\n"
        "editorApiRequest(`${base}/init`);\n"
        "editorApiRequest(`${base}/${sessionId}/complete`);"
    )
    paths = rc.extract_call_paths(text)
    assert "/projects/\x00/broll/library/multipart/init" in paths
    assert "/projects/\x00/broll/library/multipart/\x00/complete" in paths


def test_expands_ternary_string_segment(rc):
    text = '`${env.apiBaseUrl}/admin/users/${banned ? "ban" : "unban"}`'
    paths = rc.extract_call_paths(text)
    assert "/admin/users/ban" in paths
    assert "/admin/users/unban" in paths


def test_query_string_is_stripped(rc):
    text = "`${env.apiBaseUrl}/projects/${id}/plans/${pid}/compile-timeline?expected_revision=3`"
    paths = rc.extract_call_paths(text)
    assert "/projects/\x00/plans/\x00/compile-timeline" in paths


def test_a_route_declaration_is_not_a_call_to_itself(rc):
    # The handler module names its own path in the router prefix, the
    # decorator, its docstring and comments. None of that calls the route.
    text = '''"""Internal renders.

GET  /internal/renders/{render_job_id}            returns the manifest
POST /internal/renders/{render_job_id}/complete   marks it complete
"""
router = APIRouter(prefix="/internal/renders", tags=["internal renders"])

# ---------------------------------------------------------------------------
# GET /internal/renders/{render_job_id}
# ---------------------------------------------------------------------------
@router.get("/{render_job_id}", response_model=Manifest)
async def get_manifest(render_job_id: UUID): ...


@router.get("/phase5/resources/{resource_id}", response_model=Resource)
async def get_resource(resource_id: UUID): ...
'''
    paths = rc.extract_call_paths(text)
    assert not any(p.startswith("/internal/renders") for p in paths), paths
    assert not any(p.startswith("/phase5/resources") for p in paths), paths


def test_comments_and_docstrings_are_not_callers_in_javascript_either(rc):
    text = (
        "// POST /internal/renders/${id}/complete is no longer used\n"
        "/* GET /phase5/resources/${id} */\n"
        "/**\n * Calls /admin/users/ban.\n */\n"
        "await fetch(`${env.apiBaseUrl}/projects/${projectId}/phase5/resources`);\n"
    )
    paths = rc.extract_call_paths(text)
    assert "/projects/\x00/phase5/resources" in paths
    assert not any(
        p.startswith(("/internal/renders", "/phase5/resources", "/admin"))
        for p in paths
    )


def test_a_url_string_in_python_code_is_still_a_caller(rc):
    text = (
        "# not this one: /internal/comment\n"
        'response = httpx.post(f"{base_url}/internal/renders/{job_id}/complete", json=body)\n'
        "fetch(`${apiBaseUrl()}/internal/batches/${workspaceId}/${resourceId}/run`)\n"
    )
    paths = rc.extract_call_paths(text)
    assert "/internal/renders/\x00/complete" in paths
    assert "/internal/batches/\x00/\x00/run" in paths
    assert "/internal/comment" not in paths


def test_test_files_are_not_product_callers(rc, tmp_path):
    # A path literal that lives only in a test file must not count as a caller.
    (tmp_path / "apps" / "web" / "components").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "tests").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "tests" / "thing.test.mjs").write_text(
        "`${env.apiBaseUrl}/only/in/tests`", encoding="utf-8"
    )
    found = rc.scan_callers(tmp_path, caller_roots=("apps/web/components", "apps/web/tests"))
    assert "/only/in/tests" not in found


# ---------------------------------------------------------------------------
# Allow-list loaders
# ---------------------------------------------------------------------------


def test_external_loader_requires_a_reason_on_every_entry(rc):
    with pytest.raises(rc.RouteCallerError):
        rc.load_external({"entries": [{"method": "GET", "path": "/x", "reason": ""}]})


def test_gpu_loader_reads_method_and_pathless_entries(rc):
    allowlist = {
        "entries": [
            {"kind": "api_route", "locations": ["POST /a/b"]},
            {"kind": "api_route", "locations": ["/c/d"]},
            {"kind": "python_symbol", "locations": ["app/x.py#y"]},
        ]
    }
    gpu = rc.load_gpu_routes(allowlist)
    assert ("POST", "/a/b") in gpu
    assert ("*", "/c/d") in gpu
    # non-api_route kinds are ignored
    assert all("app/x.py" not in p for _, p in gpu)


# ---------------------------------------------------------------------------
# Live contract: the real repo must pass through the same judge the stage runs.
# ---------------------------------------------------------------------------


def test_live_repo_route_surface_is_clean_or_lists_only_known_dead(rc):
    """The live run must not raise and must not have stale allow-list entries.

    Genuinely-dead routes (owned by the deletions work) may still be reported in
    ``dead``; this test asserts the mechanism is sound (no loader failures, no
    stale allow-list entries), not that the surface is already empty.
    """
    verdict = rc.run(ROOT)
    assert verdict.failures == [], f"stale allow-list or loader error: {verdict.failures}"
    # External + GPU + caller-reachable must together cover everything that is
    # not in the (separately reported) dead set.
    classified = (
        len(verdict.reachable_by_caller)
        + len(verdict.external)
        + len(verdict.gpu_stage)
        + len(verdict.dead)
    )
    assert classified == verdict.total
