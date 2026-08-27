#!/usr/bin/env python3
"""Write safe ephemeral Compose inputs from a validated immutable release manifest."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from generate_release_manifest import validate_manifest

JWT_SECRET = "release-integration-jwt-secret-0123456789"
POSTGRES_PASSWORD = "release-integration-postgres-password"
REDIS_PASSWORD = "release-integration-redis-password"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _jwt(role: str) -> str:
    header = _base64url(b'{"alg":"HS256","typ":"JWT"}')
    payload = _base64url(
        json.dumps(
            {"aud": "authenticated", "role": role, "sub": "00000000-0000-4000-8000-000000000001"},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = _base64url(hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest())
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
        path.chmod(0o600)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--secrets-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = validate_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
    if args.secrets_dir.exists() or args.env_file.exists():
        raise ValueError("refusing to overwrite existing integration env or secret material")

    service_role_key = _jwt("service_role")
    anon_key = _jwt("anon")
    secret_values = {
        "postgres_password": POSTGRES_PASSWORD,
        "redis_password": REDIS_PASSWORD,
        "temporal_db_password": "release-integration-temporal-db-password",
        "supabase_service_role_key": service_role_key,
        "supabase_jwt_secret": JWT_SECRET,
        "admin_api_token": "release-integration-admin-token",
        "worker_api_token": "release-integration-worker-token",
        "publish_token_encryption_key": "a" * 64,
        "publish_oauth_state_secret": "release-integration-oauth-state-secret",
        "groq_api_key": "release-integration-groq-key",
        "openrouter_api_key": "release-integration-openrouter-key",
        "runway_api_key": "release-integration-runway-key",
        "azure_openai_api_key": "",
        "metrics_bearer_token": "release-integration-metrics-token",
    }
    args.secrets_dir.mkdir(mode=0o700, parents=True)
    for name, value in secret_values.items():
        secret_path = args.secrets_dir / name
        secret_path.write_text(value + "\n", encoding="utf-8")
        secret_path.chmod(0o600)
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
        "API_DOMAIN": "api.release.test",
        "SUPABASE_DOMAIN": "supabase.release.test",
        "SENTRY_DSN": "https://public@errors.release.test/1",
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
