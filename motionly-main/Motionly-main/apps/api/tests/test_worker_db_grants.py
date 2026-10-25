"""The restricted worker role is granted exactly what worker code needs.

``supabase/migrations/202610230101_task18_worker_least_privilege.sql`` grants
``motionly_worker`` an explicit allowlist of job tables and RPCs. These tests
compare it with the database usage of code that runs with the worker
credential (``tests/worker_db_reachability.py``), so a worker change that needs
a new grant fails here rather than in production, and identity, membership,
billing and credential objects can never be added to the worker's reach.
The live grants are checked by supabase/tests/task_18_worker_least_privilege_test.sql.
"""

from __future__ import annotations

import re

import pytest

from tests.schema_usage import ROOT
from tests.worker_db_reachability import WORKER_ROOTS, worker_database_usage

NARROWING_VERSION = "202610230101"
MIGRATIONS = ROOT / "supabase" / "migrations"

TABLE_GRANT = re.compile(
    r"grant\s+([a-z, ]+?)\s+on\s+(?:table\s+)?public\.([a-z0-9_]+)\s+to\s+motionly_worker\s*;",
    re.IGNORECASE,
)
FUNCTION_GRANT = re.compile(
    r"grant\s+execute\s+on\s+function\s+public\.([a-z0-9_]+)\b[^;]*\s+to\s+motionly_worker\s*;",
    re.IGNORECASE,
)
CREATED_TABLE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?public\.([a-z0-9_]+)", re.I)
# A later migration may narrow the allowlist again; applied in migration order.
TABLE_REVOKE = re.compile(
    r"revoke\s+([a-z, ]+?)\s+on\s+(?:table\s+)?public\.([a-z0-9_]+)\s+from\s+motionly_worker\s*;",
    re.IGNORECASE,
)

# Never reachable by the worker credential, whatever worker code does.
FORBIDDEN_TABLES = {
    "workspaces",
    "workspace_members",
    "workspace_invitations",
    "workspace_api_keys",
    "profiles",
    "billing_adjustments",
    "gateway_webhook_events",
    "admin_audit_events",
    "password_reset_requests",
    "user_access_suspensions",
}
FORBIDDEN_WRITE_TABLES = {"subscriptions"}
FORBIDDEN_FUNCTIONS = {
    "motionly_transfer_workspace_ownership",
    "motionly_change_member_role",
    "motionly_remove_workspace_member",
    "motionly_leave_workspace",
    "motionly_accept_workspace_invitation",
    "motionly_create_workspace_invitation",
    "motionly_revoke_workspace_invitation",
    "motionly_delete_account_workspaces",
    "motionly_suspend_user_access",
    "motionly_restore_user_access",
    "update_own_profile",
    "handle_new_user",
}


def _migrations_from(version: str):
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name.split("_", 1)[0] >= version:
            yield path


def _granted() -> tuple[dict[str, set[str]], set[str]]:
    tables: dict[str, set[str]] = {}
    functions: set[str] = set()
    for path in _migrations_from(NARROWING_VERSION):
        text = path.read_text(encoding="utf-8")
        for privileges, table in TABLE_GRANT.findall(text):
            tables.setdefault(table, set()).update(
                privilege.strip().upper() for privilege in privileges.split(",")
            )
        for privileges, table in TABLE_REVOKE.findall(text):
            revoked = {privilege.strip().upper() for privilege in privileges.split(",")}
            if "ALL" in revoked or "ALL PRIVILEGES" in revoked:
                revoked = {"SELECT", "INSERT", "UPDATE", "DELETE"}
            remaining = tables.get(table, set()) - revoked
            if remaining:
                tables[table] = remaining
            else:
                tables.pop(table, None)
        functions.update(FUNCTION_GRANT.findall(text))
    return tables, functions


def _existing_tables() -> set[str]:
    return {
        name
        for path in MIGRATIONS.glob("*.sql")
        for name in CREATED_TABLE.findall(path.read_text(encoding="utf-8"))
    }


@pytest.fixture(scope="module")
def usage():
    return worker_database_usage()


def test_every_table_worker_code_uses_is_granted(usage) -> None:
    granted, _ = _granted()
    existing = _existing_tables()
    missing = {
        table: sorted(privileges - granted.get(table, set()))
        for table, privileges in usage.tables.items()
        if table in existing and privileges - granted.get(table, set())
    }
    assert missing == {}, (
        "Worker code uses these tables beyond motionly_worker's grants; add an explicit "
        f"`grant ... on public.<table> to motionly_worker` in a new migration: {missing}"
    )


def test_every_rpc_worker_code_calls_is_granted(usage) -> None:
    _, granted = _granted()
    assert sorted(usage.rpcs - granted) == []


def test_identity_membership_billing_and_credential_objects_stay_out_of_reach() -> None:
    tables, functions = _granted()
    assert sorted(set(tables) & FORBIDDEN_TABLES) == []
    assert {
        table: sorted(privileges - {"SELECT"})
        for table, privileges in tables.items()
        if table in FORBIDDEN_WRITE_TABLES and privileges - {"SELECT"}
    } == {}
    assert sorted(functions & FORBIDDEN_FUNCTIONS) == []


def test_worker_code_does_not_need_identity_or_billing_writes(usage) -> None:
    """If this fails, worker code started touching tenancy/billing state: review it."""
    assert sorted(set(usage.tables) & FORBIDDEN_TABLES) == []
    assert {
        table: sorted(usage.tables[table] - {"SELECT"})
        for table in FORBIDDEN_WRITE_TABLES & set(usage.tables)
        if usage.tables[table] - {"SELECT"}
    } == {}
    assert sorted(usage.rpcs & FORBIDDEN_FUNCTIONS) == []


def test_grants_are_not_much_wider_than_worker_code_needs(usage) -> None:
    """Grants for tables no worker code uses should be removed, not accumulate."""
    granted, functions = _granted()
    unused_tables = sorted(set(granted) - set(usage.tables))
    unused_functions = sorted(functions - usage.rpcs - {"is_workspace_member"})
    assert unused_tables == []
    assert unused_functions == []


def test_reachability_starts_from_every_worker_entry_point(usage) -> None:
    assert ("app/workers/domain_job.py", "_execute") in WORKER_ROOTS
    # Sanity: the analysis sees the core worker writes it must keep.
    for table, privilege in (
        ("render_job_events", "INSERT"),
        ("render_jobs", "UPDATE"),
        ("transcripts", "INSERT"),
        ("caption_segments", "INSERT"),
        ("video_manifests", "INSERT"),
        ("segmentation_mattes", "UPDATE"),
        ("uploaded_videos", "UPDATE"),
    ):
        assert privilege in usage.tables.get(table, set()), (table, privilege)
    for rpc in ("motionly_complete_render", "motionly_begin_workflow_step"):
        assert rpc in usage.rpcs
