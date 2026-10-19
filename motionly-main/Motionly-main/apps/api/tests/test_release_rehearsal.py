"""Helpers of the local release rehearsal (scripts/release/rehearse_release.py).

The rehearsal itself runs in `pnpm verify --stage aws`; these tests pin how it
turns generated test secrets into SSM parameters and how it points the
CPU-stage overlay at files the signed bundle does not carry.
"""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def rehearsal():
    spec = importlib.util.spec_from_file_location(
        "motionly_rehearse_release", ROOT / "scripts" / "release" / "rehearse_release.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_secret_files_map_to_the_parameter_names_the_agent_reads(rehearsal, tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "postgres_password").write_text("pg\n")
    (secrets / "edge_origin_verify").write_text("guard\n")
    (secrets / "edge_origin_verify_previous").write_text("")
    (secrets / "groq_api_key").write_text("")
    (secrets / "temporal_ca_cert").write_text(
        "-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----\n"
    )
    (secrets / "edge_tls_key").write_text(
        "-----BEGIN PRIVATE KEY-----\nBB==\n-----END PRIVATE KEY-----\n"
    )
    (secrets / "edge_ca_cert").write_text("not shipped to the instance\n")

    parameters = rehearsal.parameters_from_inputs(secrets, {"SUPABASE_ANON_KEY": "anon"})

    assert parameters["postgres-password"] == "pg"
    assert parameters["supabase-anon-key"] == "anon"
    assert parameters["edge/origin-verify"] == "guard"
    # SSM cannot store empty values; the agent treats them as optional.
    assert "edge/origin-verify-previous" not in parameters
    assert "groq-api-key" not in parameters
    # Multi-line material is base64, as in production.
    pem = base64.b64decode(parameters["temporal-ca-cert"]).decode()
    assert pem.startswith("-----BEGIN CERTIFICATE-----")
    assert (
        base64.b64decode(parameters["edge-tls-key"]).decode().startswith("-----BEGIN PRIVATE KEY")
    )
    # The viewer's CA certificate stays with the rehearsal (it checks the edge).
    assert "edge-ca-cert" not in parameters
    assert rehearsal.EXTRA_SECRETS == ("edge-tls-cert", "edge-tls-key")


def test_the_overlay_uses_bundle_files_and_the_checkout_only_for_test_infrastructure(
    rehearsal,
) -> None:
    overlay = "\n".join(
        [
            "    volumes: !override",
            "      - ./Caddyfile.cpu-stage:/etc/caddy/Caddyfile:ro",
            "      - ./Caddyfile.routes:/etc/caddy/routes.caddy:ro",
            "      - caddy-data:/data",
            "      - ../scripts/cdn-sim:/opt/cdn-sim:ro",
            "      - ./aws/edge-contract.json:/opt/edge-contract.json:ro",
        ]
    )
    bundle = {"infra/Caddyfile.routes", "infra/docker-compose.gpu.yml"}

    rewritten = rehearsal.rehearsal_overlay(overlay, bundle, ROOT)

    assert "      - ./Caddyfile.routes:/etc/caddy/routes.caddy:ro" in rewritten
    assert f"      - {ROOT}/infra/Caddyfile.cpu-stage:/etc/caddy/Caddyfile:ro" in rewritten
    assert f"      - {ROOT}/scripts/cdn-sim:/opt/cdn-sim:ro" in rewritten
    assert f"      - {ROOT}/infra/aws/edge-contract.json:/opt/edge-contract.json:ro" in rewritten
    assert "      - caddy-data:/data" in rewritten


def test_release_ids_are_fresh_lowercase_git_style_shas(rehearsal) -> None:
    first, second = rehearsal.new_release_id(), rehearsal.new_release_id()
    assert first != second
    assert len(first) == 40 and int(first, 16) >= 0 and first == first.lower()


def test_the_emulators_leave_no_volumes_behind(rehearsal, tmp_path: Path, monkeypatch) -> None:
    """Found by running the rehearsal: the registry image declares a volume, so
    every run left ~2 GiB of pushed images in an anonymous volume on the
    Docker host's disk."""
    calls: list[list[str]] = []

    class Done:
        stdout = ""
        returncode = 0

    def record(command, **_kwargs):
        calls.append(list(command))
        return Done()

    monkeypatch.setattr(rehearsal, "run", record)
    monkeypatch.setattr(rehearsal.urllib.request, "urlopen", lambda *_a, **_k: None)
    session = rehearsal.Rehearsal(tmp_path)
    session.start_emulators()
    session.teardown()

    starts = [c for c in calls if c[:3] == ["docker", "run", "-d"]]
    removals = [c for c in calls if c[:2] == ["docker", "rm"]]
    assert len(starts) == len(rehearsal.CONTAINERS)
    assert removals, "the emulators are removed"
    for removal in removals:
        assert "--volumes" in removal or "-v" in removal, removal


def test_a_failed_rehearsal_keeps_the_service_logs(rehearsal, tmp_path: Path, monkeypatch) -> None:
    """Teardown removes the containers, so the evidence of a failed apply must
    be saved first (CI uploads test-results/)."""
    calls: list[list[str]] = []

    class Done:
        returncode = 0

        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout
            self.stderr = ""

    def record(command, **_kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "ps", "-a"]:
            return Done("motionly-supabase-auth-1\nmotionly-postgres-1\n")
        if command[:2] == ["docker", "inspect"]:
            return Done('{"Status": "restarting", "ExitCode": 139}')
        if command[:2] == ["docker", "logs"]:
            return Done("service log line\n")
        return Done()

    monkeypatch.setattr(rehearsal, "run", record)
    monkeypatch.setattr(rehearsal, "RESULTS", tmp_path / "results")
    session = rehearsal.Rehearsal(tmp_path / "work")
    session.save_service_logs()

    saved = tmp_path / "results" / "rehearsal-services"
    assert (saved / "motionly-supabase-auth-1.log").read_text().endswith("service log line\n")
    assert "ExitCode" in (saved / "motionly-supabase-auth-1.log").read_text()
    assert (saved / "motionly-postgres-1.log").exists()
    ps = next(c for c in calls if c[:3] == ["docker", "ps", "-a"])
    assert f"label=com.docker.compose.project={rehearsal.PROJECT}" in ps


def test_teardown_removes_the_images_pulled_from_the_rehearsal_registry(
    rehearsal, tmp_path: Path, monkeypatch
) -> None:
    """Found by running the rehearsal repeatedly: the agent pulls every image
    by digest from the local registry, and those copies (GiBs per run) stayed
    in the Docker host's image store."""
    calls: list[list[str]] = []

    class Done:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout

    def record(command, **_kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "ls"] and any(
            rehearsal.REGISTRY in arg for arg in command
        ):
            return Done(
                f"{rehearsal.REGISTRY}/motionly/api@sha256:aaa\n"
                f"{rehearsal.REGISTRY}/motionly/web@sha256:bbb\n"
                "<none>@<none>\n"
            )
        return Done()

    monkeypatch.setattr(rehearsal, "run", record)
    rehearsal.Rehearsal(tmp_path).teardown()

    listing = next(c for c in calls if c[:3] == ["docker", "image", "ls"])
    assert f"reference={rehearsal.REGISTRY}/motionly/*" in listing
    removal = next(c for c in calls if c[:3] == ["docker", "image", "rm"])
    # By reference, never by ID or with -f: the local CPU-stage tags stay.
    assert removal[3:] == [
        f"{rehearsal.REGISTRY}/motionly/api@sha256:aaa",
        f"{rehearsal.REGISTRY}/motionly/web@sha256:bbb",
    ]
    # The images are removed after the containers that use them.
    assert calls.index(removal) > max(
        i
        for i, c in enumerate(calls)
        if c[:2] == ["docker", "container"] or c[:2] == ["docker", "rm"]
    )
