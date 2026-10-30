"""Application-side contracts for the database audit fixes (DB-xx findings).

The SQL half of each fix is covered by supabase/tests/db_audit_*_test.sql.
These tests pin what the API, the configuration and the host tooling must do
so the database guarantees are not undone in code: quota reads fail closed,
status and plan writes are compare-and-set, workers run without the JWT
signing secret, and backups, alarms and migrations behave as documented in
docs/ops/database-operations.md.
"""

import asyncio
import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.api.billing import apply_gateway_event_async
from app.api.errors import ApiError
from app.api.plans import _update_plan_unversioned
from app.api.revision_errors import RevisionConflictError
from app.core.config import get_settings
from app.mock_gateway.models import WebhookEnvelope
from app.repositories.billing import InMemoryBillingRepository, SupabaseBillingRepository
from app.repositories.captions import SupabaseCaptionRepository, TranscriptRecord
from app.repositories.uploads import InMemoryUploadRepository, SupabaseUploadRepository
from app.schemas.billing import BillingPlanKey, SubscriptionStatus
from app.schemas.captions import LanguageHint, TranscriptStatus, TranscriptWord
from app.schemas.uploads import UploadStatus
from app.services.billing import check_quota
from app.services.operations import OperationsRepository, UsageUnavailableError

ROOT = Path(__file__).resolve().parents[3]
NOW = "2026-10-28T00:00:00+00:00"


def _response(rows, status_code: int = 200):
    return SimpleNamespace(status_code=status_code, headers={}, json=lambda: rows)


# ---------------------------------------------------------------------------
# DB-07: feature usage is summed by the database and a quota fails closed.
# ---------------------------------------------------------------------------


def _database_operations() -> OperationsRepository:
    return OperationsRepository(
        supabase_url="https://supabase.example.com", service_role_key="service-role"
    )


def test_count_usage_sums_in_the_database_with_one_rpc(monkeypatch) -> None:
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append((url, json))
        return httpx.Response(200, json=42.5, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.operations.httpx.post", fake_post)
    workspace_id = uuid4()
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 11, 1, tzinfo=UTC)

    used = _database_operations().count_usage(
        workspace_id=workspace_id,
        category="tokens",
        period_start=start,
        period_end=end,
        provider="provider-a",
    )

    assert used == 42.5
    assert calls == [
        (
            "https://supabase.example.com/rest/v1/rpc/motionly_sum_cost_usage",
            {
                "p_workspace_id": str(workspace_id),
                "p_category": "tokens",
                "p_period_start": start.isoformat(),
                "p_period_end": end.isoformat(),
                "p_provider": "provider-a",
            },
        )
    ]


