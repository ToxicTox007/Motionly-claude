"""Task 6: only CloudFront reaches the origin.

The ingress (Caddy) refuses every request that lacks the secret
X-Motionly-Origin-Verify header CloudFront adds (infra/aws/edge-contract.json),
strips the header before routing, and tells the services that the viewer used
HTTPS (CloudFront redirects every plain-HTTP viewer request). The CPU stage puts
the CDN simulator in front of the same ingress.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"
CONTRACT = json.loads((INFRA / "aws" / "edge-contract.json").read_text(encoding="utf-8"))
GUARD = INFRA / "Caddyfile.origin-guard"
ENTRYPOINT = INFRA / "caddy-entrypoint.sh"
ROUTES = INFRA / "Caddyfile.routes"
BASE = INFRA / "docker-compose.gpu.yml"
CPU_STAGE = INFRA / "docker-compose.cpu-stage.yml"
LOCAL_AUDIT = INFRA / "docker-compose.local-audit.yml"
STACK = ROOT / "tests" / "cpu-stage" / "stack.json"
WRITER = ROOT / "scripts" / "release" / "write_compose_test_env.py"
GUARD_SECRETS = ("edge_origin_verify", "edge_origin_verify_previous")
HEADER = CONTRACT["origin_guard"]["header"]


class _ComposeLoader(yaml.SafeLoader):
    """Understands Compose's !override and !reset merge tags."""


for _tag in ("!override", "!reset"):
    _ComposeLoader.add_constructor(
        _tag,
        lambda loader, node: (
            loader.construct_sequence(node)
            if isinstance(node, yaml.SequenceNode)
            else loader.construct_mapping(node)
            if isinstance(node, yaml.MappingNode)
            else loader.construct_scalar(node)
        ),
    )


def _yaml(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)  # noqa: S506


def _code(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )


def _secret_file(value_source: str) -> str:
    match = re.fullmatch(r"ssm:/motionly/\{environment\}/([a-z0-9/-]+)", value_source)
    assert match, value_source
    return re.sub(r"[/-]", "_", match.group(1))


def test_the_contract_names_the_secrets_the_ingress_reads() -> None:
    guard = CONTRACT["origin_guard"]
    sources = guard["value_sources"]

    # CloudFront sends the current value; the ingress also accepts the previous
    # one while a rotation is deploying.
    assert CONTRACT["origin"]["custom_headers"][0]["value_source"] == sources["current"]
    assert sources["previous"] != sources["current"]
    assert tuple(_secret_file(sources[key]) for key in ("current", "previous")) == GUARD_SECRETS
    assert guard["previous_optional"] is True
    assert re.fullmatch(guard["value_format"], "a" * 32)
    assert not re.fullmatch(guard["value_format"], "a" * 31)
    assert not re.fullmatch(guard["value_format"], "a" * 31 + "*")


def test_the_guard_refuses_requests_without_the_secret_before_any_route() -> None:
    code = _code(GUARD)

    name = re.search(r"^@(\w+) \{\n\tnot \{$", code, flags=re.MULTILINE)
    assert name, "the guard is one negated matcher block"
    name = name.group(1)
    conditions = code.split("\tnot {", 1)[1].split("\n\t}", 1)[0]
    assert f"header_regexp {HEADER} {CONTRACT['origin_guard']['value_format']}" in conditions
    assert f"header {HEADER} {{env.MOTIONLY_ORIGIN_VERIFY_CURRENT}}" in conditions
    assert f"header {HEADER} {{env.MOTIONLY_ORIGIN_VERIFY_PREVIOUS}}" in conditions
    status = CONTRACT["origin_guard"]["reject_status"]
    assert re.search(rf"handle @{name} \{{\s*respond \"[^\"]+\" {status}\s*\}}", code)
    assert "reverse_proxy" not in code


def test_routes_strip_the_secret_and_report_the_https_viewer() -> None:
    code = _code(ROUTES)
    proxies = re.findall(r"reverse_proxy (\S+) \{(.*?)\}", code, flags=re.DOTALL)

    assert [upstream for upstream, _ in proxies] == [
        "api:8000",
        "supabase-gateway:8000",
        "web:3000",
    ]
    for upstream, block in proxies:
        assert f"header_up -{HEADER}" in block, upstream
        # Every behavior redirects plain-HTTP viewers, so the viewer used HTTPS.
        assert "header_up X-Forwarded-Proto https" in block, upstream
    assert all(
        behavior["viewer_protocol_policy"] == "redirect-to-https"
        for behavior in CONTRACT["behaviors"]
    )


