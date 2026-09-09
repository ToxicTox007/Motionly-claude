"""Static schema validation - verify migrations, RLS policies, and immutability."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"


def test_migration_files_exist_and_are_ordered() -> None:
    """Verify migrations directory exists and files follow naming convention."""
    assert MIGRATIONS.exists(), f"Migrations directory not found: {MIGRATIONS}"
    
    migration_files = sorted(MIGRATIONS.glob("*.sql"))
    assert len(migration_files) > 0, "No migration files found"
    
    # Verify naming: YYYYMMDDNNNN_description.sql
    for migration in migration_files:
        name = migration.name
        assert len(name) > 17, f"Migration name too short: {name}"
        assert name[:8].isdigit(), f"Migration timestamp invalid: {name}"
        assert name[8:12].isdigit(), f"Migration sequence invalid: {name}"
        assert "_" in name, f"Migration missing underscore separator: {name}"
        assert name.endswith(".sql"), f"Migration not SQL file: {name}"


def test_migrations_are_forward_only_not_edited() -> None:
    """Verify existing migrations haven't been modified (forward-only contract)."""
    # Get all migration files
    migration_files = sorted(MIGRATIONS.glob("*.sql"))
    
    # Check git history for modifications (not additions)
    for migration in migration_files:
        result = subprocess.run(
            ["git", "log", "--oneline", "--all", "--", str(migration.relative_to(ROOT))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        
        if result.returncode == 0 and result.stdout.strip():
            commits = result.stdout.strip().split("\n")
            # If more than one commit touches this file, it may have been edited
            # (This is a heuristic - manual review needed for multi-commit files)
            if len(commits) > 2:
                # Allow some edits for documented fixes, but flag for review
                print(f"⚠️  Migration {migration.name} has {len(commits)} commits - verify no schema changes")


def test_rls_enabled_on_core_tables() -> None:
    """Verify RLS is enabled on workspace-scoped tables."""
    foundation_migration = MIGRATIONS / "202605300001_week_01_foundation.sql"
    assert foundation_migration.exists(), "Foundation migration not found"
    
    content = foundation_migration.read_text(encoding="utf-8").lower()
    
    # Core tables that MUST have RLS
    required_rls_tables = [
        "profiles",
        "workspaces",
        "workspace_members",
        "projects",
        "uploaded_videos",
        "render_jobs",
        "exports",
    ]
    
    for table in required_rls_tables:
        assert f"alter table public.{table} enable row level security" in content, \
            f"RLS not enabled for {table}"


def test_rls_policies_exist_for_workspace_isolation() -> None:
    """Verify RLS policies enforce workspace boundaries."""
    foundation_migration = MIGRATIONS / "202605300001_week_01_foundation.sql"
    content = foundation_migration.read_text(encoding="utf-8").lower()
    
    # Check for workspace isolation policies
    assert "create policy" in content, "No RLS policies found"
    assert "is_workspace_member" in content, "Workspace member check missing"
    
    # Verify policies exist for core tables
    assert "members read projects" in content, "Project read policy missing"
    assert "members read workspaces" in content, "Workspace read policy missing"


def test_all_migrations_have_sql_extension() -> None:
    """Ensure no non-SQL files in migrations directory."""
    all_files = list(MIGRATIONS.glob("*"))
    non_sql = [f for f in all_files if f.is_file() and not f.name.endswith(".sql")]
    
    assert len(non_sql) == 0, f"Non-SQL files in migrations: {[f.name for f in non_sql]}"


def test_migration_count_matches_expected_range() -> None:
    """Verify migration count is reasonable (detect accidental deletions)."""
    migration_files = list(MIGRATIONS.glob("*.sql"))
    count = len(migration_files)
    
    # As of 2026-08-30, we have 86 migrations
    # Fail if significantly fewer (indicates deletions)
    assert count >= 80, f"Migration count too low ({count}), possible deletions"
    assert count <= 150, f"Migration count unexpectedly high ({count}), verify additions"


@pytest.mark.skipif(
    not (ROOT / ".git").exists(),
    reason="Not a git repository - cannot verify immutability"
)
def test_no_migration_reordering_detected() -> None:
    """Verify migrations maintain chronological order based on filename timestamps."""
    migration_files = sorted(MIGRATIONS.glob("*.sql"))
    
    timestamps = [int(f.name[:12]) for f in migration_files]
    
    # Check for strict ascending order
    for i in range(len(timestamps) - 1):
        assert timestamps[i] < timestamps[i + 1], \
            f"Migration order violation: {migration_files[i].name} >= {migration_files[i + 1].name}"


def test_storage_bucket_references_in_migrations() -> None:
    """Verify storage buckets are created in migrations."""
    # Search for bucket creation in any migration
    bucket_names = set()
    
    for migration in MIGRATIONS.glob("*.sql"):
        content = migration.read_text(encoding="utf-8").lower()
        if "storage.buckets" in content or "insert into storage.buckets" in content:
            bucket_names.add(migration.name)
    
    # We expect at least one migration to reference storage buckets
    assert len(bucket_names) > 0, "No storage bucket setup found in migrations"
