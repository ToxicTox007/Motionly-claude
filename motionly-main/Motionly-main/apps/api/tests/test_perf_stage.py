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