@pytest.mark.parametrize(
    ("caddyfile", "guarded"),
    [("Caddyfile", True), ("Caddyfile.cpu-stage", True), ("Caddyfile.local-audit", False)],
)
def test_ingresses_behind_cloudfront_import_the_guard_before_the_routes(
    caddyfile: str, guarded: bool
) -> None:
    code = _code(INFRA / caddyfile)
    guard_import = "import /etc/caddy/origin-guard.caddy"

    if guarded:
        assert code.index(guard_import) < code.index("import /etc/caddy/routes.caddy")
    else:
        # The local audit is reached directly on loopback, without CloudFront.
        assert guard_import not in code


def test_the_production_origin_is_plain_http_on_port_80() -> None:
    code = _code(INFRA / "Caddyfile")
    ingress = _yaml(BASE)["services"]["ingress"]

    assert re.search(r"^http://\{\$APP_DOMAIN\} \{", code, flags=re.MULTILINE)
    assert ingress["ports"] == [f"{CONTRACT['origin']['http_port']}:80"]


def test_the_production_ingress_starts_through_the_guard_entrypoint() -> None:
    ingress = _yaml(BASE)["services"]["ingress"]
    secrets = _yaml(BASE)["secrets"]

    assert ingress["entrypoint"] == ["/bin/sh", "/etc/caddy/caddy-entrypoint.sh"]
    assert ingress["command"] == [
        "caddy",
        "run",
        "--config",
        "/etc/caddy/Caddyfile",
        "--adapter",
        "caddyfile",
    ]
    assert "./caddy-entrypoint.sh:/etc/caddy/caddy-entrypoint.sh:ro" in ingress["volumes"]
    assert "./Caddyfile.origin-guard:/etc/caddy/origin-guard.caddy:ro" in ingress["volumes"]
    assert ingress["secrets"] == list(GUARD_SECRETS)
    for name in GUARD_SECRETS:
        assert secrets[name]["file"].endswith(f"}}/{name}"), name


def test_the_local_audit_ingress_runs_without_the_guard() -> None:
    ingress = _yaml(LOCAL_AUDIT)["services"]["ingress"]

    assert ingress["entrypoint"] == ["caddy"]
    assert ingress["command"] == [
        "run",
        "--config",
        "/etc/caddy/Caddyfile",
        "--adapter",
        "caddyfile",
    ]
    assert ingress["secrets"] == []


