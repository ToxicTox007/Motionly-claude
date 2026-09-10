import asyncio
from pathlib import Path
from uuid import uuid4

from app.core.middleware import InMemoryRateLimiter
from app.services.operations import OperationsRepository

ROOT = Path(__file__).resolve().parents[3]


def test_provider_slo_rate_limit_and_cost_alert_inputs_are_measured() -> None:
    operations = OperationsRepository()
    workspace_id, project_id, render_id, export_id = uuid4(), uuid4(), uuid4(), uuid4()

    operations.record_provider_slo(
        workspace_id=workspace_id,
        project_id=project_id,
        provider="openrouter",
        operation="edit_plan",
        success=False,
        latency_ms=2500,
        error_code="rate_limit_exceeded",
    )
    operations.record_cost(
        workspace_id=workspace_id,
        project_id=project_id,
        render_job_id=render_id,
        provider="openrouter",
        category="tokens",
        quantity=1000,
        unit="token",
        unit_cost_usd=0.000002,
        metadata={"rate_limit_remaining": 0},
    )
    operations.approve_export(
        workspace_id=workspace_id,
        render_job_id=render_id,
        export_id=export_id,
    )

    assert operations.cost_summary()["cost_per_approved_export_usd"] == 0.002
    assert operations.cost_summary()["by_provider"]["openrouter"] == 0.002
    assert operations.provider_slo_summary()[0]["success_rate"] == 0
    assert operations.provider_slo_summary()[0]["p95_latency_ms"] == 2500
    assert any(
        event["provider"] == "openrouter"
        and event["error_code"] == "rate_limit_exceeded"
        and event["latency_ms"] == 2500
        for event in operations.provider_events
    )


def test_verified_identity_rate_limit_blocks_after_threshold() -> None:
    limiter = InMemoryRateLimiter()
    key = f"user:{uuid4()}:workspace:{uuid4()}:/projects/demo/plans"

    first_allowed, first_remaining = asyncio.run(limiter.allow(key=key, limit=1))
    second_allowed, second_remaining = asyncio.run(limiter.allow(key=key, limit=1))

    assert (first_allowed, first_remaining) == (True, 0)
    assert (second_allowed, second_remaining) == (False, 0)


def test_phase10_prelaunch_evidence_files_are_present() -> None:
    required = {
        "docs/data-retention.md": ["Source video", "Encoded exports", "Local processing cache"],
        "docs/operations.md": ["Provider latency/error SLO", "/health/ready", "worker heartbeat"],
        "docs/backups-restore.md": ["pg_dump", "pg_restore", "repository integration contracts"],
        "docs/prelaunch-review.md": [
            "Accessibility",
            "Browser and device compatibility",
            "Privacy and legal",
        ],
        "docs/security/secrets.md": ["never commit", "/run/secrets"],
    }
    for relative, terms in required.items():
        content = (ROOT / relative).read_text()
        for term in terms:
            assert term in content


def test_backup_restore_script_uses_dump_restore_and_schema_probe() -> None:
    script = (ROOT / "scripts/verify_backup_restore.sh").read_text()
    assert "pg_dump --format=custom" in script
    assert "--exclude-table-data=vault.secrets" in script
    assert "pg_restore --exit-on-error --no-owner" in script
    assert "information_schema.tables" in script
    assert "Restore target must be empty" in script
    assert "MOTIONLY_BACKUP_RESTORE_ACK" in script
    assert "Restore target resolves to the source database" in script
    assert "fingerprint_tables" in script
    assert "cmp -s" in script
    assert "pg_is_in_recovery" in script


def test_backup_restore_drill_seeds_representative_workflow_data() -> None:
    seed = (ROOT / "scripts/backup_restore_seed.sql").read_text()
    workflow = (ROOT / ".github/workflows/phase-0-safety.yml").read_text()
    assert "motionly_ensure_first_draft_v2" in seed
    assert "workflow_version" in seed
    assert "backup_restore_seed.sql" in workflow
    assert "MOTIONLY_BACKUP_RESTORE_ACK=restore-target" in workflow


def test_ci_runs_phase10_prelaunch_gates() -> None:
    workflow = (ROOT / ".github/workflows/phase-0-safety.yml").read_text()
    for term in (
        "phase-10-prelaunch",
        "verify_prelaunch_evidence.mjs",
        "verify_backup_restore.sh",
        "test_phase10_prelaunch.py",
    ):
        assert term in workflow


def test_renderer_feature_matrix_is_backed_by_golden_and_encoded_gates() -> None:
    matrix = (ROOT / "docs/renderer-feature-matrix.md").read_text(encoding="utf-8")
    golden = (ROOT / "apps/renderer/src/golden-frames.test.ts").read_text()
    encoded = (ROOT / "apps/renderer/src/encoded-output.test.ts").read_text()
    workflow = (ROOT / ".github/workflows/phase-0-safety.yml").read_text()

    for supported_feature in (
        "Source clip trim and concatenation",
        "Static burned captions",
        "Manual transitions",
        "Named color presets",
        "Fit/fill/custom framing and watermark",
        "Free is capped at 720p",
        "Agency can deliver the 4K option",
    ):
        assert supported_feature in matrix
    for golden_scenario in ("effects", "captions", "transition", "framing"):
        assert golden_scenario in golden
    assert "duration_ms: sourceEndMs - sourceStartMs" in encoded
    assert "pnpm test:encoded" in workflow
    assert "pnpm test:golden" in workflow
