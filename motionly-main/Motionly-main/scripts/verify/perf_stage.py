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
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
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
    metrics_token: str = "",
    summary_output: str = str(K6_SUMMARY),
    ca_cert: str = "",
) -> dict[str, str]:
    """The environment for a strict CPU-stage k6 smoke. Pure, so it is unit
    tested. K6_STRICT_SLO=1 makes every non-2xx abort the run. ``ca_cert`` is
    the stack's per-run edge test CA: k6 (Go) reads its trusted roots from
    SSL_CERT_FILE, so the smoke verifies the CDN simulator's certificate
    instead of skipping TLS verification."""
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
    if metrics_token:
        env["METRICS_BEARER_TOKEN"] = metrics_token
    if ca_cert:
        env["SSL_CERT_FILE"] = ca_cert
    return env


def _run(args: list[str], *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(args, cwd=ROOT, env=env, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args[:4])} ...")


def _source_video() -> bytes:
    """A 3 s H.264/AAC clip (the browser suite's source video, made the same way)."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "perf-source.mp4"
        _run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=24:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
            ]
        )
        return path.read_bytes()


def _put_part(stack, url: str, data: bytes, headers: dict[str, str]) -> str | None:
    """PUT one part to its signed storage URL through the edge, as the browser does."""
    request = urllib.request.Request(url, method="PUT", data=data, headers=headers)
    with stack._opener(follow_redirects=False).open(request, timeout=30) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"storage refused the upload part ({response.status})")
        return response.headers.get("etag")


def _expect(status: int, body: object, accepted: tuple[int, ...], what: str) -> dict:
    if status not in accepted or not isinstance(body, dict):
        raise RuntimeError(f"{what} returned {status}: {body}")
    return body


def _seed_perf_workload(stack, state: dict) -> dict:
    """Provision the ids the strict k6 smoke needs through the real pipeline:
    a confirmed user and workspace, a project, a completed upload of a real
    clip (which starts the first-draft workflow), its ready transcript, an
    edit plan and a render. Runs only against a live stack (the perf CI job)."""
    origin = stack.STACK["public_origin"]
    env = stack._stack_env(state)
    anon = env["SUPABASE_ANON_KEY"]
    service = {
        "apikey": env["SUPABASE_SERVICE_ROLE_KEY"],
        "authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE_KEY']}",
    }
    email = f"perf-smoke-{os.getpid()}-{int(time.time())}@motionly.test"
    password = f"Perf-Smoke-{os.urandom(12).hex()}-Aa1!"
    status, user = stack._http_json(
        "POST",
        f"{origin}/supabase/auth/v1/admin/users",
        headers=service,
        body={"email": email, "password": password, "email_confirm": True},
    )
    _expect(status, user, (200,), "creating a confirmed user")
    status, session = stack._http_json(
        "POST",
        f"{origin}/supabase/auth/v1/token?grant_type=password",
        headers={"apikey": anon},
        body={"email": email, "password": password},
    )
    bearer = _expect(status, session, (200,), "password sign-in")["access_token"]
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
        "POST", f"{origin}/api/projects", headers=api, body={"title": "Perf smoke"}
    )
    project_id = _expect(status, project, (200, 201), "creating a project")["id"]
    uploads = f"{origin}/api/projects/{project_id}/uploads/multipart"

    video = _source_video()
    status, upload = stack._http_json(
        "POST",
        f"{uploads}/init",
        headers=api,
        body={"filename": "perf.mp4", "mime_type": "video/mp4", "file_size_bytes": len(video)},
    )
    upload = _expect(status, upload, (200, 201), "initialising an upload")
    session_id = upload["session_id"]
    part_size = int(upload["part_size_bytes"])
    for part in upload["part_urls"]:
        number = int(part["part_number"])
        chunk = video[(number - 1) * part_size : number * part_size]
        url = part["upload_url"]
        url = f"{origin}{url}" if url.startswith("/") else url
        etag = _put_part(stack, url, chunk, dict(part.get("upload_headers") or {}))
        status, confirmed = stack._http_json(
            "POST",
            f"{uploads}/{session_id}/parts/{number}/complete",
            headers=api,
            body={"etag": etag} if etag else {},
        )
        _expect(status, confirmed, (200,), f"confirming upload part {number}")
    status, completed = stack._http_json(
        "POST",
        f"{uploads}/{session_id}/complete",
        headers=api,
        body={"sha256_hash": hashlib.sha256(video).hexdigest()},
    )
    _expect(status, completed, (200,), "completing the upload")

    def transcript_ready() -> bool:
        status, transcript = stack._http_json(
            "GET", f"{origin}/api/projects/{project_id}/transcript", headers=api
        )
        if status == 200 and isinstance(transcript, dict):
            if transcript.get("status") in ("failed", "captions_unavailable"):
                raise RuntimeError(f"the first-draft transcript failed: {transcript}")
            return transcript.get("status") == "ready"
        return False

    stack.wait_for("the first-draft transcript", transcript_ready, timeout=300, interval=5)
    status, plan = stack._http_json(
        "POST",
        f"{origin}/api/projects/{project_id}/plans",
        headers=api,
        body={"prompt": "perf smoke"},
    )
    plan = _expect(status, plan, (200, 201, 202), "creating a plan")
    plan_id = (plan.get("plan") or {}).get("id")
    render_body = {"include_captions": False, "include_broll": False}
    if plan_id:
        render_body["edit_plan_id"] = plan_id
    status, render = stack._http_json(
        "POST", f"{origin}/api/projects/{project_id}/renders", headers=api, body=render_body
    )
    render_id = _expect(status, render, (200, 202), "creating a render")["job_id"]
    return {
        "base_url": f"{origin}/api",
        "bearer_token": bearer,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "upload_id": str(session_id),
        "render_id": str(render_id),
        "admin_token": _stack_secret(stack, state, "admin_api_token"),
        "metrics_token": _stack_secret(stack, state, "metrics_bearer_token"),
    }


def _stack_secret(stack, state: dict, name: str) -> str:
    return stack.secret_path(state, name).read_text("utf-8").strip()


def cmd_smoke(args: argparse.Namespace) -> int:
    stack = _load(ROOT / "scripts" / "verify" / "cpu_stage_stack.py", "motionly_cpu_stage_stack")
    state = stack.load_state()
    # The edge serves the run's own test CA certificate; verify against it
    # (the seeding requests here and k6 below), never skip verification.
    stack.trust_edge_ca(state)
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
        metrics_token=seeded.get("metrics_token", ""),
        revision=revision,
        ca_cert=str(stack.secret_path(state, "edge_ca_cert")),
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
