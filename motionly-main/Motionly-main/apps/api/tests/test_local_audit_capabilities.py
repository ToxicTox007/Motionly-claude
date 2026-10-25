"""Fail-closed contracts for the isolated local-audit preflight and runtime gate."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.local_audit import (
    LocalAuditEffectsDenied,
    LocalAuditLiveEffectsGate,
    compile_local_audit_policy,
)
from app.services.model_gateway import (
    AdapterResult,
    ChatMessage,
    InMemoryBudgetLedger,
    ModelCapability,
    ModelGateway,
    ModelRoute,
    PlanningOutput,
    PlanningRequest,
    budget_scope,
)

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "apps" / "api" / "scripts" / "audit_local_capabilities.py"

spec = importlib.util.spec_from_file_location("audit_local_capabilities", SCRIPT_PATH)
assert spec and spec.loader
local_audit_capabilities = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = local_audit_capabilities
spec.loader.exec_module(local_audit_capabilities)


def _protected_secrets(tmp_path: Path) -> Path:
    directory = tmp_path / "protected-local-audit-secrets"
    directory.mkdir()
    directory.chmod(0o700)
    for name in local_audit_capabilities.SECRET_FILE_NAMES:
        path = directory / name
        path.write_text(f"audit-secret-value-for-{name}-123456789", encoding="utf-8")
        path.chmod(0o600)
    return directory


def _environment(secret_directory: Path, *, effects_enabled: bool = False) -> dict[str, str]:
    values = {
        "COMPOSE_PROJECT_NAME": "motionly-local-audit",
        "SECRETS_DIR": str(secret_directory),
        "POSTGRES_PASSWORD": "local-audit-postgres-password-123",
        "REDIS_PASSWORD": "local-audit-redis-password-123",
        "SUPABASE_JWT_SECRET": "local-audit-jwt-secret-123",
        "SUPABASE_ANON_KEY": "local-audit-anon-key-123",
        "SUPABASE_SERVICE_ROLE_KEY": "local-audit-service-key-123",
        "APP_DOMAIN": "app.audit.localhost",
        "PUBLIC_ORIGIN": "https://app.audit.localhost:18443",
        "BILLING_MODE": "demo",
        "TEMPORAL_BOOTSTRAP_ADMIN_UNTIL": "2027-01-01T00:00:00Z",
        "MOTIONLY_API_IMAGE": "motionly-api:dev",
        "MOTIONLY_WEB_IMAGE": "motionly-web:dev",
        "MOTIONLY_CPU_FFMPEG_WORKER_IMAGE": "motionly-cpu-ffmpeg-worker:dev",
        "MOTIONLY_GPU_WORKER_IMAGE": "motionly-gpu-worker:dev",
        "MOTIONLY_GPU_FFMPEG_WORKER_IMAGE": "motionly-gpu-ffmpeg-worker:dev",
        "MOTIONLY_DEMUCS_WORKER_IMAGE": "motionly-demucs-worker:dev",
        "MOTIONLY_PROVIDER_WORKER_IMAGE": "motionly-provider-worker:dev",
        "MOTIONLY_PLANNING_WORKER_IMAGE": "motionly-planning-worker:dev",
        "MOTIONLY_REMOTION_WORKER_IMAGE": "motionly-remotion-worker:dev",
        "MOTIONLY_TEMPORAL_SERVER_IMAGE": "motionly-temporal-server:local-audit",
        "TEMPORAL_ADMIN_TOOLS_IMAGE": "temporalio/admin-tools@sha256:" + "a" * 64,
        "LOCAL_AUDIT_MODE": "true",
        "LOCAL_AUDIT_EXTERNAL_EFFECTS_ENABLED": str(effects_enabled).lower(),
        "LOCAL_AUDIT_TARGET": "local-sandbox",
        "LOCAL_AUDIT_RUN_ID": "local-audit-run-20260830",
        "LOCAL_AUDIT_PROVIDER_ACCOUNTS": json.dumps(
            [{"provider": "provider-a", "account_id": "provider-a-audit-001"}]
        ),
        "LOCAL_AUDIT_APPROVED_DOMAINS": "api.provider-a.example",
        "LOCAL_AUDIT_MAX_PER_RUN_SPEND_USD": "1.25",
        "LOCAL_AUDIT_MAX_TOTAL_SPEND_USD": "5.00",
        "LOCAL_AUDIT_REMOTE_CLEANUP_OWNER": "audit-owner-dev",
        "LOCAL_AUDIT_REMOTE_CLEANUP_REFERENCE": "LOCAL-AUDIT-20260830",
        "LOCAL_AUDIT_REMOTE_CLEANUP_POLICY": "delete-within-24-hours",
        "LOCAL_AUDIT_REQUIRE_GPU": "true",
        "LOCAL_AUDIT_MIN_FREE_DISK_GB": "1",
        "LOCAL_AUDIT_MIN_MEMORY_GB": "1",
    }
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    path.chmod(0o600)


def _ready_tools() -> dict[str, dict[str, object]]:
    return {
        "docker": {"status": "ready", "observed_version": "27.0.0"},
        "compose": {"status": "ready", "observed_version": "2.30.0"},
        "node": {"status": "ready", "observed_version": "22.14.0"},
        "pnpm": {"status": "ready", "observed_version": "9.15.4"},
        "python": {"status": "ready", "observed_version": "3.12.8"},
        "supabase": {"status": "ready", "observed_version": "2.104.0"},
        "ffmpeg": {"status": "ready", "observed_version": "7.1.0"},
        "chromium": {"status": "ready", "observed_version": "128.0.0"},
    }


def _successful_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    values: dict[str, str],
    *,
    gpu: dict[str, object] | None = None,
) -> dict[str, object]:
    # Windows test ACLs are environment-specific; the production preflight uses
    # icacls, while these contracts exercise the policy independent of host ACLs.
    monkeypatch.setattr(
        local_audit_capabilities,
        "_protected_path_errors",
        lambda *_args, **_kwargs: [],
    )
    env_file = tmp_path / "local-audit-compose.env"
    _write_env_file(env_file, values)
    return local_audit_capabilities.build_capability_report(
        env_file=env_file,
        probe_gpu=True,
        tool_probe=_ready_tools,
        image_probe=lambda _tools: {
            "status": "ready",
            "required_image_count": 11,
            "available_image_count": 11,
            "operation": "local-metadata-only",
        },
        gpu_probe=lambda _tools: gpu
        or {
            "status": "ready",
            "validated": True,
            "gpu_count": 1,
            "docker_runtime_metadata": "available",
        },
        capacity_probe=lambda: (32 * 1024**3, 32 * 1024**3),
        compose_probe=lambda _path: "ready",
    )


def test_successful_preflight_is_redacted_and_keeps_live_effects_denied_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secrets = _protected_secrets(tmp_path)
    report = _successful_report(monkeypatch, tmp_path, _environment(secrets))

    assert report["ready"] is True
    assert report["namespace"] == {
        "compose_project_name": "motionly-local-audit",
        "profile": "local-audit",
        "audit_owned_volume_prefix": "motionly-local-audit-",
        "automatic_volume_deletion": "disabled",
    }
    assert report["live_effects"]["status"] == "blocked-by-default"
    assert report["gpu"] == {
        "status": "ready",
        "validated": True,
        "gpu_count": 1,
        "docker_runtime_metadata": "available",
    }

    rendered = json.dumps(report)
    assert "audit-secret-value" not in rendered
    assert "provider-a-audit-001" not in rendered
    assert str(secrets) not in rendered
    assert "signed" not in rendered


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda values, _secrets: values.update({"APP_DOMAIN": "app.motionly.com"}),
            "production_domain_not_allowed",
        ),
        (
            lambda values, _secrets: values.update({"PUBLIC_ORIGIN": "https://app.motionly.com"}),
            "production_domain_not_allowed",
        ),
        (
            lambda values, secrets: (secrets / "postgres_password").write_text(
                "replace-me", encoding="utf-8"
            ),
            "secret_file_missing_or_placeholder",
        ),
        (
            lambda values, _secrets: values.update(
                {
                    "LOCAL_AUDIT_PROVIDER_ACCOUNTS": json.dumps(
                        [{"provider": "provider-a", "account_id": "production-account"}]
                    )
                }
            ),
            "provider_accounts_must_use_non_production_accounts",
        ),
        (
            lambda values, _secrets: values.pop("LOCAL_AUDIT_MAX_TOTAL_SPEND_USD"),
            "max_total_spend_required",
        ),
    ],
)
def test_preflight_rejects_unsafe_or_incomplete_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutate,
    expected_error: str,
) -> None:
    secrets = _protected_secrets(tmp_path)
    values = _environment(secrets)
    mutate(values, secrets)
    report = _successful_report(monkeypatch, tmp_path, values)

    assert report["ready"] is False
    assert expected_error in report["errors"]


def test_preflight_requires_actual_gpu_container_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secrets = _protected_secrets(tmp_path)
    report = _successful_report(
        monkeypatch,
        tmp_path,
        _environment(secrets),
        gpu={"status": "unavailable", "validated": False, "gpu_count": 0},
    )

    assert report["ready"] is False
    assert report["gpu"]["status"] == "unavailable"
    assert "gpu_support_not_validated" in report["errors"]


class _Adapter:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, *, route, request):
        del route, request
        self.calls += 1
        return AdapterResult(output=PlanningOutput(content="{}", raw_response={}))


def _gate(values: dict[str, str], secrets: Path) -> LocalAuditLiveEffectsGate:
    authorization = (secrets / "local_audit_external_authorization").read_text(encoding="utf-8")
    compilation = compile_local_audit_policy(values, authorization=authorization)
    return LocalAuditLiveEffectsGate(compilation)


def test_model_gate_denies_before_budget_reservation_or_adapter_invocation(tmp_path: Path) -> None:
    secrets = _protected_secrets(tmp_path)
    gate = _gate(_environment(secrets, effects_enabled=False), secrets)
    adapter = _Adapter()
    ledger = InMemoryBudgetLedger()
    route = ModelRoute(
        route_id="planning-audit",
        capability=ModelCapability.PLANNING,
        adapter="remote",
        provider="provider-a",
        model="audit-model",
        model_revision="audit-model-2026-08-30",
        fixed_cost_usd=0.25,
        endpoint_host="api.provider-a.example",
        requires_live_effects_gate=True,
    )
    gateway = ModelGateway(
        adapters={"remote": adapter},
        routes=[route],
        budget_ledger=ledger,
        live_effects_gate=gate,
    )

    with pytest.raises(LocalAuditEffectsDenied, match="blocked"):
        gateway.invoke(
            PlanningRequest(messages=(ChatMessage(role="user", content="audit"),))
        )

    assert adapter.calls == 0
    assert ledger.usage(budget_scope(None)) == 0
    decision = gate.safe_report()["recent_decisions"][-1]
    assert decision == {
        "action": "model_invoke",
        "allowed": False,
        "reason": "local_audit_external_effects_disabled",
        "provider": "provider-a",
        "endpoint_hosts": ["api.provider-a.example"],
        "estimated_cost_usd": 0.25,
    }


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker CLI is unavailable")
def test_local_audit_compose_overlay_renders_without_starting_services(tmp_path: Path) -> None:
    secrets = _protected_secrets(tmp_path)
    values = _environment(secrets)
    env_file = tmp_path / "local-audit-compose.env"
    _write_env_file(env_file, values)

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "motionly-local-audit",
            "--env-file",
            str(env_file),
            "-f",
            "infra/docker-compose.gpu.yml",
            "-f",
            "infra/docker-compose.dev.yml",
            "-f",
            "infra/docker-compose.local-audit.yml",
            "--profile",
            "local-audit",
            "config",
            "-q",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_settings_loads_local_audit_authorization_from_file_not_inherited_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    authorization_file = tmp_path / "local_audit_external_authorization"
    authorization_file.write_text("audit-file-authorization-123456789", encoding="utf-8")
    monkeypatch.setenv("SETTINGS_ENV_FILE", "none")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("LOCAL_AUDIT_MODE", "true")
    monkeypatch.setenv(
        "LOCAL_AUDIT_EXTERNAL_AUTHORIZATION_FILE",
        str(authorization_file),
    )
    monkeypatch.setenv("LOCAL_AUDIT_EXTERNAL_AUTHORIZATION", "untrusted-inherited-value")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.local_audit_external_authorization == "audit-file-authorization-123456789"
        assert settings.local_audit_external_authorization_file == str(authorization_file)
    finally:
        get_settings.cache_clear()


def test_plan_only_cli_writes_only_redacted_json_to_stdout() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--plan-only"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["mode"] == "plan-only"
    assert report["side_effects"] == {
        "containers_started": False,
        "images_built": False,
        "images_pulled": False,
        "provider_calls": False,
        "files_written": False,
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes only")
def test_secret_files_may_be_read_only_inside_a_private_directory(tmp_path: Path) -> None:
    """Containers run as non-root, so secret files are 0444 inside a 0700 directory."""
    check = local_audit_capabilities._protected_path_errors
    directory = tmp_path / "secrets"
    directory.mkdir()
    directory.chmod(0o700)
    secret = directory / "postgres_password"
    secret.write_text("value", encoding="utf-8")

    secret.chmod(0o444)
    assert check(directory, is_directory=True) == []
    assert check(secret, is_directory=False, secret_file=True) == []
    # The env file is not a mounted secret: it stays owner-only.
    assert check(secret, is_directory=False) == ["protected_input_permissions_not_private"]
    secret.chmod(0o666)
    assert check(secret, is_directory=False, secret_file=True) == [
        "protected_input_permissions_not_private"
    ]
    directory.chmod(0o755)
    assert check(directory, is_directory=True) == ["protected_input_permissions_not_private"]
