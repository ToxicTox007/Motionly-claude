#!/usr/bin/env python3
"""Write safe ephemeral Compose inputs for an integration run.

``--manifest``: inputs for the exact release topology (GPU stage).
``--cpu-stage``: inputs for the CPU-stage stack described by
tests/cpu-stage/stack.json, with random per-run secrets and no live provider
keys, so every external provider falls back to its labelled test provider.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from generate_release_manifest import validate_manifest

JWT_SECRET = "release-integration-jwt-secret-0123456789"
# Compose mounts file secrets with their host mode (it ignores uid/gid/mode),
# and the containers run as non-root users, so secret files are read-only for
# everyone inside a 0700 directory that keeps other host users out.
SECRET_FILE_MODE = 0o444
POSTGRES_PASSWORD = "release-integration-postgres-password"
REDIS_PASSWORD = "release-integration-redis-password"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _jwt(role: str, secret: str = JWT_SECRET) -> str:
    header = _base64url(b'{"alg":"HS256","typ":"JWT"}')
    payload = _base64url(
        json.dumps(
            {"aud": "authenticated", "role": role, "sub": "00000000-0000-4000-8000-000000000001"},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = _base64url(hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _openssl(arguments: list[str]) -> None:
    try:
        subprocess.run(
            ["openssl", *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise ValueError("openssl is required to generate ephemeral Temporal mTLS test material") from error


def _generate_temporal_tls_material(secret_directory: Path) -> None:
    """Generate throwaway PEM files only for static/release-integration Compose inputs."""
    with tempfile.TemporaryDirectory(prefix="motionly-temporal-test-ca-") as temporary:
        root = Path(temporary)
        ca_key = root / "ca.key"
        ca_cert = root / "ca.crt"
        _openssl(
            [
                "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-keyout", str(ca_key), "-out", str(ca_cert),
                "-subj", "/CN=motionly-temporal-test-ca",
            ]
        )
        shutil.copyfile(ca_cert, secret_directory / "temporal_ca_cert")
        identities = {
            "temporal_server": ("temporal", "serverAuth,clientAuth", True),
            "temporal_internode": ("temporal", "serverAuth,clientAuth", True),
            "temporal_api": ("motionly-api", "clientAuth", False),
            "temporal_dispatcher": ("motionly-dispatcher", "clientAuth", False),
            "temporal_worker": ("motionly-temporal-worker", "clientAuth", False),
            "temporal_reconciler": ("motionly-reconciler", "clientAuth", False),
            "temporal_namespace_bootstrap": (
                "motionly-namespace-bootstrap", "clientAuth", False,
            ),
            "temporal_diagnostics": ("motionly-diagnostics", "clientAuth", False),
        }
        for name, (common_name, usages, has_server_name) in identities.items():
            request = root / f"{name}.csr"
            extensions = root / f"{name}.ext"
            key = secret_directory / f"{name}_key"
            certificate = secret_directory / f"{name}_cert"
            extension_lines = ["basicConstraints=CA:FALSE", f"extendedKeyUsage={usages}"]
            if has_server_name:
                extension_lines.append("subjectAltName=DNS:temporal")
            extensions.write_text("\n".join(extension_lines) + "\n", encoding="utf-8")
            _openssl(
                [
                    "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(request), "-subj", f"/CN={common_name}",
                ]
            )
            _openssl(
                [
                    "x509", "-req", "-in", str(request), "-CA", str(ca_cert),
                    "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(certificate),
                    "-days", "1", "-extfile", str(extensions),
                ]
            )
    for path in secret_directory.iterdir():
        path.chmod(SECRET_FILE_MODE)


def _generate_edge_tls_material(secret_directory: Path) -> None:
    """A per-run test CA and the CDN simulator's certificate (127.0.0.1, localhost).

    The CA certificate (edge_ca_cert) lets the stack checker and browsers verify
    the edge; the CA key is discarded, so nothing else can be signed with it.
    """
    with tempfile.TemporaryDirectory(prefix="motionly-edge-test-ca-") as temporary:
        root = Path(temporary)
        ca_key = root / "ca.key"
        ca_cert = secret_directory / "edge_ca_cert"
        _openssl(
            [
                "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                "-nodes", "-days", "2", "-keyout", str(ca_key), "-out", str(ca_cert),
                "-subj", "/CN=Motionly CPU-stage edge test CA (not trusted anywhere else)",
                "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            ]
        )
        extensions = root / "edge.ext"
        extensions.write_text(
            "basicConstraints=CA:FALSE\n"
            "keyUsage=critical,digitalSignature\n"
            "extendedKeyUsage=serverAuth\n"
            "subjectAltName=IP:127.0.0.1,DNS:localhost\n",
            encoding="utf-8",
        )
        request = root / "edge.csr"
        _openssl(
            [
                "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                "-keyout", str(secret_directory / "edge_tls_key"), "-out", str(request),
                "-subj", "/CN=127.0.0.1",
            ]
        )
        _openssl(
            [
                "x509", "-req", "-in", str(request), "-CA", str(ca_cert), "-CAkey", str(ca_key),
                "-set_serial", str(secrets.randbits(63)), "-out",
                str(secret_directory / "edge_tls_cert"), "-days", "2",
                "-extfile", str(extensions),
            ]
        )
    for name in ("edge_ca_cert", "edge_tls_cert", "edge_tls_key"):
        (secret_directory / name).chmod(SECRET_FILE_MODE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", type=Path)
    source.add_argument("--cpu-stage", type=Path, metavar="STACK_JSON")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--secrets-dir", type=Path, required=True)
    return parser.parse_args()


def _write_private(path: Path, text: str, mode: int = 0o600) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def write_cpu_stage_inputs(stack_path: Path, env_file: Path, secrets_dir: Path) -> None:
    """Random per-run secrets; provider key files are empty (test providers only)."""
    stack = json.loads(stack_path.read_text(encoding="utf-8"))
    jwt_secret = secrets.token_hex(32)
    postgres_password = secrets.token_hex(24)
    redis_password = secrets.token_hex(24)
    service_role_key = _jwt("service_role", jwt_secret)
    # S3: workers authenticate as the restricted motionly_worker role (a JWT
    # PostgREST switches to), never with the service-role key. Mounted only into
    # worker services.
    worker_key = _jwt("motionly_worker", jwt_secret)
    secret_values = {
        "postgres_password": postgres_password,
        "redis_password": redis_password,
        "temporal_db_password": secrets.token_hex(24),
        "supabase_service_role_key": service_role_key,
        "supabase_worker_key": worker_key,
        "supabase_jwt_secret": jwt_secret,
        "admin_api_token": secrets.token_hex(24),
        "worker_api_token": secrets.token_hex(24),
        "metrics_bearer_token": secrets.token_hex(24),
        # The CDN simulator adds it; the ingress origin guard requires it.
        "edge_origin_verify": secrets.token_urlsafe(32),
        # Mock payment gateway (Task 22, demo-billing profile): its own merchant
        # API key and webhook signing secret, isolated from the rest of the stack.
        "merchant_api_key": secrets.token_hex(24),
        "mock_gateway_webhook_secret": secrets.token_hex(32),
        # Persistence: the gateway's own PostgREST role JWT (role claim
        # mock_gateway_role). PostgREST switches into that role (granted to the
        # authenticator in migration 202610190803); it can touch ONLY the
        # mock_gateway schema. Mounted only into payment-gateway, like the
        # worker key above.
        "mock_gateway_db_key": _jwt("mock_gateway_role", jwt_secret),
        # Realtime (Task 25): DB_ENC_KEY is a 16-byte AES key; SECRET_KEY_BASE
        # is the Phoenix endpoint secret.
        "realtime_db_enc_key": secrets.token_hex(8),
        "realtime_secret_key_base": secrets.token_hex(32),
        "realtime_metrics_jwt_secret": secrets.token_hex(32),
    }
    secrets_dir.mkdir(mode=0o700, parents=True)
    for name, value in secret_values.items():
        _write_private(secrets_dir / name, value + "\n", SECRET_FILE_MODE)
    for name in (
        # The declarative inference registry is the only source of routes (plan
        # Task 26); the CPU stage uses fake routes with no secret refs, so the
        # generic secrets bundle the base topology mounts is an empty JSON
        # object. No vendor-named provider key is written.
        "ai_secrets_bundle",
        # Bedrock static keys (docs/aws): the CPU stage uses no Bedrock route, so
        # the API and inference workers mount empty key files.
        "aws_inference_access_key_id",
        "aws_inference_secret_access_key",
        "edge_origin_verify_previous",
    ):
        _write_private(secrets_dir / name, "", SECRET_FILE_MODE)
    _generate_temporal_tls_material(secrets_dir)
    _generate_edge_tls_material(secrets_dir)

    environment = {
        **stack["image_variables"],
        "TEMPORAL_ADMIN_TOOLS_IMAGE": stack["temporal_admin_tools_image"],
        "TEMPORAL_BOOTSTRAP_ADMIN_UNTIL": (datetime.now(UTC) + timedelta(hours=3))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "SECRETS_DIR": str(secrets_dir),
        "POSTGRES_PASSWORD": postgres_password,
        "REDIS_PASSWORD": redis_password,
        "SUPABASE_JWT_SECRET": jwt_secret,
        "SUPABASE_ANON_KEY": _jwt("anon", jwt_secret),
        "SUPABASE_SERVICE_ROLE_KEY": service_role_key,
        # One public origin; the CPU-stage ingress serves any host on it.
        "APP_DOMAIN": urlsplit(stack["public_origin"]).hostname,
        "PUBLIC_ORIGIN": stack["public_origin"],
        # The CPU stage runs the mock payment gateway (demo-billing profile).
        "BILLING_MODE": "demo",
    }
    env_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_private(env_file, "".join(f"{name}={value}\n" for name, value in environment.items()))


def main() -> int:
    args = parse_args()
    if args.secrets_dir.exists() or args.env_file.exists():
        raise ValueError("refusing to overwrite existing integration env or secret material")
    if args.cpu_stage is not None:
        write_cpu_stage_inputs(args.cpu_stage, args.env_file, args.secrets_dir)
        return 0
    manifest = validate_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))

    service_role_key = _jwt("service_role")
    anon_key = _jwt("anon")
    # S3: restricted motionly_worker JWT, mounted only into worker services.
    worker_key = _jwt("motionly_worker")
    secret_values = {
        "postgres_password": POSTGRES_PASSWORD,
        "redis_password": REDIS_PASSWORD,
        "temporal_db_password": "release-integration-temporal-db-password",
        "supabase_service_role_key": service_role_key,
        "supabase_worker_key": worker_key,
        "supabase_jwt_secret": JWT_SECRET,
        "admin_api_token": "release-integration-admin-token",
        "worker_api_token": "release-integration-worker-token",
        "azure_openai_api_key": "",
        # Bedrock static keys (docs/aws): empty for the release integration run;
        # no Bedrock route is exercised.
        "aws_inference_access_key_id": "",
        "aws_inference_secret_access_key": "",
        "metrics_bearer_token": "release-integration-metrics-token",
        "edge_origin_verify": "release-integration-origin-verify-0123456789",
        "edge_origin_verify_previous": "",
        "realtime_db_enc_key": "rltimeenckey0123",
        "realtime_secret_key_base": "release-integration-realtime-secret-key-base-0123456789abcdef",
        "realtime_metrics_jwt_secret": "release-integration-realtime-metrics-jwt-secret-0123456789",
    }
    args.secrets_dir.mkdir(mode=0o700, parents=True)
    for name, value in secret_values.items():
        secret_path = args.secrets_dir / name
        secret_path.write_text(value + "\n", encoding="utf-8")
        secret_path.chmod(SECRET_FILE_MODE)
    _generate_temporal_tls_material(args.secrets_dir)

    environment = {
        **manifest["images"],
        "TEMPORAL_ADMIN_TOOLS_IMAGE": "temporalio/admin-tools@sha256:" + "c" * 64,
        "TEMPORAL_BOOTSTRAP_ADMIN_UNTIL": (
            datetime.now(UTC) + timedelta(minutes=30)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "SECRETS_DIR": str(args.secrets_dir),
        "POSTGRES_PASSWORD": POSTGRES_PASSWORD,
        "REDIS_PASSWORD": REDIS_PASSWORD,
        "SUPABASE_JWT_SECRET": JWT_SECRET,
        "SUPABASE_ANON_KEY": anon_key,
        "SUPABASE_SERVICE_ROLE_KEY": service_role_key,
        "APP_DOMAIN": "app.release.test",
        "PUBLIC_ORIGIN": "https://app.release.test",
        "BILLING_MODE": "disabled",
    }
    args.env_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.env_file.write_text("".join(f"{name}={value}\n" for name, value in environment.items()), encoding="utf-8")
    args.env_file.chmod(0o600)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"release integration input error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
