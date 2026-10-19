"""Contracts for the single-instance Compose deployment control plane (AWS).

The AWS-specific release path is pinned in test_aws_release_contract.py.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-aws.yml"
AGENT = ROOT / "scripts" / "deploy" / "apply_release.sh"
BOOTSTRAP = ROOT / "scripts" / "deploy" / "bootstrap_compose_host.sh"
PG_REHEARSAL = ROOT / "scripts" / "deploy" / "rehearse_postgres17_upgrade.sh"
HOST_ENV = ROOT / "infra" / "compose-host" / "host.env.example"
DEPLOY_RUNBOOK = ROOT / "docs" / "ops" / "compose-vm-deployment.md"
PG_RUNBOOK = ROOT / "docs" / "ops" / "postgresql-17-upgrade.md"
TEMPORAL_CANARY = ROOT / "scripts" / "deploy" / "temporal_canary_gate.sh"
TEMPORAL_CANARY_RUNBOOK = ROOT / "docs" / "ops" / "temporal-mtls-canary.md"
SUPPLY_CHAIN = ROOT / ".github" / "workflows" / "supply-chain.yml"


def test_deploy_workflow_is_oidc_single_instance_and_production_is_protected() -> None:
    workflow = DEPLOY_WORKFLOW.read_text()

    assert "id-token: write" in workflow
    assert "aws-actions/configure-aws-credentials@v4" in workflow
    assert "environment: production" in workflow
    assert "scripts/deploy/run_on_instance.sh" in workflow
    assert "--preflight" in workflow
    assert "vmss" not in workflow.lower() and "autoscaling" not in workflow.lower()
    assert "secrets." not in workflow


def test_host_agent_reads_secrets_locally_and_tracks_digest_rollback() -> None:
    agent = AGENT.read_text()
    bootstrap = BOOTSTRAP.read_text()
    host_env = HOST_ENV.read_text()

    assert "cosign verify-blob" in agent
    assert "--password-stdin" in agent
    assert "chmod 600" in agent
    assert "current_link" in agent and "previous_link" in agent
    assert "rollback_previous_release" in agent
    assert "docker compose --project-name motionly" in agent
    assert "vmss" not in agent.lower()
    assert "MOTIONLY_APP_DOMAIN" in host_env
    # One public origin: browsers reach /api and /supabase on the app domain.
    for retired in ("MOTIONLY_API_DOMAIN", "MOTIONLY_SUPABASE_DOMAIN"):
        assert retired not in host_env
        assert retired not in agent
    assert '"PUBLIC_ORIGIN": f"https://{app_domain}"' in agent
    assert "MOTIONLY_TEMPORAL_SERVER_IMAGE" not in host_env
    assert "MOTIONLY_TEMPORAL_ADMIN_TOOLS_IMAGE" in host_env
    assert "MOTIONLY_TEMPORAL_CANARY_OVERLAP_PROOF_FILE" in host_env
    assert "temporal-db-password" in agent
    assert "fetch_base64_secret_to_file" in agent
    assert "temporal-ca-cert" in agent
    assert "MOTIONLY_TEMPORAL_SERVER_IMAGE" in SUPPLY_CHAIN.read_text()
    assert "temporal-server" in SUPPLY_CHAIN.read_text()
    assert "VMSS is explicitly unsupported" in DEPLOY_RUNBOOK.read_text()
    assert "instance role" in bootstrap


def test_postgres17_rehearsal_requires_logical_restore_and_explicit_rollback_boundary() -> None:
    rehearsal = PG_REHEARSAL.read_text()
    runbook = PG_RUNBOOK.read_text()

    assert "MOTIONLY_POSTGRES17_UPGRADE_ACK=pg15-to-pg17" in rehearsal
    assert "^15[0-9]{4}$" in rehearsal
    assert "^17[0-9]{4}$" in rehearsal
    assert "verify_backup_restore.sh" in rehearsal
    assert "logical `pg_dump`/`pg_restore`" in runbook
    assert "Rollback is safe only before accepting PostgreSQL 17 writes" in runbook


def test_temporal_backup_verifier_uses_protected_libpq_service_interfaces() -> None:
    verifier = (ROOT / "scripts" / "verify_temporal_backup_restore.sh").read_text()
    backup = (ROOT / "scripts" / "verify_backup_restore.sh").read_text()
    rehearsal = PG_REHEARSAL.read_text()

    assert "<application-source-service> <application-restore-service>" in verifier
    assert "<temporal-source-service> <temporal-restore-service>" in verifier
    assert "PGSERVICEFILE" in backup and "PGPASSFILE" in backup
    assert "printf 'service=%s'" in backup
    assert "<source-database-url>" not in backup
    assert "postgres://" not in backup
    assert "<postgres-15-source-service>" in rehearsal
    assert "<postgres-15-source-url>" not in rehearsal


def test_release_agent_never_passes_secret_values_as_argv() -> None:
    agent = AGENT.read_text()

    assert "fetch_secret_to_file" in agent
    assert "read_required_secret" not in agent
    assert 'python3 - "$manifest" "$env_file" "$secrets_dir" "$postgres_password"' not in agent
    assert "aws ssm get-parameter --with-decryption" in agent
    assert "--password-stdin" in agent


def test_temporal_same_vm_canary_requires_evidence_and_never_activates_automatically() -> None:
    gate = TEMPORAL_CANARY.read_text()
    runbook = TEMPORAL_CANARY_RUNBOOK.read_text()
    agent = AGENT.read_text()

    assert "dual frontend overlap proof is not configured" in gate
    assert "signed_release_verified" in gate
    assert "mtls_handshake_verified" in gate
    assert "authorization_role_matrix_verified" in gate
    assert "controlled_namespace_validation_verified" in gate
    assert "rollback_verified" in gate
    assert "docker compose" not in gate.lower()
    assert "validate_temporal_canary_gate" in agent
    assert "temporal-canary-gate" in BOOTSTRAP.read_text()
    assert "No live canary has been run" in runbook
    assert "same-VM dual frontend is not assumed safe" in runbook
