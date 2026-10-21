#!/usr/bin/env python3
"""Run the CPU-stage stack for ``pnpm verify --stage stack``.

Subcommands, run in order by scripts/verify/stages.json:

``build``    Build every image in tests/cpu-stage/stack.json from this checkout.
``up``       Remove leftovers of this project only, generate per-run env and
             secrets outside the repository, start the resource sampler, and
             start the stack from empty volumes (``up --wait``).
``check``    Prove the stack works: every migration recorded once, a second
             ``up`` applies nothing, the migrated schema matches the code,
             health reports cpu-stage with the GPU stage not enabled, Auth,
             REST, Storage and the web app answer through the ingress, and
             every worker reports a CPU role with labelled fixture capabilities.
``verdict``  Stop the sampler and fail on any restart, OOM kill or failed
             one-shot. Writes test-results/cpu-stage/resources.json.
``mark``     Record the current time under a name (e.g. before the browser
             suite), for ``logscan --since-mark``.
``logscan``  Fail on error lines in any service's log since ``--since`` or a
             mark, except registered known defects
             (tests/cpu-stage/known-log-defects.json). Writes logscan.json.
``down``     Remove this project's containers, networks and volumes, and the
             generated secrets.

The size comes from CPU_STAGE_SIZE (slim by default; see the overlay).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STACK = json.loads((ROOT / "tests" / "cpu-stage" / "stack.json").read_text(encoding="utf-8"))
PROJECT = STACK["project"]
RESULTS = ROOT / "test-results" / "cpu-stage"
STATE_FILE = RESULTS / "state.json"
SAMPLER = ROOT / "scripts" / "verify" / "resource_sampler.py"
WRITER = ROOT / "scripts" / "release" / "write_compose_test_env.py"
MIGRATIONS = ROOT / "supabase" / "migrations"
NODE_IMAGE = (
    "node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20"
)
WORKER_SERVICES = {
    "slim": {"cpu-worker", "remotion-worker"},
    "full": {
        "cpu-ffmpeg-worker",
        "cpu-fixture-worker",
        "demucs-worker",
        "provider-worker",
        "planning-worker",
        "remotion-worker",
    },
}
FIXTURE_QUEUES = {"visual_analysis", "matte_extraction"}


class StackError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"cpu-stage: {message}", flush=True)


def load_state() -> dict:
    if not STATE_FILE.exists():
        raise StackError("the stack was not started (no test-results/cpu-stage/state.json)")
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def compose_args(state: dict) -> list[str]:
    args = ["docker", "compose", "--project-name", PROJECT, "--env-file", state["env_file"]]
    for path in STACK["compose_files"]:
        args += ["-f", str(ROOT / path)]
    # `demo-billing` brings up the mock payment gateway (Task 22); it is always
    # enabled in the CPU stage so /pay/* has an upstream and billing is demoed.
    return [*args, "--profile", state["size"], "--profile", "demo-billing"]


def run(
    args: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args, cwd=cwd, env=env, text=True, capture_output=capture, check=False
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-4000:] if capture else ""
        raise StackError(f"command failed ({result.returncode}): {' '.join(args[:6])} ...\n{detail}")
    return result


def compose(state: dict, *args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return run([*compose_args(state), *args], check=check, capture=capture)


def psql(state: dict, sql: str) -> str:
    """Run SQL as postgres inside the database container; returns unaligned rows."""
    result = compose(
        state,
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'PGPASSWORD="$(cat /run/secrets/postgres_password)" '
        'psql -X -q -t -A -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres -d postgres -c "$0"',
        sql,
    )
    return result.stdout.strip()


# HTTPS to the CDN simulator is verified against the run's own test CA.
_EDGE_TLS: ssl.SSLContext | None = None


def trust_edge_ca(state: dict) -> None:
    global _EDGE_TLS
    _EDGE_TLS = ssl.create_default_context(cafile=str(secret_path(state, "edge_ca_cert")))


def secret_path(state: dict, name: str) -> Path:
    return Path(state["secrets_root"]) / "secrets" / name


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        return None


def _opener(follow_redirects: bool) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [urllib.request.HTTPSHandler(context=_EDGE_TLS)]
    if not follow_redirects:
        handlers.append(_NoRedirects())
    return urllib.request.build_opener(*handlers)


def http_response(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    method: str = "GET",
    data: bytes | None = None,
    follow_redirects: bool = True,
) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(url, headers=headers or {}, method=method, data=data)
    try:
        with _opener(follow_redirects).open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, {k.lower(): v for k, v in response.headers.items()}, body
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        return error.code, {k.lower(): v for k, v in error.headers.items()}, body


def http(url: str, *, headers: dict[str, str] | None = None, timeout: float = 10.0) -> tuple[int, str]:
    status, _headers, body = http_response(url, headers=headers, timeout=timeout)
    return status, body


def wait_for(description: str, probe, *, timeout: float = 180.0, interval: float = 3.0):
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        try:
            result = probe()
        except (OSError, StackError, ValueError) as error:
            result, last = None, error
        if result:
            return result
        time.sleep(interval)
    raise StackError(f"timed out waiting for {description} (last: {last})")


def project_leftovers() -> list[str]:
    output = run(
        ["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"],
        capture=True,
    ).stdout.split()
    output += run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"],
        capture=True,
    ).stdout.split()
    return output


def remove_project_resources() -> None:
    """Remove only resources labelled with this Compose project."""
    label = f"label=com.docker.compose.project={PROJECT}"
    containers = run(["docker", "ps", "-aq", "--filter", label], capture=True).stdout.split()
    if containers:
        run(["docker", "rm", "-f", "-v", *containers], capture=True)
    volumes = run(["docker", "volume", "ls", "-q", "--filter", label], capture=True).stdout.split()
    if volumes:
        run(["docker", "volume", "rm", "-f", *volumes], capture=True)
    networks = run(["docker", "network", "ls", "-q", "--filter", label], capture=True).stdout.split()
    if networks:
        run(["docker", "network", "rm", *networks], capture=True, check=False)


# --- subcommands --------------------------------------------------------------


def cmd_build(_args: argparse.Namespace) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    for tag, image in STACK["images"].items():
        log(f"building {tag} from {image['dockerfile']}")
        started = time.monotonic()
        log_path = RESULTS / f"build-{tag.split(':', 1)[0]}.log"
        result = run(
            ["docker", "build", "--progress=plain", "--file", image["dockerfile"], "--tag", tag, "."],
            capture=True,
            check=False,
        )
        log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode != 0:
            tail = "\n".join((result.stdout + result.stderr).splitlines()[-40:])
            raise StackError(f"building {tag} failed (full log: {log_path}):\n{tail}")
        log(f"built {tag} in {time.monotonic() - started:.0f}s")


def cmd_up(args: argparse.Namespace) -> None:
    size = args.size
    if size not in STACK["sizes"]:
        raise StackError(f"unknown CPU-stage size {size!r}; expected one of {sorted(STACK['sizes'])}")
    RESULTS.mkdir(parents=True, exist_ok=True)
    if project_leftovers():
        log(f"removing leftover resources of project {PROJECT} from an earlier run")
        remove_project_resources()
    if project_leftovers():
        raise StackError("could not start from an empty environment: project resources remain")

    secrets_root = Path(tempfile.mkdtemp(prefix="motionly-cpu-stage-"))
    env_file = secrets_root / "compose.env"
    run(
        [
            sys.executable,
            str(WRITER),
            "--cpu-stage",
            str(ROOT / "tests" / "cpu-stage" / "stack.json"),
            "--env-file",
            str(env_file),
            "--secrets-dir",
            str(secrets_root / "secrets"),
        ],
        capture=True,
    )
    stop_file = RESULTS / "sampler.stop"
    stop_file.unlink(missing_ok=True)
    sampler = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            str(SAMPLER),
            "sample",
            "--project",
            PROJECT,
            "--output",
            str(RESULTS / "peaks.json"),
            "--stop-file",
            str(stop_file),
            "--interval",
            "5",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    state = {
        "size": size,
        "env_file": str(env_file),
        "secrets_root": str(secrets_root),
        "sampler_pid": sampler.pid,
        "started_at": time.time(),
    }
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    log(f"starting the {size} stack from empty volumes")
    started = time.monotonic()
    result = compose(state, "up", "-d", "--wait", "--wait-timeout", "600", check=False)
    state["startup_seconds"] = round(time.monotonic() - started, 1)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    if result.returncode != 0:
        status = compose(state, "ps", "-a", "--format", "{{.Service}}\t{{.State}}\t{{.Status}}", check=False)
        logs = compose(state, "logs", "--no-color", "--tail", "40", check=False)
        (RESULTS / "startup-failure.log").write_text(
            f"{result.stdout}\n{result.stderr}\n--- ps ---\n{status.stdout}\n--- logs ---\n{logs.stdout}",
            encoding="utf-8",
        )
        raise StackError(
            "stack did not become healthy; see test-results/cpu-stage/startup-failure.log\n"
            f"{status.stdout}\n{result.stderr[-2000:]}"
        )
    log(f"stack healthy in {state['startup_seconds']}s")
    wait_for_api_ready(state)
    seed_music_catalog(state)


def seed_music_catalog(state: dict) -> None:
    """Seed the generated 'Test track' music catalog (non-production only).

    FixtureMusicProvider was deleted; the licensed-catalog provider is the only
    music source. In the CPU stage it must serve real catalog rows, so once the
    API is ready (migrations applied, storage up) we run the seed CLI inside the
    API container. The CLI refuses production and synthesises generated test
    tracks into the private music-catalog bucket.
    """
    result = compose(
        state,
        "exec",
        "-T",
        "api",
        "python",
        "-m",
        "app.ops.seed_music_catalog",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise StackError(f"seeding the generated music catalog failed:\n{detail}")
    log("generated test-track music catalog seeded")


def wait_for_api_ready(state: dict) -> None:
    """The API's container health check is liveness only (/health/live).
    Poll readiness (/api/health/ready) through the edge so nothing that follows
    (checks, the browser suite, its log-scan mark) starts while the API's
    dependencies are still coming up."""
    trust_edge_ca(state)
    url = f"{STACK['public_origin']}/api/health/ready"

    def ready() -> bool:
        status, body = http(url)
        return status == 200 and json.loads(body).get("environment") == "cpu-stage"

    wait_for("the API to report ready through the edge", ready, timeout=180.0, interval=2.0)
    log("API ready through the edge")


def _migration_versions() -> list[str]:
    return sorted(path.name.split("_", 1)[0] for path in MIGRATIONS.glob("*.sql"))


def _applied_counts(state: dict) -> list[int]:
    """Migrations applied by each db-migrate run, oldest first (logs accumulate)."""
    logs = compose(state, "logs", "--no-color", "db-migrate").stdout
    counts = [int(count) for count in re.findall(r"db-migrate: applied (\d+) migration\(s\)", logs)]
    if not counts:
        raise StackError("db-migrate did not report how many migrations it applied")
    return counts


def check_migrations(state: dict) -> dict:
    expected = _migration_versions()
    recorded = psql(
        state, "select version from supabase_migrations.schema_migrations order by version"
    ).split()
    if recorded != expected:
        missing = sorted(set(expected) - set(recorded))
        extra = sorted(set(recorded) - set(expected))
        raise StackError(f"migration history mismatch: missing {missing}, unexpected {extra}")
    runs = _applied_counts(state)
    first = runs[0]
    if first != len(expected):
        raise StackError(f"db-migrate applied {first} migrations on an empty database, expected {len(expected)}")
    log(f"all {len(expected)} migrations applied from empty and recorded once")

    rerun = compose(state, "up", "-d", "--wait", "--wait-timeout", "600", check=False)
    if rerun.returncode != 0:
        raise StackError(f"second `up` (a release with no new migrations) failed:\n{rerun.stderr[-2000:]}")
    after = _applied_counts(state)
    if len(after) <= len(runs):
        raise StackError("a second `up` did not run db-migrate again")
    second = after[-1]
    if second != 0:
        raise StackError(f"a second `up` applied {second} migrations; expected 0")
    log("a second `up` ran db-migrate again and applied nothing")
    return {"migrations": len(expected), "applied_on_empty": first, "applied_on_rerun": second}


def check_schema(state: dict) -> dict:
    """The schema-matches-code test against the database the production image built."""
    env = dict(line.split("=", 1) for line in Path(state["env_file"]).read_text().splitlines())
    network = f"{PROJECT}_app"
    database_url = f"postgresql://postgres:{env['POSTGRES_PASSWORD']}@postgres:5432/postgres"
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "--env",
            f"DATABASE_URL={database_url}",
            "--volume",
            f"{ROOT}:/repo:ro",
            "--workdir",
            "/repo",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            NODE_IMAGE,
            "node",
            "scripts/verify/db-schema.mjs",
        ],
        capture=True,
    )
    schema_path = RESULTS / "db-schema.json"
    schema_path.write_text(result.stdout, encoding="utf-8")
    junit = ROOT / "test-results" / "junit" / "stack-api.xml"
    junit.parent.mkdir(parents=True, exist_ok=True)
    python = os.environ.get("MOTIONLY_PYTHON") or sys.executable
    test = run(
        [
            python,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit}",
            "tests/integration/test_schema_matches_code.py",
        ],
        cwd=ROOT / "apps" / "api",
        env={
            **os.environ,
            "APP_ENV": "test",
            "RUN_SUPABASE_CONTRACTS": "1",
            "DB_SCHEMA_JSON": str(schema_path),
        },
        check=False,
    )
    if test.returncode != 0:
        raise StackError("the schema built by db-migrate on the production image does not match the code")
    log("schema built by db-migrate on the production Postgres image matches the code")
    return {"schema_matches_code": True}


def check_edge(state: dict) -> dict:
    """Everything answers on the one public origin, routed by path."""
    origin = STACK["public_origin"]
    env = dict(line.split("=", 1) for line in Path(state["env_file"]).read_text().splitlines())
    if env["PUBLIC_ORIGIN"] != origin:
        raise StackError(f"generated PUBLIC_ORIGIN {env['PUBLIC_ORIGIN']} is not {origin}")

    def api_ready():
        status, body = http(f"{origin}/api/health/ready")
        return json.loads(body) if status == 200 else None

    health = wait_for("API readiness at /api", api_ready)
    if health.get("environment") != "cpu-stage" or health.get("gpu_stage") != "not_enabled":
        raise StackError(f"unexpected API health: {health}")
    log("API is ready at /api: environment cpu-stage, gpu_stage not_enabled")

    supabase = f"{origin}/supabase"
    status, body = http(f"{supabase}/auth/v1/health")
    if status != 200:
        raise StackError(f"Supabase Auth health at /supabase returned {status}: {body[:300]}")
    anon = {"apikey": env["SUPABASE_ANON_KEY"], "Authorization": f"Bearer {env['SUPABASE_ANON_KEY']}"}
    service = {
        "apikey": env["SUPABASE_SERVICE_ROLE_KEY"],
        "Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE_KEY']}",
    }
    status, body = http(f"{supabase}/rest/v1/projects?select=id&limit=1", headers=anon)
    # Signed-out callers get nothing: no table privilege (42501) or no rows (RLS).
    denied = status in {401, 403} and '"42501"' in body
    if not (denied or (status == 200 and json.loads(body) == [])):
        raise StackError(f"REST as anon must not return rows; got {status}: {body[:300]}")
    status, body = http(f"{supabase}/rest/v1/projects?select=id&limit=1", headers=service)
    if status != 200 or not isinstance(json.loads(body), list):
        raise StackError(f"REST as the service role returned {status}: {body[:300]}")
    status, body = http(f"{supabase}/storage/v1/bucket", headers=service)
    if status != 200:
        raise StackError(f"Storage bucket list returned {status}: {body[:300]}")
    buckets = sorted(bucket["id"] for bucket in json.loads(body))
    if not buckets:
        raise StackError("Storage has no buckets; the migrations create them")
    log(f"Auth, REST (RLS) and Storage answer at /supabase; buckets: {', '.join(buckets)}")

    # Realtime answers through Kong at /supabase/realtime/v1. /api/ping needs no
    # auth and reports the Phoenix endpoint (and so its realtime schema) is up.
    status, body = http(f"{supabase}/realtime/v1/api/ping")
    if status != 200:
        raise StackError(f"Realtime health at /supabase/realtime/v1 returned {status}: {body[:300]}")
    log("Realtime answers at /supabase/realtime/v1 (/api/ping)")

    web = check_web_runtime(origin, env)
    status, body = http(f"{origin}/app-api/health")
    if status != 200 or json.loads(body).get("status") != "ok":
        raise StackError(f"web /app-api/health returned {status}: {body[:300]}")
    log("web reaches the API over the private network (/app-api/health ok)")
    return {"api_health": health, "storage_buckets": buckets, "web": web}


ORIGIN_GUARD_HEADER = "X-Motionly-Origin-Verify"


def check_cdn(state: dict) -> dict:
    """The CloudFront simulator applies the edge contract in front of the origin."""
    origin = STACK["public_origin"]

    status, headers, _ = http_response(f"{STACK['edge_http']}/login", follow_redirects=False)
    if status != 301 or headers.get("location") != f"{origin}/login":
        raise StackError(f"plain-HTTP GET must 301 to HTTPS; got {status} {headers.get('location')}")
    status, headers, _ = http_response(
        f"{STACK['edge_http']}/api/projects", method="POST", data=b"{}", follow_redirects=False
    )
    if status != 307 or headers.get("location") != f"{origin}/api/projects":
        raise StackError(f"plain-HTTP POST must 307 to HTTPS; got {status} {headers.get('location')}")

    # The app builds absolute redirects from the viewer's https origin.
    status, headers, _ = http_response(f"{origin}/dashboard", follow_redirects=False)
    expected = f"{origin}/login?next=%2Fdashboard"
    if status not in {302, 303, 307} or headers.get("location") != expected:
        raise StackError(
            f"signed-out /dashboard must redirect to {expected}; got {status} {headers.get('location')}"
        )

    status, headers, body = http_response(f"{origin}/login")
    if headers.get("strict-transport-security") != "max-age=63072000":
        raise StackError(f"the edge must add HSTS; got {headers.get('strict-transport-security')}")
    if "server" in headers or "cloudfront.net (CloudFront)" not in headers.get("via", ""):
        raise StackError(f"edge response headers are wrong: {sorted(headers)}")
    for path in ("/login", "/api/health/ready"):
        caches = [http_response(f"{origin}{path}")[1].get("x-cache") for _ in range(2)]
        if caches != ["Miss from cloudfront"] * 2:
            raise StackError(f"{path} must never be cached at the edge; got {caches}")
    _, headers, _ = http_response(f"{origin}/supabase/auth/v1/health")
    if any(name.startswith("x-kong-") for name in headers):
        raise StackError("the edge must remove Kong's latency headers")

    asset = re.search(r'/_next/static/[^"\'\s]+\.js', body)
    if not asset:
        raise StackError("web /login references no /_next/static script")
    caches = [http_response(f"{origin}{asset.group(0)}")[1].get("x-cache") for _ in range(2)]
    if caches != ["Miss from cloudfront", "Hit from cloudfront"]:
        raise StackError(f"build assets must be cached at the edge; got {caches}")
    log("edge: HTTPS redirects, absolute https app redirects, HSTS, assets cached, pages and APIs not")
    return {"static_asset_cached": True, "dynamic_uncached": True}


def check_origin_guard(state: dict) -> dict:
    """Requests that bypass the edge (no origin secret) never reach a service."""
    direct = STACK["origin_direct"]
    secret = secret_path(state, "edge_origin_verify").read_text(encoding="utf-8").strip()
    attempts = {
        "no secret": {},
        "a wrong secret": {ORIGIN_GUARD_HEADER: "w" * len(secret)},
        "a short prefix of the secret": {ORIGIN_GUARD_HEADER: secret[:-1]},
    }
    for path in ("/login", "/api/health/ready", "/supabase/auth/v1/health"):
        for label, headers in attempts.items():
            status, body = http(f"{direct}{path}", headers=headers)
            if status != 403:
                raise StackError(f"direct {path} with {label} returned {status}: {body[:200]}")
    status, body = http(f"{direct}/api/health/ready", headers={ORIGIN_GUARD_HEADER: secret})
    if status != 200:
        raise StackError(f"the origin guard refused the real secret: {status} {body[:200]}")
    log("origin guard: direct requests without the secret get 403 on pages, /api and /supabase")
    return {"direct_requests_refused": True}


def check_web_runtime(origin: str, env: dict[str, str]) -> dict:
    """The web image is configured at runtime and sends a per-request CSP nonce."""
    nonces = []
    for _ in range(2):
        status, headers, body = http_response(f"{origin}/login")
        if status != 200:
            raise StackError(f"web /login returned {status}: {body[:300]}")
        policy = headers.get("content-security-policy", "")
        directives = {
            part.split()[0]: part.split()[1:] for part in policy.split(";") if part.strip()
        }
        scripts = directives.get("script-src", [])
        nonce = next((item[7:-1] for item in scripts if item.startswith("'nonce-")), None)
        if not nonce or "'strict-dynamic'" not in scripts or "'unsafe-inline'" in scripts:
            raise StackError(f"web CSP must use a nonce, not unsafe-inline: {policy}")
        # Upgrading belongs to HTTPS origins only; on plain HTTP it breaks every load.
        if ("upgrade-insecure-requests" in directives) != origin.startswith("https:"):
            raise StackError(f"upgrade-insecure-requests does not match the {origin} origin")
        if f'nonce="{nonce}"' not in body:
            raise StackError("the page's inline script does not carry the CSP nonce")
        nonces.append(nonce)

        match = re.search(
            r'<script id="motionly-public-config" type="application/json">(.*?)</script>', body
        )
        if not match:
            raise StackError("web /login does not embed the runtime public configuration")
        public = json.loads(match.group(1))
        expected = {
            "publicOrigin": origin,
            "supabaseAnonKey": env["SUPABASE_ANON_KEY"],
            "billingEnabled": False,
        }
        if public != expected:
            raise StackError(f"unexpected public configuration: {public}")
        for internal in ("supabase-gateway", "http://api:8000", env["SUPABASE_SERVICE_ROLE_KEY"]):
            if internal in body:
                raise StackError(f"web /login leaks an internal value: {internal[:24]}")
    if nonces[0] == nonces[1]:
        raise StackError("the CSP nonce must change on every request")
    log("web /login: runtime public config, per-request CSP nonce, no internal URLs")
    return {"csp_nonce_per_request": True, "public_config_keys": sorted(expected)}


def _http_json(
    method: str, url: str, *, headers: dict[str, str], body: object | None = None
) -> tuple[int, object]:
    request = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"content-type": "application/json", **headers},
    )
    try:
        with _opener(follow_redirects=True).open(request, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return error.code, raw.decode("utf-8", "replace")[:300]


def _jwt_claims(token: str) -> dict:
    import base64

    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _stack_env(state: dict) -> dict[str, str]:
    lines = Path(state["env_file"]).read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


RATE_LIMITED_VIEWER = "198.51.100.20"
OTHER_VIEWER = "198.51.100.21"
RATE_LIMIT_PROBE_ATTEMPTS = 300


def check_auth_rate_limit(state: dict) -> dict:
    """Auth limits each viewer separately. The check speaks to the origin as
    CloudFront does (origin secret, CloudFront-Viewer-Address with a new source
    port each time): one viewer's failed sign-ins are eventually refused with
    429, and another viewer is still served."""
    secret = secret_path(state, "edge_origin_verify").read_text(encoding="utf-8").strip()
    anon = _stack_env(state)["SUPABASE_ANON_KEY"]
    url = f"{STACK['origin_direct']}/supabase/auth/v1/token?grant_type=password"
    body = {"email": "rate-limit-probe@motionly.test", "password": "not-the-password-1A!"}

    def attempt(viewer: str, port: int) -> int:
        headers = {
            "apikey": anon,
            ORIGIN_GUARD_HEADER: secret,
            "CloudFront-Viewer-Address": f"{viewer}:{port}",
        }
        status, _ = _http_json("POST", url, headers=headers, body=body)
        return status

    limited_after = next(
        (
            count
            for count in range(RATE_LIMIT_PROBE_ATTEMPTS)
            if attempt(RATE_LIMITED_VIEWER, 40000 + count) == 429
        ),
        None,
    )
    if limited_after is None:
        raise StackError(
            f"Auth never rate-limited one viewer in {RATE_LIMIT_PROBE_ATTEMPTS} sign-in attempts:"
            " GOTRUE_RATE_LIMIT_HEADER or the ingress's X-Motionly-Viewer-IP is not working"
        )
    if attempt(OTHER_VIEWER, 40000) == 429:
        raise StackError("another viewer was rate-limited too: Auth does not key on the viewer")
    log(f"Auth rate-limits per viewer (one viewer limited after {limited_after} attempts)")
    return {"limited_after": limited_after, "other_viewer_limited": False}


def check_sign_in(state: dict) -> dict:
    """A user signs in with a password, as a browser does (no extra headers), and
    the token works for REST under RLS and for the API."""
    origin = STACK["public_origin"]
    env = _stack_env(state)
    anon = env["SUPABASE_ANON_KEY"]
    service = {
        "apikey": env["SUPABASE_SERVICE_ROLE_KEY"],
        "authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE_KEY']}",
    }
    email = f"cpu-stage-sign-in-{os.getpid()}-{int(time.time())}@motionly.test"
    password = f"Cpu-Stage-{os.urandom(12).hex()}-Aa1!"
    status, user = _http_json(
        "POST",
        f"{origin}/supabase/auth/v1/admin/users",
        headers=service,
        body={"email": email, "password": password, "email_confirm": True},
    )
    if status != 200 or not isinstance(user, dict):
        raise StackError(f"creating a confirmed user returned {status}: {user}")
    try:
        status, session = _http_json(
            "POST",
            f"{origin}/supabase/auth/v1/token?grant_type=password",
            headers={"apikey": anon},
            body={"email": email, "password": password},
        )
        if status != 200 or not isinstance(session, dict):
            raise StackError(f"password sign-in returned {status}: {session}")
        claims = _jwt_claims(session["access_token"])
        if claims.get("aud") != "authenticated" or claims.get("role") != "authenticated":
            raise StackError(
                "access tokens must carry aud and role 'authenticated'; got "
                f"aud={claims.get('aud')!r} role={claims.get('role')!r}"
            )
        user_headers = {"apikey": anon, "authorization": f"Bearer {session['access_token']}"}
        status, memberships = _http_json(
            "GET",
            f"{origin}/supabase/rest/v1/workspace_members?select=workspace_id,user_id",
            headers=user_headers,
        )
        if status != 200 or not isinstance(memberships, list) or not memberships:
            raise StackError(f"REST as the signed-in user returned {status}: {memberships}")
        if {row["user_id"] for row in memberships} != {user["id"]}:
            raise StackError("RLS let the user read another user's workspace membership")
        workspace_id = memberships[0]["workspace_id"]
        status, projects = _http_json(
            "GET",
            f"{origin}/api/projects",
            headers={
                "authorization": f"Bearer {session['access_token']}",
                "x-motionly-workspace-id": workspace_id,
            },
        )
        if status != 200:
            raise StackError(f"API /projects as the signed-in user returned {status}: {projects}")
    finally:
        _http_json("DELETE", f"{origin}/supabase/auth/v1/admin/users/{user['id']}", headers=service)
    log("a confirmed user signs in through /supabase; REST (RLS) and the API accept the token")
    return {"token_aud": claims["aud"], "token_role": claims["role"], "rest_rows": len(memberships)}


def check_workers(state: dict) -> dict:
    expected = WORKER_SERVICES[state["size"]]
    hostnames = {}
    for service in expected:
        container = compose(state, "ps", "-q", service).stdout.strip()
        if not container:
            raise StackError(f"worker service {service} has no container")
        hostnames[container[:12]] = service

    def heartbeats():
        rows = psql(
            state,
            "select coalesce(json_agg(json_build_object('worker_id', worker_id, "
            "'worker_role', worker_role, 'queues', queues, 'metadata', metadata)), '[]') "
            "from public.worker_heartbeats",
        )
        found = {}
        for row in json.loads(rows or "[]"):
            prefix = row["worker_id"].rsplit("-", 1)[0]
            if prefix in hostnames:
                found[hostnames[prefix]] = row
        return found if set(found) == expected else None

    found = wait_for("a heartbeat from every worker service", heartbeats, timeout=240)
    report = {}
    for service, row in sorted(found.items()):
        metadata = row["metadata"] or {}
        if row["worker_role"] != "cpu" or metadata.get("ml_execution_mode") != "test_fixture":
            raise StackError(f"{service} must report role cpu in test_fixture mode: {row}")
        fixtures = {item["queue"]: item for item in metadata.get("fixture_capabilities", [])}
        served = set(row["queues"] or []) & FIXTURE_QUEUES
        if set(fixtures) != served or any(not item.get("label") for item in fixtures.values()):
            raise StackError(f"{service} must label every fixture capability it serves: {row}")
        report[service] = {"queues": row["queues"], "fixture_capabilities": sorted(fixtures)}
    log("every worker reports role cpu; fixture capabilities: " + ", ".join(
        f"{service}={'/'.join(item['fixture_capabilities']) or 'none'}" for service, item in report.items()
    ))
    return report


def cmd_check(_args: argparse.Namespace) -> None:
    state = load_state()
    trust_edge_ca(state)
    summary = {
        "size": state["size"],
        "startup_seconds": state.get("startup_seconds"),
        "migrations": check_migrations(state),
        "edge": check_edge(state),
        "cdn": check_cdn(state),
        "origin_guard": check_origin_guard(state),
        "sign_in": check_sign_in(state),
        "auth_rate_limit": check_auth_rate_limit(state),
        "workers": check_workers(state),
        "schema": check_schema(state),
    }
    (RESULTS / "checks.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def _stop_sampler(state: dict) -> None:
    stop_file = RESULTS / "sampler.stop"
    stop_file.write_text("stop\n", encoding="utf-8")
    pid = state.get("sampler_pid")
    if not pid:
        return
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(1)
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _load_sampler_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("motionly_resource_sampler", SAMPLER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def cmd_verdict(_args: argparse.Namespace) -> None:
    state = load_state()
    _stop_sampler(state)
    sampler = _load_sampler_module()
    states = sampler.container_states(PROJECT)
    peaks_path = RESULTS / "peaks.json"
    peaks = json.loads(peaks_path.read_text(encoding="utf-8")) if peaks_path.exists() else {}
    problems = sampler.verdict(states, one_shots=set(STACK["one_shots"]))
    report = {
        "size": state["size"],
        "startup_seconds": state.get("startup_seconds"),
        "sampling": {key: peaks.get(key) for key in ("interval_seconds", "sample_rounds", "duration_seconds")},
        "services": {
            service: {
                **container,
                "peak_memory_bytes": peaks.get("peaks", {}).get(service, {}).get("memory_bytes"),
                "peak_cpu_percent": peaks.get("peaks", {}).get(service, {}).get("cpu_percent"),
            }
            for service, container in sorted(states.items())
        },
        "problems": problems,
    }
    (RESULTS / "resources.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for service, entry in report["services"].items():
        peak = entry["peak_memory_bytes"]
        limit = entry["memory_limit_bytes"]
        log(
            f"{service:22} peak {peak / 1024**2 if peak else 0:7.1f} MiB"
            f" / cap {limit / 1024**2 if limit else 0:6.0f} MiB"
            f"  cpu {entry['peak_cpu_percent'] or 0:6.1f}%  restarts {entry['restart_count']}"
        )
    if problems:
        raise StackError("container health problems:\n  " + "\n  ".join(problems))
    log("no restarts, no OOM kills, every one-shot exited 0, every service still running")


# Error lines in any service's log. Each format the stack's services use:
# Python logging (JSON levelname, tracebacks), uvicorn and nginx/Kong access
# lines (5xx), Go services (JSON level), Storage (pino numeric level), the CDN
# simulator's JSON access log, Node workers' JSON failure events,
# Postgres's own message prefix, and Node/Next error output.
LOG_ERROR_PATTERNS = [
    re.compile(r"^Traceback \(most recent call last\):"),
    re.compile(r'"levelname":\s*"(ERROR|CRITICAL)"'),
    re.compile(r'"level":\s*"(error|fatal|panic|dpanic)"', re.IGNORECASE),
    re.compile(r'"level":\s*(50|60)\b'),
    re.compile(r'"event":\s*"[a-z_]*(_failed|_error|_stalled|_crash)"'),
    re.compile(r'HTTP/[\d.]+"\s+5\d\d\b'),
    re.compile(r'"status":\s*5\d\d\b'),
    re.compile(r"\bUTC \[\d+\] (\S+ )?(ERROR|FATAL|PANIC):"),
    re.compile(r"^\s*(⨯\s*)?([A-Za-z]*Error|Uncaught [A-Za-z]*|Unhandled[^:]*):"),
    re.compile(r"^\s*Unhandled promise rejection\b", re.IGNORECASE),
    # GoTrue skips its rate limit when a request lacks GOTRUE_RATE_LIMIT_HEADER:
    # some Auth call reached it without the viewer's address (fail-open).
    re.compile(r"rate limiting is not applied"),
]


def _json_line(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _timestamp(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


TEMPORAL_UNLOAD_RACE_SECONDS = 1.0


def _temporal_unload_race(record: dict, unloaded: dict[str, datetime]) -> bool:
    """Temporal (1.27.1) unloads idle task-queue partitions; when its periodic
    ack-level save races the unload, it logs the cancelled save at error level
    and carries on ("saving ack is not critical", service/matching/task_reader.go).
    Only that exact line, right after the same queue was unloaded, is ignored."""
    if not (
        record.get("msg") == "Persistent store operation failure"
        and record.get("store-operation") == "update-task-queue"
        and record.get("error") == "context canceled"
    ):
        return False
    stopped = unloaded.get(str(record.get("wf-task-queue-name")))
    at = _timestamp(record.get("ts"))
    return (
        stopped is not None
        and at is not None
        and 0 <= (at - stopped).total_seconds() <= TEMPORAL_UNLOAD_RACE_SECONDS
    )


def log_problems(service: str, text: str, *, ignored: list[str] | None = None) -> list[str]:
    """Return ``service: line`` for every error line in a service's log text.

    A Python traceback is reported once, with the exception it ends in.
    Upstream noise that is not an error (see ``_temporal_unload_race``) goes to
    ``ignored`` instead, so the report still shows it.
    """
    problems: list[str] = []
    unloaded: dict[str, datetime] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if _TRACEBACK.match(line):
            end = index + 1
            while end < len(lines) and (
                lines[end].startswith((" ", "\t")) or not lines[end].strip()
            ):
                end += 1
            final = lines[end].strip() if end < len(lines) else ""
            problems.append(f"{service}: {line.strip()} … {final}" if final else f"{service}: {line.strip()}")
            index = end + 1
            continue
        record = _json_line(line) if service == "temporal" else None
        if record and record.get("msg") == "Stopped physicalTaskQueueManager":
            at = _timestamp(record.get("ts"))
            if at is not None:
                unloaded[str(record.get("wf-task-queue-name"))] = at
        if any(pattern.search(line) for pattern in LOG_ERROR_PATTERNS):
            if record and _temporal_unload_race(record, unloaded):
                if ignored is not None:
                    ignored.append(f"{service}: {line.strip()}")
            else:
                problems.append(f"{service}: {line.strip()}")
        index += 1
    return problems


_TRACEBACK = LOG_ERROR_PATTERNS[0]
KNOWN_LOG_DEFECTS = ROOT / "tests" / "cpu-stage" / "known-log-defects.json"


def classify_log_problems(
    problems: list[str], known: list[dict]
) -> tuple[list[str], dict[str, int]]:
    """Split problems into new ones and counts of registered known defects.

    A known defect matches only lines from its own service.
    """
    compiled = [(entry["id"], entry["service"], re.compile(entry["pattern"])) for entry in known]
    new: list[str] = []
    matched: dict[str, int] = {}
    for problem in problems:
        service, _, line = problem.partition(": ")
        for defect_id, defect_service, pattern in compiled:
            if service == defect_service and pattern.search(line):
                matched[defect_id] = matched.get(defect_id, 0) + 1
                break
        else:
            new.append(problem)
    return new, matched


def scan_start(state: dict, args: argparse.Namespace) -> str:
    """The RFC 3339 time the log scan starts at: --since, or a recorded mark."""
    if args.since:
        return args.since
    marks = state.get("marks", {})
    if args.since_mark not in marks:
        raise StackError(f"no mark named {args.since_mark!r}; run `mark {args.since_mark}` first")
    return marks[args.since_mark]


def cmd_mark(args: argparse.Namespace) -> None:
    state = load_state()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state.setdefault("marks", {})[args.name] = now
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    log(f"mark {args.name} at {now}")


def cmd_logscan(args: argparse.Namespace) -> None:
    state = load_state()
    since = scan_start(state, args)
    names = compose(state, "ps", "--all", "--services").stdout.split()
    problems: list[str] = []
    ignored: list[str] = []
    for service in sorted(names):
        logs = compose(state, "logs", "--no-color", "--no-log-prefix", "--since", since, service, check=False)
        problems.extend(log_problems(service, logs.stdout + logs.stderr, ignored=ignored))
    known = json.loads(KNOWN_LOG_DEFECTS.read_text(encoding="utf-8"))["defects"]
    new, matched = classify_log_problems(problems, known)
    report = {
        "since": since,
        "services": sorted(names),
        "problems": new,
        "known_defects": [
            {**entry, "lines": matched[entry["id"]]} for entry in known if entry["id"] in matched
        ],
        "ignored_upstream_noise": ignored,
    }
    (RESULTS / "logscan.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for entry in report["known_defects"]:
        log(f"known defect {entry['id']} ({entry['task']}): {entry['lines']} log lines")
    if ignored:
        log(f"ignored {len(ignored)} Temporal task-queue unload line(s) (not errors)")
    if new:
        shown = "\n  ".join(problem[:400] for problem in new[:40])
        more = f"\n  … and {len(new) - 40} more" if len(new) > 40 else ""
        raise StackError(f"{len(new)} error lines in service logs since {since}:\n  {shown}{more}")
    log(f"no new error lines in {len(names)} service logs since {since}")


def cmd_down(_args: argparse.Namespace) -> None:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    if state:
        _stop_sampler(state)
        if Path(state.get("env_file", "")).exists():
            logs = compose(state, "logs", "--no-color", "--timestamps", check=False)
            (RESULTS / "compose.log").write_text(logs.stdout, encoding="utf-8")
            compose(state, "down", "--volumes", "--remove-orphans", "--timeout", "20", check=False)
    remove_project_resources()
    if state.get("secrets_root"):
        shutil.rmtree(state["secrets_root"], ignore_errors=True)
    STATE_FILE.unlink(missing_ok=True)
    (RESULTS / "sampler.stop").unlink(missing_ok=True)
    if project_leftovers():
        raise StackError(f"resources of project {PROJECT} remain after teardown")
    log("removed the project's containers, networks, volumes and generated secrets")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build")
    up = commands.add_parser("up")
    up.add_argument("--size", default=os.environ.get("CPU_STAGE_SIZE", "slim"))
    commands.add_parser("check")
    commands.add_parser("verdict")
    mark = commands.add_parser("mark")
    mark.add_argument("name", help="records the current time under this name, for logscan")
    logscan = commands.add_parser("logscan")
    since = logscan.add_mutually_exclusive_group(required=True)
    since.add_argument("--since", help="RFC 3339 time, e.g. 2026-10-07T06:00:00Z")
    since.add_argument("--since-mark", help="a time recorded with `mark`")
    commands.add_parser("down")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    handlers = {
        "build": cmd_build,
        "up": cmd_up,
        "check": cmd_check,
        "verdict": cmd_verdict,
        "mark": cmd_mark,
        "logscan": cmd_logscan,
        "down": cmd_down,
    }
    try:
        handlers[args.command](args)
    except StackError as error:
        print(f"cpu-stage: FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