def test_count_usage_raises_instead_of_counting_a_local_copy(monkeypatch) -> None:
    def failing_post(url, **_kwargs):
        raise httpx.ConnectError("database unavailable", request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.operations.httpx.post", failing_post)
    repository = _database_operations()
    # Even a populated in-process list must not be used as the answer.
    repository.cost_events.append({"workspace_id": "w", "category": "tokens", "quantity": 1.0})

    with pytest.raises(UsageUnavailableError):
        repository.count_usage(
            workspace_id=uuid4(),
            category="tokens",
            period_start=datetime(2026, 10, 1, tzinfo=UTC),
            period_end=datetime(2026, 11, 1, tzinfo=UTC),
        )


def test_recorded_costs_are_not_kept_in_process_when_the_database_is_configured(
    monkeypatch,
) -> None:
    inserted = []
    repository = _database_operations()
    monkeypatch.setattr(
        repository, "_insert", lambda table, values, **kwargs: inserted.append(table)
    )

    for _ in range(3):
        repository.record_cost(workspace_id=uuid4(), provider="p", category="tokens", quantity=1.0)

    assert inserted == ["cost_attribution_events"] * 3
    assert repository.cost_events == []


def test_check_quota_refuses_with_503_when_usage_cannot_be_read() -> None:
    workspace_id = uuid4()
    billing = InMemoryBillingRepository()
    billing.upsert_subscription(
        workspace_id=workspace_id, plan=BillingPlanKey.CREATOR, status=SubscriptionStatus.ACTIVE
    )

    class UnavailableUsage:
        def count_usage(self, **_kwargs):
            raise UsageUnavailableError("down")

    with pytest.raises(ApiError) as refused:
        check_quota(
            workspace_id=workspace_id,
            feature="planning",
            category="tokens",
            billing_repository=billing,
            operations_repository=UnavailableUsage(),
            estimated_quantity=1.0,
        )

    assert refused.value.status_code == 503
    assert refused.value.code == "planning_quota_unavailable"


# ---------------------------------------------------------------------------
# DB-10: upload status writes are compare-and-set and tenant-scoped.
# ---------------------------------------------------------------------------


def _upload_fixture(status: str):
    workspace_id, project_id, upload_id = uuid4(), uuid4(), uuid4()

    def row(current: str) -> dict[str, object]:
        return {
            "id": str(upload_id),
            "project_id": str(project_id),
            "workspace_id": str(workspace_id),
            "storage_bucket": "raw-uploads",
            "storage_path": f"workspaces/{workspace_id}/projects/{project_id}/raw/{upload_id}.mp4",
            "original_filename": "launch.mp4",
            "mime_type": "video/mp4",
            "file_size_bytes": 10_000,
            "byte_size": 10_000,
            "status": current,
            "thumbnail_path": None,
            "metadata": {},
            "error_code": None,
            "error_message": None,
            "created_at": NOW,
            "updated_at": NOW,
        }

    repository = SupabaseUploadRepository(
        supabase_url="https://supabase.example.com", service_role_key="service-role"
    )
    return repository, repository._upload_from_row(row(status)), row


def test_upload_status_patch_matches_only_allowed_predecessors_in_the_tenant(
    monkeypatch,
) -> None:
    repository, upload, row = _upload_fixture("processing")
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return _response([row("ready")])

    monkeypatch.setattr("app.repositories.uploads.SupabaseRestClient.request", fake_request)

    updated = repository.transition_upload(upload=upload, status=UploadStatus.READY)

    assert updated.status == UploadStatus.READY
    params = calls[0][2]["params"]
    assert params["id"] == f"eq.{upload.id}"
    assert params["workspace_id"] == f"eq.{upload.workspace_id}"
    assert params["project_id"] == f"eq.{upload.project_id}"
    assert params["status"] == "in.(processing)"


def test_upload_status_patch_that_lost_the_race_is_a_conflict(monkeypatch) -> None:
    repository, upload, row = _upload_fixture("processing")

    def fake_request(_self, method, path, **_kwargs):
        # The PATCH matched nothing: a concurrent cancel already failed it.
        return _response([] if method == "PATCH" else [row("failed")])

    monkeypatch.setattr("app.repositories.uploads.SupabaseRestClient.request", fake_request)

    with pytest.raises(ApiError) as conflict:
        repository.transition_upload(upload=upload, status=UploadStatus.READY)

    assert conflict.value.status_code == 409
    assert conflict.value.code == "upload_invalid_state_transition"


def test_upload_status_patch_replayed_after_success_returns_the_upload(monkeypatch) -> None:
    repository, upload, row = _upload_fixture("processing")

    def fake_request(_self, method, path, **_kwargs):
        # A retried request: the first attempt already moved it to ready.
        return _response([] if method == "PATCH" else [row("ready")])

    monkeypatch.setattr("app.repositories.uploads.SupabaseRestClient.request", fake_request)

    replayed = repository.transition_upload(upload=upload, status=UploadStatus.READY)

    assert replayed.status == UploadStatus.READY


# ---------------------------------------------------------------------------
# DB-10: a plan edit without expected_revision is still compare-and-set.
# ---------------------------------------------------------------------------


class _RevisionedPlans:
    def __init__(self, revision: int) -> None:
        self.revision = revision
        self.calls: list[str] = []

    def update_plan_with_revision(self, *, plan_id, expected_revision, **_kwargs):
        self.calls.append(f"cas:{expected_revision}")
        if expected_revision != self.revision:
            raise RevisionConflictError(
                plan_id=str(plan_id), expected=expected_revision, actual=self.revision
            )
        self.revision += 1
        return SimpleNamespace(revision=self.revision)

    def update_plan_cuts(self, **_kwargs):
        self.calls.append("unconditional")
        return SimpleNamespace(revision=None)


def _save_unversioned(repository, record):
    return asyncio.run(
        _update_plan_unversioned(
            repository,
            record=record,
            project_id=uuid4(),
            workspace_id=uuid4(),
            plan_id=uuid4(),
            plan=object(),
            summary=object(),
            target_duration_s=10,
            change_type="cuts_update",
            request=None,
        )
    )


def test_unversioned_plan_edit_is_saved_against_the_revision_it_was_computed_from() -> None:
    repository = _RevisionedPlans(revision=7)

    updated = _save_unversioned(repository, SimpleNamespace(revision=7))

    assert repository.calls == ["cas:7"]
    assert updated.revision == 8


def test_unversioned_plan_edit_does_not_overwrite_a_concurrent_save() -> None:
    # Another editor saved revision 8 after this request read revision 7.
    repository = _RevisionedPlans(revision=8)

    with pytest.raises(RevisionConflictError):
        _save_unversioned(repository, SimpleNamespace(revision=7))

    assert repository.calls == ["cas:7"]


def test_unversioned_plan_edit_of_a_legacy_row_without_revision_saves_as_before() -> None:
    repository = _RevisionedPlans(revision=1)

    _save_unversioned(repository, SimpleNamespace(revision=None))

    assert repository.calls == ["unconditional"]


# ---------------------------------------------------------------------------
# DB-10: transcript words are rewritten only when the caller passes them.
# ---------------------------------------------------------------------------


def _caption_fixture(monkeypatch):
    repository = SupabaseCaptionRepository(
        upload_repository=InMemoryUploadRepository(),
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    transcript = TranscriptRecord(
        id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        status=TranscriptStatus.PROCESSING,
        language="en",
        language_hint=LanguageHint.AUTO,
        provider="fixture",
        model="fixture",
        confidence=None,
        duration_ms=1000,
        words=[TranscriptWord(index=0, text="old", start_ms=0, end_ms=100)],
    )
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/transcripts":
            body = kwargs["json"]
            return _response(
                [
                    {
                        "id": str(transcript.id),
                        "workspace_id": str(transcript.workspace_id),
                        "project_id": str(transcript.project_id),
                        "uploaded_video_id": str(transcript.uploaded_video_id),
                        "status": body.get("status", "processing"),
                        "provider": "fixture",
                        "model": "fixture",
                        "created_at": NOW,
                        "updated_at": NOW,
                    }
                ]
            )
        return _response([])

    monkeypatch.setattr("app.repositories.captions.SupabaseRestClient.request", fake_request)
    return repository, transcript, calls


def test_transcript_status_change_does_not_rewrite_words(monkeypatch) -> None:
    repository, transcript, calls = _caption_fixture(monkeypatch)

    updated = repository.update_transcript(transcript=transcript, status=TranscriptStatus.READY)

    assert [call[:2] for call in calls] == [("PATCH", "/transcripts")]
    assert calls[0][2]["params"]["workspace_id"] == f"eq.{transcript.workspace_id}"
    assert [word.text for word in updated.words] == ["old"]


def test_transcript_words_are_replaced_in_one_rpc(monkeypatch) -> None:
    repository, transcript, calls = _caption_fixture(monkeypatch)
    words = [TranscriptWord(index=0, text="new", start_ms=0, end_ms=100)]

    updated = repository.update_transcript(
        transcript=transcript, status=TranscriptStatus.READY, words=words
    )

    assert [call[:2] for call in calls] == [
        ("POST", "/rpc/motionly_replace_transcript_words"),
        ("PATCH", "/transcripts"),
    ]
    assert calls[0][2]["json"]["p_words"][0]["text"] == "new"
    assert [word.text for word in updated.words] == ["new"]


# ---------------------------------------------------------------------------
# DB-04: workers start without the JWT signing secret; the API does not.
# ---------------------------------------------------------------------------


def _production_env(monkeypatch, *, process_role: str) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PROCESS_ROLE", process_role)
    monkeypatch.delenv("SKIP_API_AUTH", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET_FILE", raising=False)
    get_settings.cache_clear()


def test_worker_settings_load_without_the_jwt_secret(monkeypatch) -> None:
    _production_env(monkeypatch, process_role="worker")
    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.process_role == "worker"
    assert settings.supabase_jwt_secret is None


@pytest.mark.parametrize("process_role", ["api", "orchestration"])
def test_api_and_orchestration_still_require_the_jwt_secret(monkeypatch, process_role) -> None:
    _production_env(monkeypatch, process_role=process_role)
    try:
        with pytest.raises(ValidationError) as missing:
            get_settings()
    finally:
        get_settings.cache_clear()

    assert "SUPABASE_JWT_SECRET is required" in str(missing.value)


def test_compose_workers_do_not_receive_the_jwt_secret() -> None:
    compose = (ROOT / "infra" / "docker-compose.gpu.yml").read_text("utf-8")
    worker_secrets = re.search(r"&worker-secrets \[([^\]]*)\]", compose)

    assert worker_secrets is not None
    assert "supabase_jwt_secret" not in worker_secrets.group(1)


# ---------------------------------------------------------------------------
# DB-02 / DB-15: WAL archiving, the backup agent, its alarms.
# ---------------------------------------------------------------------------


def test_both_databases_archive_wal_to_their_backup_volume() -> None:
    compose = (ROOT / "infra" / "docker-compose.gpu.yml").read_text("utf-8")

    assert compose.count("-c archive_mode=on") == 2
    assert compose.count('-c "archive_command=wal-g wal-push %p"') == 2
    assert "WALG_FILE_PREFIX: /var/lib/motionly-backups/postgres" in compose
    assert "WALG_FILE_PREFIX: /var/lib/motionly-backups/temporal" in compose
    assert "postgres-backups:/var/lib/motionly-backups/postgres" in compose
    assert "temporal-postgres-backups:/var/lib/motionly-backups/temporal" in compose


def test_host_bootstrap_requires_the_backup_bucket_and_installs_the_timers() -> None:
    host_env = (ROOT / "infra" / "compose-host" / "host.env.example").read_text("utf-8")
    bootstrap = (ROOT / "scripts" / "deploy" / "bootstrap_compose_host.sh").read_text("utf-8")

    assert "MOTIONLY_BACKUP_BUCKET=" in host_env
    required = re.search(r"^for value in ([^;]+); do$", bootstrap, re.MULTILINE)
    assert required is not None and "MOTIONLY_BACKUP_BUCKET" in required.group(1).split()
    assert "ExecStart=/opt/motionly/agent/bin/db-backup %i" in bootstrap
    assert "Unit=motionly-db-backup@daily.service" in bootstrap
    assert "Unit=motionly-db-backup@frequent.service" in bootstrap
    assert (
        "systemctl enable --now motionly-db-backup-daily.timer "
        "motionly-db-backup-frequent.timer" in bootstrap
    )


def test_stack_snapshots_the_data_volume_and_alarms_on_backup_metrics() -> None:
    template = json.loads((ROOT / "infra" / "aws" / "motionly.template.json").read_text("utf-8"))
    resources = template["Resources"]

    snapshots = resources["DataVolumeSnapshots"]["Properties"]["PolicyDetails"]
    data_tags = {tag["Key"] for tag in resources["DataVolume"]["Properties"]["Tags"]}
    assert {tag["Key"] for tag in snapshots["TargetTags"]} <= data_tags
    alarms = {
        name: resource["Properties"]
        for name, resource in resources.items()
        if resource["Type"] == "AWS::CloudWatch::Alarm"
    }
    for service in ("Postgres", "TemporalPostgres"):
        for kind in ("DownAlarm", "BaseBackupAgeAlarm", "BackupSyncAgeAlarm", "WalArchiveAlarm"):
            assert f"{service}{kind}" in alarms
    assert "DataVolumeUsageAlarm" in alarms
    for properties in alarms.values():
        assert properties["Namespace"] == "Motionly/Database"
        assert properties["AlarmActions"] == [{"Ref": "DatabaseAlarmTopic"}]
    bucket_rules = resources["BackupsBucket"]["Properties"]["LifecycleConfiguration"]["Rules"]
    assert any(rule.get("Prefix") == "database/" for rule in bucket_rules)


def _fake_tools(tmp_path: Path) -> Path:
    """docker and aws stand-ins: postgres runs, temporal-postgres does not."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/bin/sh
case "$1" in
  ps)
    for arg in "$@"; do
      [ "$arg" = "label=com.docker.compose.service=postgres" ] && echo postgres-container
    done
    exit 0 ;;
  volume)
    echo "$FAKE_VOLUME_ROOT/$5"; exit 0 ;;
  exec)
    case "$*" in
      *pg_isready*) exit 0 ;;
      *pg_stat_archiver*) echo "$FAKE_ARCHIVED $FAKE_FAILED"; exit 0 ;;
      *backup-list*) printf '[{"backup_name":"base_1","time":"%s"}]' "$FAKE_BACKUP_TIME"; exit 0 ;;
    esac ;;
esac
exit 1
""",
        encoding="utf-8",
    )
    aws = bin_dir / "aws"
    aws.write_text(
        """#!/bin/sh
echo "$*" >> "$FAKE_AWS_LOG"
for arg in "$@"; do
  case "$arg" in file://*) cp "${arg#file://}" "$FAKE_AWS_LOG.metrics.json" ;; esac
done
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    aws.chmod(0o755)
    return bin_dir


def _run_backup_agent(tmp_path: Path, command: str, **environment: str):
    bin_dir = _fake_tools(tmp_path)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "MOTIONLY_HOST_ENV": str(tmp_path / "missing-host.env"),
        "MOTIONLY_ENVIRONMENT": "production",
        "MOTIONLY_BACKUP_STATE_DIR": str(tmp_path / "state"),
        "MOTIONLY_DATA_MOUNT": str(tmp_path),
        "FAKE_AWS_LOG": str(tmp_path / "aws.log"),
        "FAKE_VOLUME_ROOT": str(tmp_path / "volumes"),
        **environment,
    }
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "deploy" / "db_backup.sh"), command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_backup_agent_metrics_drive_the_alarms(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    archived = int((now - timedelta(minutes=10)).timestamp())
    result = _run_backup_agent(
        tmp_path,
        "metrics",
        FAKE_ARCHIVED=str(archived),
        # The last archive attempt failed after the last success.
        FAKE_FAILED=str(archived + 60),
        FAKE_BACKUP_TIME=(now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    )

    assert result.returncode == 0, result.stderr
    metrics = json.loads((tmp_path / "aws.log.metrics.json").read_text("utf-8"))
    by_key = {
        (
            metric["MetricName"],
            next((d["Value"] for d in metric["Dimensions"] if d["Name"] == "Service"), None),
        ): metric["Value"]
        for metric in metrics
    }
    assert by_key[("DatabaseUp", "postgres")] == 1
    assert by_key[("WalArchiveFailing", "postgres")] == 1
    assert 3500 < by_key[("BaseBackupAgeSeconds", "postgres")] < 3700
    # Never synced, and the stopped Temporal database: both read as breaching.
    assert by_key[("BackupSyncAgeSeconds", "postgres")] >= 10**9
    assert by_key[("DatabaseUp", "temporal-postgres")] == 0
    assert by_key[("BaseBackupAgeSeconds", "temporal-postgres")] >= 10**9
    assert ("DataVolumeUsedPercent", None) in by_key
    assert "--namespace Motionly/Database" in (tmp_path / "aws.log").read_text("utf-8")


def test_backup_agent_syncs_each_volume_to_its_prefix_and_needs_the_bucket(
    tmp_path: Path,
) -> None:
    for volume in ("motionly_postgres-backups", "motionly_temporal-postgres-backups"):
        (tmp_path / "volumes" / volume).mkdir(parents=True)

    unset = _run_backup_agent(tmp_path, "sync")
    synced = _run_backup_agent(tmp_path, "sync", MOTIONLY_BACKUP_BUCKET="motionly-backups")

    assert unset.returncode == 78
    assert "MOTIONLY_BACKUP_BUCKET is not set" in unset.stderr
    assert synced.returncode == 0, synced.stderr
    log = (tmp_path / "aws.log").read_text("utf-8")
    assert "s3://motionly-backups/database/production/postgres/" in log
    assert "s3://motionly-backups/database/production/temporal-postgres/" in log
    assert (tmp_path / "state" / "postgres.synced").exists()


# ---------------------------------------------------------------------------
# DB-03 / DB-09: restore drill keeps grants; migrations wait for locks briefly.
# ---------------------------------------------------------------------------


def test_restore_drill_keeps_grants_and_compares_privileges() -> None:
    drill = (ROOT / "scripts" / "verify_backup_restore.sh").read_text("utf-8")
    commands = [
        line for line in drill.splitlines() if line.lstrip().startswith(("pg_dump", "pg_restore"))
    ]

    assert commands and not any("--no-acl" in line or "-x " in line for line in commands)
    assert "fingerprint_privileges" in drill
    assert "has_function_privilege" in drill and "pg_policies" in drill
    # psql must not run the \gexec generator twice (that doubled the table count).
    assert "order by table_name\n\\gexec" in drill


@pytest.mark.parametrize(
    "workflow",
    [
        ROOT / ".github" / "workflows" / "phase-0-safety.yml",
        ROOT.parents[1] / ".github" / "workflows" / "production-readiness.yml",
    ],
)
def test_ci_restore_drill_passes_service_names_not_urls(workflow: Path) -> None:
    if not workflow.exists():
        pytest.skip(f"{workflow} is not part of this checkout")
    text = workflow.read_text("utf-8")
    invocation = next(line for line in text.splitlines() if "verify_backup_restore.sh" in line)

    assert "postgresql://" not in invocation
    assert invocation.rstrip().endswith("motionly_source motionly_restore")
    assert "PGSERVICEFILE=" in text and "PGPASSFILE=" in text
    assert 'chmod 600 "$PGSERVICEFILE" "$PGPASSFILE"' in text


def test_migrations_set_a_lock_timeout_after_taking_the_release_lock() -> None:
    migrate = (ROOT / "infra" / "db" / "migrate.sh").read_text("utf-8")

    assert 'lock_timeout="${MIGRATION_LOCK_TIMEOUT:-15s}"' in migrate
    assert migrate.index("pg_advisory_lock(") < migrate.index("set lock_timeout = ")


def test_audit_migrations_are_well_named_and_each_has_a_pgtap_test() -> None:
    names = sorted(path.name for path in (ROOT / "supabase" / "migrations").glob("*.sql"))
    audit = [name for name in names if "_db_audit_" in name]

    assert len(audit) == 9
    assert all(re.fullmatch(r"[0-9]{12}_[a-z0-9_]+\.sql", name) for name in audit)
    # Each audit migration has its pgTAP test.
    tests = {path.name for path in (ROOT / "supabase" / "tests").glob("db_audit_*_test.sql")}
    for name in audit:
        assert f"{name[13:-4]}_test.sql" in tests, name


# ---------------------------------------------------------------------------
# DB-12: gateway webhooks change a subscription in event order.
# ---------------------------------------------------------------------------


def _gateway_event(event: str, created_at: str, workspace_id, *, event_id: str | None = None):
    notes = {"workspace_id": str(workspace_id), "plan": "creator"}
    body: dict[str, object] = {
        "id": event_id or f"evt_{event}_{created_at}",
        "event": event,
        "created_at": created_at,
    }
    if event in {"payment.captured", "subscription.charged"}:
        body["order"] = {
            "id": f"order_{created_at}",
            "status": "captured",
            "amount_paise": 99900,
            "currency": "INR",
            "notes": notes,
            "created_at": created_at,
        }
    if event.startswith("subscription."):
        body["subscription"] = {
            "id": "sub_db12",
            "status": "cancelled" if event == "subscription.cancelled" else "active",
            "amount_paise": 99900,
            "currency": "INR",
            "total_cycles": 12,
            "completed_cycles": 1,
            "notes": notes,
            "created_at": "2026-09-01T00:00:00Z",
        }
    return WebhookEnvelope.model_validate(body)


def _apply(envelope, repository) -> None:
    asyncio.run(apply_gateway_event_async(envelope=envelope, repository=repository))


def test_a_late_charge_does_not_reactivate_a_cancelled_subscription() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    _apply(_gateway_event("payment.captured", "2026-10-01T09:00:00Z", workspace_id), repository)
    _apply(
        _gateway_event("subscription.cancelled", "2026-10-03T09:00:00Z", workspace_id), repository
    )

    # A renewal charge created before the cancel, delivered after it.
    late = _gateway_event("subscription.charged", "2026-10-02T09:00:00Z", workspace_id)
    _apply(late, repository)

    subscription = repository.get_subscription(workspace_id=workspace_id)
    assert subscription.status == SubscriptionStatus.CANCELED
    assert subscription.plan == BillingPlanKey.FREE
    assert subscription.last_gateway_event_at == datetime(2026, 10, 3, 9, tzinfo=UTC)
    # The money still moved: the late charge is in the ledger.
    assert late.id in repository.billing_adjustments


def test_a_late_charge_failure_does_not_mark_a_renewed_subscription_past_due() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    _apply(_gateway_event("subscription.charged", "2026-10-05T09:00:00Z", workspace_id), repository)
    _apply(
        _gateway_event("subscription.charge_failed", "2026-10-04T09:00:00Z", workspace_id),
        repository,
    )

    assert repository.get_subscription(workspace_id=workspace_id).status == (
        SubscriptionStatus.ACTIVE
    )


def test_events_created_at_the_same_instant_apply_in_arrival_order() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    stamp = "2026-10-05T09:00:00Z"
    _apply(_gateway_event("subscription.charge_failed", stamp, workspace_id), repository)
    _apply(_gateway_event("subscription.charged", stamp, workspace_id), repository)

    assert repository.get_subscription(workspace_id=workspace_id).status == (
        SubscriptionStatus.ACTIVE
    )


def test_the_billing_period_is_the_month_of_the_payment_not_of_the_delivery() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    # Paid a minute before midnight on 30 September; delivered in October.
    _apply(_gateway_event("payment.captured", "2026-09-30T23:59:00Z", workspace_id), repository)

    subscription = repository.get_subscription(workspace_id=workspace_id)
    assert subscription.current_period_start == datetime(2026, 9, 1, tzinfo=UTC)
    assert subscription.current_period_end == datetime(2026, 10, 1, tzinfo=UTC)


def test_an_unreadable_event_time_applies_without_moving_the_watermark() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    _apply(_gateway_event("payment.captured", "2026-10-01T09:00:00Z", workspace_id), repository)
    _apply(_gateway_event("subscription.cancelled", "not-a-time", workspace_id), repository)

    subscription = repository.get_subscription(workspace_id=workspace_id)
    assert subscription.status == SubscriptionStatus.CANCELED
    assert subscription.last_gateway_event_at == datetime(2026, 10, 1, 9, tzinfo=UTC)


def test_supabase_subscription_upsert_sends_the_event_time_and_reports_a_skip(
    monkeypatch,
) -> None:
    repository = SupabaseBillingRepository(
        supabase_url="https://supabase.example.com", service_role_key="service-role"
    )
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append(kwargs)
        # The database skipped the stale write: no row comes back.
        return _response([])

    monkeypatch.setattr("app.repositories.billing.SupabaseRestClient.request", fake_request)
    event_at = datetime(2026, 10, 2, 9, tzinfo=UTC)

    result = repository.upsert_subscription(
        workspace_id=uuid4(),
        plan=BillingPlanKey.CREATOR,
        status=SubscriptionStatus.ACTIVE,
        last_gateway_event_at=event_at,
    )

    assert result is None
    assert calls[0]["json"]["last_gateway_event_at"] == event_at.isoformat()
    assert calls[0]["params"] == {"on_conflict": "workspace_id"}


# ---------------------------------------------------------------------------
# DB-11: a render belongs to one worker attempt at a time.
# ---------------------------------------------------------------------------


def _render_fixture(status=None):
    from app.schemas.renders import RenderJobStatus
    from tests.test_renders import make_render_repository

    repository, project_id, workspace_id, _ = make_render_repository()
    job = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=repository.get_active_upload(
            project_id=project_id, workspace_id=workspace_id
        ).id,
        caption_track_id=None,
        status=status or RenderJobStatus.QUEUED,
        planner_version="fencing-test",
        manifest={"include_captions": False},
        keep_segments=[],
        progress_pct=5,
    )
    return repository, job


def test_a_claimed_render_rejects_writes_from_a_superseded_attempt() -> None:
    from app.schemas.renders import RenderJobStatus

    repository, job = _render_fixture()
    first, second = uuid4(), uuid4()
    repository.claim_render_attempt(job=job, attempt_id=first)
    # A stalled redelivery takes the render over.
    claimed = repository.claim_render_attempt(job=job, attempt_id=second)
    assert (claimed.attempt_id, claimed.attempt_number) == (second, 2)

    with pytest.raises(ApiError) as superseded:
        repository.transition_job(
            job=job,
            status=RenderJobStatus.FAILED,
            progress_pct=10,
            message="stale failure",
            attempt_id=first,
        )

    assert superseded.value.code == "render_attempt_superseded"
    assert repository.get_render_job_by_id(render_job_id=job.id).status == (
        RenderJobStatus.PREPARING
    )


def test_requeueing_a_render_releases_its_attempt() -> None:
    from app.schemas.renders import RenderJobStatus

    repository, job = _render_fixture()
    attempt = uuid4()
    repository.claim_render_attempt(job=job, attempt_id=attempt)
    repository.transition_job(
        job=job,
        status=RenderJobStatus.FAILED,
        progress_pct=10,
        message="failed",
        attempt_id=attempt,
    )
    retried = repository.retry_render_job(render_job_id=job.id)

    assert retried.status == RenderJobStatus.QUEUED
    assert retried.attempt_id is None
    with pytest.raises(ApiError) as superseded:
        repository.transition_job(
            job=job,
            status=RenderJobStatus.RENDERING,
            progress_pct=70,
            message="late progress",
            attempt_id=attempt,
        )
    assert superseded.value.code == "render_attempt_superseded"


def test_finished_or_canceled_renders_cannot_be_claimed() -> None:
    from app.schemas.renders import RenderJobStatus

    for status, code in (
        (RenderJobStatus.CANCELED, "render_cancelled"),
        (RenderJobStatus.FAILED, "render_terminal_state"),
        (RenderJobStatus.COMPLETE, "render_already_complete"),
    ):
        repository, job = _render_fixture(status)
        with pytest.raises(ApiError) as refused:
            repository.claim_render_attempt(job=job, attempt_id=uuid4())
        assert refused.value.code == code


def test_a_superseded_ffmpeg_attempt_stops_without_writing(monkeypatch) -> None:
    from app.schemas.renders import RenderJobStatus
    from app.services.render_jobs import complete_render_job

    repository, job = _render_fixture()
    newer = uuid4()

    def preflight_during_which_another_run_takes_over(**kwargs):
        repository.claim_render_attempt(job=kwargs["job"], attempt_id=newer)
        return {
            "storage": None,
            "source_path": None,
            "output_path": None,
            "broll_assets": [],
            "audio_mix": {},
            "effects_config": None,
            "matte_path": None,
        }

    monkeypatch.setattr(
        "app.services.render_jobs._run_render_preflight",
        preflight_during_which_another_run_takes_over,
    )
    renderer = SimpleNamespace(render=lambda **_: pytest.fail("a superseded run must not render"))

    with pytest.raises(ApiError) as superseded:
        complete_render_job(
            job=job,
            repository=repository,
            export_bucket="exports",
            storage_root="/tmp",
            supabase_url=None,
            service_role_key=None,
            renderer=renderer,
            attempt_id=uuid4(),
        )

    assert superseded.value.code == "render_attempt_superseded"
    stored = repository.get_render_job_by_id(render_job_id=job.id)
    assert (stored.status, stored.attempt_id) == (RenderJobStatus.PREPARING, newer)
    assert not [e for e in repository.events_for_job(job.id) if e.event_type == "failed"]
    assert not [e for e in repository.exports.values() if e.render_job_id == job.id]


def test_supabase_render_writes_carry_the_attempt(monkeypatch) -> None:
    from app.repositories.renders import SupabaseRenderRepository
    from app.schemas.renders import RenderJobStatus
    from tests.test_renders import make_render_repository

    memory, _, _, _ = make_render_repository()
    repository = SupabaseRenderRepository(
        caption_repository=memory.caption_repository,
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    _, job = _render_fixture()
    attempt = uuid4()
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/rpc/motionly_claim_render_attempt":
            return SimpleNamespace(
                status_code=400,
                headers={},
                json=lambda: {"code": "P0001", "message": "RENDER_CANCELED"},
            )
        return _response([])  # the PATCH matched no row

    monkeypatch.setattr("app.repositories.renders.SupabaseRestClient.request", fake_request)

    with pytest.raises(ApiError) as cancelled:
        repository.claim_render_attempt(job=job, attempt_id=attempt)
    with pytest.raises(ApiError) as superseded:
        repository.transition_job(
            job=job,
            status=RenderJobStatus.RENDERING,
            progress_pct=70,
            message="progress",
            attempt_id=attempt,
        )

    assert cancelled.value.code == "render_cancelled"
    assert calls[0][2]["json"]["p_attempt_id"] == str(attempt)
    assert calls[1][2]["params"]["attempt_id"] == f"eq.{attempt}"
    assert superseded.value.code == "render_attempt_superseded"


def test_superseded_render_attempts_are_never_retried() -> None:
    from app.workers.executor_protocol import error_reply

    reply = error_reply(1, ApiError(409, "render_attempt_superseded", "superseded"))

    assert reply["retryable"] is False


def test_the_worker_attempt_id_reaches_the_render_job() -> None:
    from app.workers import domain_job

    captured = {}

    def fake_execute_job(*, job, request):
        captured["attempt"] = job.render_attempt_id
        return {"failed": False}

    import app.services.job_engine as job_engine

    original = job_engine._execute_job
    job_engine._execute_job = fake_execute_job
    try:
        domain_job._execute(
            {
                "job_id": "render--p--r--w",
                "kind": "render",
                "queue_name": "render",
                "workspace_id": str(uuid4()),
                "project_id": str(uuid4()),
                "resource_id": str(uuid4()),
                "render_attempt_id": "a1000000-0000-4000-8000-000000000001",
            }
        )
    finally:
        job_engine._execute_job = original

    assert captured["attempt"] == "a1000000-0000-4000-8000-000000000001"


def test_the_job_engine_passes_the_attempt_to_the_ffmpeg_render(monkeypatch) -> None:
    from app.schemas.renders import RenderJobStatus
    from app.services import job_engine
    from app.services.queue import EnqueuedJob

    captured = {}

    def fake_complete(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=kwargs["render_job_id"], status=RenderJobStatus.COMPLETE)

    monkeypatch.setattr(job_engine, "complete_render_job_by_id", fake_complete)
    monkeypatch.setattr(job_engine, "record_render_success_outcome", lambda **_: None)
    settings = SimpleNamespace(
        export_storage_bucket="exports",
        media_work_dir="/tmp",
        supabase_url=None,
        supabase_service_role_key=None,
        app_env="test",
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings, render_repository=object()))
    )
    attempt = "a1000000-0000-4000-8000-000000000001"

    result = job_engine._execute_job(
        job=EnqueuedJob(
            id="render--p--r--w",
            kind="render",
            workspace_id=uuid4(),
            project_id=uuid4(),
            resource_id=uuid4(),
            render_attempt_id=attempt,
        ),
        request=request,
    )

    assert result["failed"] is False
    assert str(captured["attempt_id"]) == attempt


# ---------------------------------------------------------------------------
# DB-21: own passwords per service role, and TLS to both database servers.
# ---------------------------------------------------------------------------

_TLS_FLAGS = (
    "-c ssl=on",
    "-c ssl_cert_file=/etc/motionly-db-tls/server/server.crt",
    "-c ssl_key_file=/etc/motionly-db-tls/server/server.key",
    "-c ssl_min_protocol_version=TLSv1.2",
    "-c hba_file=/etc/motionly-db/pg_hba.conf",
)


def _compose_services() -> dict:
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_multi_constructor("!", lambda loader, _s, node: loader.construct_scalar(node))
    source = (ROOT / "infra" / "docker-compose.gpu.yml").read_text("utf-8")
    return yaml.load(source, Loader=Loader)["services"]  # noqa: S506


def test_both_database_servers_serve_tls_and_refuse_plain_password_logins() -> None:
    services = _compose_services()
    hba = {
        "postgres": "./db/pg_hba.conf",
        "temporal-postgres": "./db/pg_hba.temporal.conf",
    }
    for name, hba_file in hba.items():
        service = services[name]
        command = " ".join(service["entrypoint"])
        for flag in _TLS_FLAGS:
            assert flag in command, (name, flag)
        assert "postgres-tls-server:/etc/motionly-db-tls/server:ro" in service["volumes"]
        assert f"{hba_file}:/etc/motionly-db/pg_hba.conf:ro" in service["volumes"]
        assert service["depends_on"]["db-tls"] == {"condition": "service_completed_successfully"}

    for path, exception in (("pg_hba.conf", True), ("pg_hba.temporal.conf", False)):
        rules = [
            line.split()
            for line in (ROOT / "infra" / "db" / path).read_text("utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        remote = [
            rule
            for rule in rules
            if rule[0].startswith("host") and rule[3] not in ("127.0.0.1/32", "::1/128")
        ]
        # Every non-loopback password login needs TLS, except Realtime's
        # tenant connection on the Supabase database (it cannot use TLS).
        plain = [rule for rule in remote if rule[0] == "host"]
        assert plain == (
            [["host", "postgres", "supabase_admin", "0.0.0.0/0", "scram-sha-256"]]
            if exception
            else []
        ), path
        assert all(rule[-1] == "scram-sha-256" for rule in remote), path
        assert ["hostssl", "all", "all", "0.0.0.0/0", "scram-sha-256"] in remote


def test_every_database_client_verifies_the_server_certificate() -> None:
    services = _compose_services()
    db = ROOT / "infra" / "db"
    for script in ("start-auth.sh", "start-storage.sh", "apply-role-passwords.sh"):
        text = (db / script).read_text("utf-8")
        assert "sslmode=verify-full&sslrootcert=" in text, script
    for name in ("db-migrate", "temporal-db-init"):
        environment = services[name]["environment"]
        assert environment["PGSSLMODE"] == "verify-full", name
        assert environment["PGSSLROOTCERT"] == "/etc/motionly-db-tls/ca/ca.crt", name
    realtime = services["supabase-realtime"]["environment"]
    assert realtime["DB_SSL"] == "true"
    assert realtime["DB_SSL_CA_CERT"] == "/etc/motionly-db-tls/ca/ca.crt"
    for name in (
        "supabase-auth",
        "supabase-rest",
        "supabase-storage",
        "supabase-realtime",
        "db-migrate",
        "temporal-db-init",
        "temporal-schema",
        "temporal",
    ):
        assert "postgres-tls-ca:/etc/motionly-db-tls/ca:ro" in services[name]["volumes"], name
    # Only db-tls ever mounts the CA key.
    assert [
        name
        for name, service in services.items()
        if any(str(v).startswith("postgres-tls-ca-key:") for v in service.get("volumes", []))
    ] == ["db-tls"]
    temporal = ROOT / "infra" / "temporal"
    schema = (temporal / "apply-schema.sh").read_text("utf-8")
    assert schema.count("--tls-ca-file /etc/motionly-db-tls/ca/ca.crt") == 2
    assert "--tls-disable-host-verification" not in schema
    template = (temporal / "config" / "production.yaml.tmpl").read_text("utf-8")
    assert template.count("enableHostVerification: true") == 2
    assert template.count("serverName: temporal-postgres") == 2


def test_the_production_image_harness_starts_postgres_like_compose() -> None:
    harness = (ROOT / "scripts" / "db" / "test_production_image.sh").read_text("utf-8")
    flat = " ".join(harness.replace("\\\n", " ").split())
    for flag in _TLS_FLAGS:
        assert flag in flat, flag
    for script in (
        "generate-tls.sh",
        "apply-role-passwords.sh",
        "start-auth.sh",
        "start-storage.sh",
    ):
        assert f"infra/db/{script}" in harness, script
    assert "PGRST_DB_URI=@/var/lib/motionly-db-credentials/db-uri" in harness
