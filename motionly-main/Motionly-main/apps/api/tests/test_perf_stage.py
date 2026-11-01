"""Perf verify stage orchestrator (scripts/verify/perf_stage.py).

The live k6 smoke needs a running CPU stage (the perf CI job). Here we pin the
pure part: the strict k6 environment it builds. K6_STRICT_SLO=1 makes any
non-2xx abort the run, and the strict run refuses to start without a full set of
provisioned ids.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def perf_stage():
    spec = importlib.util.spec_from_file_location(
        "motionly_perf_stage", ROOT / "scripts" / "verify" / "perf_stage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ids() -> dict:
    return {
        "bearer_token": "token",
        "workspace_id": "ws",
        "project_id": "pr",
        "upload_id": "up",
        "render_id": "rn",
        "revision": "sha",
    }


def test_strict_k6_env_fails_on_non_2xx_and_carries_every_id(perf_stage) -> None:
    env = perf_stage.build_k6_env("https://edge.test/api/", **_ids())

    assert env["K6_STRICT_SLO"] == "1"
    assert env["API_BASE_URL"] == "https://edge.test/api"  # trailing slash stripped
    assert env["API_BEARER_TOKEN"] == "token"
    assert env["WORKSPACE_ID"] == "ws"
    assert env["PROJECT_ID"] == "pr"
    assert env["UPLOAD_ID"] == "up"
    assert env["RENDER_ID"] == "rn"
    assert env["DEPLOYMENT_REVISION"] == "sha"


def test_strict_k6_refuses_to_run_without_a_full_workload(perf_stage) -> None:
    missing = _ids() | {"render_id": ""}
    with pytest.raises(ValueError, match="render"):
        perf_stage.build_k6_env("https://edge.test/api", **missing)


def test_admin_token_is_optional(perf_stage) -> None:
    without = perf_stage.build_k6_env("https://edge.test/api", **_ids())
    assert "ADMIN_API_TOKEN" not in without
    with_admin = perf_stage.build_k6_env("https://edge.test/api", admin_token="a", **_ids())
    assert with_admin["ADMIN_API_TOKEN"] == "a"


def test_budget_subcommand_delegates_to_the_budget_checker(perf_stage, monkeypatch) -> None:
    # `budget` must defer entirely to perf_budget.main so there is one budget
    # definition; stub it and assert the return code is forwarded.
    import types

    called = {}

    def fake_main(argv):
        called["argv"] = argv
        return 0

    fake = types.SimpleNamespace(main=fake_main)
    monkeypatch.setattr(perf_stage, "_load", lambda *a, **k: fake)
    assert perf_stage.main(["budget"]) == 0
    assert called["argv"] == []


def test_k6_trusts_the_stack_test_ca_instead_of_skipping_tls_checks(perf_stage) -> None:
    # k6 (Go) reads extra trusted roots from SSL_CERT_FILE; the CDN simulator's
    # certificate comes from the run's own test CA.
    env = perf_stage.build_k6_env("https://edge.test/api", ca_cert="/run/edge_ca_cert", **_ids())
    assert env["SSL_CERT_FILE"] == "/run/edge_ca_cert"
    assert "SSL_CERT_FILE" not in perf_stage.build_k6_env("https://edge.test/api", **_ids())
    script = (ROOT / "scripts" / "perf" / "k6-api-workflows.js").read_text("utf-8")
    assert "insecureSkipTLSVerify" not in script
    orchestrator = (ROOT / "scripts" / "verify" / "perf_stage.py").read_text("utf-8")
    assert "insecureSkipTLSVerify" not in orchestrator


def test_the_smoke_seeds_and_runs_k6_against_the_verified_edge(perf_stage) -> None:
    source = (ROOT / "scripts" / "verify" / "perf_stage.py").read_text("utf-8")
    smoke = source.split("def cmd_smoke", 1)[1].split("\ndef ", 1)[0]
    # The edge CA is trusted before the seeding requests, then handed to k6.
    assert smoke.index("stack.trust_edge_ca(state)") < smoke.index("_seed_perf_workload(")
    assert 'ca_cert=str(stack.secret_path(state, "edge_ca_cert"))' in smoke


def test_the_metrics_scrape_gets_its_own_token(perf_stage) -> None:
    env = perf_stage.build_k6_env("https://edge.test/api", metrics_token="m", **_ids())
    assert env["METRICS_BEARER_TOKEN"] == "m"
    assert "METRICS_BEARER_TOKEN" not in perf_stage.build_k6_env("https://edge.test/api", **_ids())


def test_the_k6_workload_calls_routes_the_api_serves() -> None:
    script = (ROOT / "scripts" / "perf" / "k6-api-workflows.js").read_text("utf-8")
    # Render status lives under the project; there is no top-level /renders/{id}.
    assert "${baseUrl}/projects/${projectId}/renders/${renderId}" in script
    assert "${baseUrl}/renders/" not in script
    # Each upload iteration uses its own session and aborts it, instead of
    # confirming parts of the seeded (already completed) upload.
    assert "uploadId || session.session_id" not in script
    assert "${project}/uploads/multipart/${session.session_id}/abort" in script
    # /metrics is scraped with the metrics token, not the user's session.
    assert "authorization: `Bearer ${metricsToken}`" in script


def test_the_seeder_builds_a_real_render_through_the_pipeline() -> None:
    source = (ROOT / "scripts" / "verify" / "perf_stage.py").read_text("utf-8")
    seed = source.split("def _seed_perf_workload", 1)[1].split("\ndef ", 1)[0]
    order = [
        '"title": "Perf smoke"',
        "uploads}/init",
        "_put_part(",
        "/complete",
        "the first-draft transcript",
        "/plans",
        "/renders",
    ]
    positions = [seed.index(marker) for marker in order]
    assert positions == sorted(positions)


def test_every_counted_surface_has_a_threshold_so_k6_reports_its_count() -> None:
    # k6 computes a tagged sub-metric such as http_reqs{surface:projects} only
    # when a threshold names it. Without these the summary counted 0 samples on
    # every surface and the SLO gate failed with insufficient_samples.
    script = (ROOT / "scripts" / "perf" / "k6-api-workflows.js").read_text("utf-8")
    for surface in ("projects", "uploads", "plans", "render_status", "ops"):
        assert f'count("{surface}")' in script, surface
        assert f'"http_reqs{{surface:{surface}}}": ["count>=0"]' in script, surface
