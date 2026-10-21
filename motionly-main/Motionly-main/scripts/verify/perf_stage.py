#!/usr/bin/env python3
"""Perf verify stage (Decision D2): the CPU-stage performance and resource gate.

Two subcommands, run in order by scripts/verify/stages.json after the stack and
browser stages have produced their reports:

``smoke``   Provision a confirmed user, workspace, project, upload and render on
            the running CPU stage (reusing scripts/verify/cpu_stage_stack.py's
            origin + admin helpers), run the k6 API-workflow smoke in strict
            mode (K6_STRICT_SLO=1, so any non-2xx aborts the run), adapt its
            summary (scripts/perf/k6-to-canary.mjs) and enforce
            scripts/perf/slo-contract.cpu-stage.json (scripts/perf/assert-slo.mjs).
``budget``  Enforce the approved resource and page-load budgets
            (scripts/verify/perf_budget.py) against the reports the stack and
            browser stages wrote (test-results/cpu-stage/resources.json and
            test-results/browser/report.json). Restarts, OOM kills and failed
            one-shots fail regardless of budgets; pending-baseline services are
            reported, never silently passed.

The budget checker core is unit-tested directly (apps/api/tests/test_perf_budget.py)
and the k6 env builder here is unit-tested (apps/api/tests/test_perf_stage.py);
the live smoke needs a running stack and runs only in the perf CI job.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERF = ROOT / "scripts" / "perf"
RESULTS = ROOT / "test-results"
K6_SCRIPT = PERF / "k6-api-workflows.js"
K6_SUMMARY = RESULTS / "k6-summary.json"
K6_CANARY = RESULTS / "k6-canary.json"
SLO_CONTRACT = PERF / "slo-contract.cpu-stage.json"
ADAPTER = PERF / "k6-to-canary.mjs"
ASSERT_SLO = PERF / "assert-slo.mjs"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"{path} is missing"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def build_k6_env(
    base_url: str,
    *,
    bearer_token: str,
    workspace_id: str,
    project_id: str,
    upload_id: str,
    render_id: str,
    revision: str,
    admin_token: str = "",
    summary_output: str = str(K6_SUMMARY),
) -> dict[str, str]:
    """The environment for a strict CPU-stage k6 smoke. Pure, so it is unit
    tested. K6_STRICT_SLO=1 makes every non-2xx abort the run."""
    if not (bearer_token and workspace_id and project_id and upload_id and render_id):
        raise ValueError("strict k6 needs a bearer token, workspace, project, upload and render")
    env = {
        "K6_STRICT_SLO": "1",
        "API_BASE_URL": base_url.rstrip("/"),
        "API_BEARER_TOKEN": bearer_token,
        "WORKSPACE_ID": workspace_id,
        "PROJECT_ID": project_id,
        "UPLOAD_ID": upload_id,
        "RENDER_ID": render_id,
        "DEPLOYMENT_REVISION": revision,
        "K6_SUMMARY_OUTPUT": summary_output,
    }
    if admin_token:
        env["ADMIN_API_TOKEN"] = admin_token
    return env


def _run(args: list[str], *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(args, cwd=ROOT, env=env, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args[:4])} ...")


def _seed_perf_workload(stack, state: dict) -> dict:
    """Provision a confirmed user, workspace, project, upload session and render
    on the running CPU stage, returning the ids the strict k6 smoke needs. Uses
    cpu_stage_stack's origin and admin helpers; kept here so the perf stage owns
    its own seeding. Runs only against a live stack (the perf CI job)."""
    origin = stack.STACK["public_origin"]
    env = stack._stack_env(state)
    anon = env["SUPABASE_ANON_KEY"]
    service = {
        "apikey": env["SUPABASE_SERVICE_ROLE_KEY"],
        "authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE_KEY']}",
    }
    email = f"perf-smoke-{os.getpid()}-{int(__import__('time').time())}@motionly.test"
    password = f"Perf-Smoke-{os.urandom(12).hex()}-Aa1!"
    status, user = stack._http_json(
        "POST",
        f"{origin}/supabase/auth/v1/admin/users",
        headers=service,
        body={"email": email, "password": password, "email_confirm": True},
    )
    if status != 200 or not isinstance(user, dict):
        raise RuntimeError(f"creating a confirmed user returned {status}: {user}")
    status, session = stack._http_json(
        "POST",
        f"{origin}/supabase/auth/v1/token?grant_type=password",
        headers={"apikey": anon},
        body={"email": email, "password": password},
    )
    if status != 200 or not isinstance(session, dict):
        raise RuntimeError(f"password sign-in returned {status}: {session}")
    bearer = session["access_token"]
    status, memberships = stack._http_json(
        "GET",
        f"{origin}/supabase/rest/v1/workspace_members?select=workspace_id",
        headers={"apikey": anon, "authorization": f"Bearer {bearer}"},
    )
    if status != 200 or not isinstance(memberships, list) or not memberships:
        raise RuntimeError(f"reading workspace membership returned {status}: {memberships}")
    workspace_id = memberships[0]["workspace_id"]
    api = {"authorization": f"Bearer {bearer}", "x-motionly-workspace-id": workspace_id}
    status, project = stack._http_json(
        "POST", f"{origin}/api/projects", headers=api, body={"name": "Perf smoke"}
    )
    if status not in (200, 201) or not isinstance(project, dict):
        raise RuntimeError(f"creating a project returned {status}: {project}")
    project_id = project["id"]
    status, upload = stack._http_json(
        "POST",
        f"{origin}/api/projects/{project_id}/uploads/multipart/init",
        headers=api,
        body={"filename": "perf.mp4", "mime_type": "video/mp4", "file_size_bytes": 1048576},
    )
    if status not in (200, 201) or not isinstance(upload, dict):
        raise RuntimeError(f"initialising an upload returned {status}: {upload}")
    upload_id = upload.get("session_id") or upload.get("id")
    status, plan = stack._http_json(
        "POST",
        f"{origin}/api/projects/{project_id}/plans",
        headers=api,
        body={"prompt": "perf smoke"},
    )
    if status not in (200, 201, 202) or not isinstance(plan, dict):
        raise RuntimeError(f"creating a plan returned {status}: {plan}")
    render_id = plan.get("render_id") or plan.get("id")
    if not (upload_id and render_id):
        raise RuntimeError("the stack did not return an upload session id and a render id")
    return {
        "base_url": f"{origin}/api",
        "bearer_token": bearer,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "upload_id": str(upload_id),
        "render_id": str(render_id),
    }


def cmd_smoke(args: argparse.Namespace) -> int:
    stack = _load(ROOT / "scripts" / "verify" / "cpu_stage_stack.py", "motionly_cpu_stage_stack")
    state = stack.load_state()
    seeded = _seed_perf_workload(stack, state)
    revision = args.revision or os.environ.get("DEPLOYMENT_REVISION", "cpu-stage")
    k6_env = build_k6_env(
        seeded["base_url"],
        bearer_token=seeded["bearer_token"],
        workspace_id=seeded["workspace_id"],
        project_id=seeded["project_id"],
        upload_id=seeded["upload_id"],
        render_id=seeded["render_id"],
        admin_token=seeded.get("admin_token", ""),
        revision=revision,
    )
    K6_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    _run(["k6", "run", str(K6_SCRIPT)], env={**os.environ, **k6_env})
    _run(
        [
            "node", str(ADAPTER),
            "--summary", str(K6_SUMMARY),
            "--out", str(K6_CANARY),
            "--revision", revision,
        ]
    )
    _run(
        [
            "node", str(ASSERT_SLO),
            "--contract", str(SLO_CONTRACT),
            "--canary", str(K6_CANARY),
            "--revision", revision,
        ]
    )
    print("perf stage: k6 smoke passed the CPU-stage SLO contract")
    return 0


def cmd_budget(_args: argparse.Namespace) -> int:
    budget = _load(ROOT / "scripts" / "verify" / "perf_budget.py", "motionly_perf_budget")
    return budget.main([])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--revision", default=None)
    commands.add_parser("budget")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    handlers = {"smoke": cmd_smoke, "budget": cmd_budget}
    try:
        return handlers[args.command](args)
    except Exception as error:  # noqa: BLE001 — surface any stack/k6 failure as a stage failure
        print(f"perf stage: FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