def _run_entrypoint(tmp_path: Path, current: str | None, previous: str | None):
    secrets = tmp_path / "secrets"
    secrets.mkdir(exist_ok=True)
    for name, value in zip(GUARD_SECRETS, (current, previous), strict=True):
        path = secrets / name
        path.unlink(missing_ok=True)
        if value is not None:
            path.write_text(value, encoding="utf-8")
    probe = 'printf "%s|%s" "$MOTIONLY_ORIGIN_VERIFY_CURRENT" "$MOTIONLY_ORIGIN_VERIFY_PREVIOUS"'
    return subprocess.run(
        ["sh", str(ENTRYPOINT), "sh", "-c", probe],
        env={**os.environ, "ORIGIN_GUARD_SECRETS_DIR": str(secrets)},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_the_entrypoint_exports_valid_secrets_and_refuses_unsafe_ones(tmp_path: Path) -> None:
    current = "c" * 40
    previous = "p_" + "q" * 38

    exported = _run_entrypoint(tmp_path, current + "\n", previous + "\n")
    assert exported.returncode == 0, exported.stderr
    assert exported.stdout == f"{current}|{previous}"

    without_previous = _run_entrypoint(tmp_path, current, None)
    assert without_previous.returncode == 0, without_previous.stderr
    assert without_previous.stdout == f"{current}|"
    empty_previous = _run_entrypoint(tmp_path, current, "\n")
    assert empty_previous.stdout == f"{current}|"

    for bad_current in (
        None,
        "",
        "\n",
        "short",
        "x" * 31,
        "x" * 40 + " ",
        "x" * 40 + "*",
        "x" * 257,
    ):
        refused = _run_entrypoint(tmp_path, bad_current, None)
        assert refused.returncode != 0, repr(bad_current)
        assert "origin guard" in refused.stderr
        assert refused.stdout == ""
    refused = _run_entrypoint(tmp_path, current, "bad value")
    assert refused.returncode != 0


def test_the_cpu_stage_puts_the_cdn_simulator_in_front_of_the_guarded_ingress() -> None:
    services = _yaml(CPU_STAGE)["services"]
    edge = services["edge"]
    stack = json.loads(STACK.read_text(encoding="utf-8"))

    assert re.fullmatch(r"node:24-bookworm-slim@sha256:[0-9a-f]{64}", edge["image"])
    assert edge["command"] == ["node", "/opt/cdn-sim/main.mjs"]
    assert "../scripts/cdn-sim:/opt/cdn-sim:ro" in edge["volumes"]
    assert "./aws/edge-contract.json:/opt/edge-contract.json:ro" in edge["volumes"]
    environment = edge["environment"]
    assert environment["CDN_SIM_ORIGIN"] == "http://ingress:80"
    assert environment["CDN_SIM_CONTRACT"] == "/opt/edge-contract.json"
    assert environment["CDN_SIM_SECRETS_DIR"] == "/run/secrets"
    assert environment["CDN_SIM_PUBLIC_HTTPS_PORT"] == "18443"
    assert set(edge["secrets"]) == {"edge_origin_verify", "edge_tls_cert", "edge_tls_key"}
    assert edge["ports"] == ["127.0.0.1:18443:8443", "127.0.0.1:18081:8080"]
    assert edge["user"] == "1000:1000" and edge["read_only"] is True
    assert "mem_limit" in edge
    # The ingress stays reachable on loopback so the checker can prove the
    # guard refuses direct requests.
    assert services["ingress"]["ports"] == ["127.0.0.1:18080:80"]
    assert stack["public_origin"] == "https://127.0.0.1:18443"
    assert stack["edge_http"] == "http://127.0.0.1:18081"
    assert stack["origin_direct"] == "http://127.0.0.1:18080"
    for name, service in services.items():
        if name not in {"ingress", "edge"}:
            assert "ports" not in service, name


def test_every_secret_writer_provides_the_guard_secrets(tmp_path: Path) -> None:
    apply_release = (ROOT / "scripts" / "deploy" / "apply_release.sh").read_text(encoding="utf-8")
    assert (
        'fetch_secret_to_file edge/origin-verify "$secrets_dir/edge_origin_verify"' in apply_release
    )
    assert (
        "fetch_secret_to_file edge/origin-verify-previous "
        '"$secrets_dir/edge_origin_verify_previous" optional' in apply_release
    )
    for script in ("setup-local-env.sh", "setup-local-env.ps1"):
        text = (ROOT / script).read_text(encoding="utf-8")
        for name in GUARD_SECRETS:
            assert name in text, (script, name)
    secrets_doc = (ROOT / "docs" / "security" / "secrets.md").read_text(encoding="utf-8")
    for name in GUARD_SECRETS:
        assert f"`{name}`" in secrets_doc, name

    result = subprocess.run(
        [
            sys.executable,
            str(WRITER),
            "--cpu-stage",
            str(STACK),
            "--env-file",
            str(tmp_path / "compose.env"),
            "--secrets-dir",
            str(tmp_path / "secrets"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    secrets = tmp_path / "secrets"
    value = (secrets / "edge_origin_verify").read_text(encoding="utf-8").strip()
    assert re.fullmatch(CONTRACT["origin_guard"]["value_format"], value)
    assert (secrets / "edge_origin_verify_previous").read_text(encoding="utf-8") == ""
    for name in ("edge_tls_cert", "edge_tls_key", "edge_ca_cert"):
        assert (secrets / name).read_text(encoding="utf-8").startswith("-----BEGIN"), name
    certificate = subprocess.run(
        [
            "openssl",
            "x509",
            "-in",
            str(secrets / "edge_tls_cert"),
            "-noout",
            "-ext",
            "subjectAltName",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "IP Address:127.0.0.1" in certificate and "DNS:localhost" in certificate
    verified = subprocess.run(
        [
            "openssl",
            "verify",
            "-CAfile",
            str(secrets / "edge_ca_cert"),
            str(secrets / "edge_tls_cert"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr
    # The CA key is not kept: nothing can sign another certificate later.
    assert not any(
        "ca_key" in path.name or path.name.endswith(".srl") for path in secrets.iterdir()
    )
