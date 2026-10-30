"""Static schema validation - verify migrations, RLS policies, and immutability."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

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


GUARD = ROOT / "scripts" / "check_forward_migrations.sh"
REPAIRS = ROOT / "scripts" / "migration-repairs.sha256"
PINNED_REPAIRS = {
    "supabase/migrations/202608090009_client_review_collaboration.sql",
    "supabase/migrations/202608090010_localized_publishing_asset_library.sql",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _guard_project(tmp_path: Path, *, subdirectory: str = "") -> tuple[Path, Path]:
    """A git repo whose base branch has two migrations; returns (git root, project)."""
    git_root = tmp_path / "repo"
    project = git_root / subdirectory if subdirectory else git_root
    (project / "scripts").mkdir(parents=True)
    (project / "supabase" / "migrations").mkdir(parents=True)
    shutil.copy(GUARD, project / "scripts" / GUARD.name)
    (project / "supabase/migrations/202601010001_first.sql").write_text(
        "create table a (id int);\n"
    )
    (project / "supabase/migrations/202601010002_second.sql").write_text(
        "create table b (id int);\n"
    )
    repaired = "create table b (id int primary key);\n"
    digest = hashlib.sha256(repaired.encode()).hexdigest()
    (project / "scripts/migration-repairs.sha256").write_text(
        f"# pinned repair\n{digest}  supabase/migrations/202601010002_second.sql\n"
    )
    _git(git_root, "init", "-q", "-b", "main")
    _git(git_root, "add", "-A")
    _git(git_root, "commit", "-q", "-m", "base")
    _git(git_root, "branch", "base")
    return git_root, project


def _commit_and_guard(git_root: Path, project: Path) -> subprocess.CompletedProcess[str]:
    _git(git_root, "add", "-A")
    _git(git_root, "commit", "-q", "--allow-empty", "-m", "change")
    return subprocess.run(
        ["bash", "scripts/check_forward_migrations.sh", "base"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )


def test_forward_migration_guard_allows_new_migrations(tmp_path: Path) -> None:
    git_root, project = _guard_project(tmp_path)
    (project / "supabase/migrations/202601010003_third.sql").write_text(
        "create table c (id int);\n"
    )

    result = _commit_and_guard(git_root, project)

    assert result.returncode == 0, result.stderr
    assert "forward-only" in result.stdout


def test_forward_migration_guard_rejects_new_migrations_ordered_before_the_base(
    tmp_path: Path,
) -> None:
    # migrate.sh and the Supabase CLI refuse a pending migration older than the
    # latest applied one, so the guard catches it before the release does.
    git_root, project = _guard_project(tmp_path)
    (project / "supabase/migrations/202601010001_late.sql").write_text("select 1;\n")

    result = _commit_and_guard(git_root, project)

    assert result.returncode == 1
    assert "202601010001_late.sql" in result.stderr
    assert "not after 202601010002" in result.stderr


def test_forward_migration_guard_rejects_names_migrate_sh_would_refuse(tmp_path: Path) -> None:
    git_root, project = _guard_project(tmp_path)
    (project / "supabase/migrations/20260102_Short-Name.sql").write_text("select 1;\n")

    result = _commit_and_guard(git_root, project)

    assert result.returncode == 1
    assert "invalid name" in result.stderr
    assert "20260102_Short-Name.sql" in result.stderr


def test_forward_migration_guard_rejects_edits_renames_and_deletions(tmp_path: Path) -> None:
    git_root, project = _guard_project(tmp_path / "edit")
    (project / "supabase/migrations/202601010001_first.sql").write_text(
        "create table a (id bigint);\n"
    )
    edited = _commit_and_guard(git_root, project)

    git_root, project = _guard_project(tmp_path / "rename")
    _git(
        git_root,
        "mv",
        "supabase/migrations/202601010001_first.sql",
        "supabase/migrations/202601010001_moved.sql",
    )
    renamed = _commit_and_guard(git_root, project)

    git_root, project = _guard_project(tmp_path / "delete")
    (project / "supabase/migrations/202601010001_first.sql").unlink()
    deleted = _commit_and_guard(git_root, project)

    for result in (edited, renamed, deleted):
        assert result.returncode == 1
        assert "202601010001_first.sql" in result.stderr
        assert "add a forward-fix migration" in result.stderr
    assert "R100" in renamed.stderr


def test_forward_migration_guard_allows_only_the_pinned_repair_content(tmp_path: Path) -> None:
    git_root, project = _guard_project(tmp_path)
    (project / "supabase/migrations/202601010002_second.sql").write_text(
        "create table b (id int primary key);\n"
    )
    pinned = _commit_and_guard(git_root, project)

    (project / "supabase/migrations/202601010002_second.sql").write_text(
        "create table b (id int primary key, note text);\n"
    )
    changed_again = _commit_and_guard(git_root, project)

    assert pinned.returncode == 0, pinned.stderr
    assert changed_again.returncode == 1
    assert "202601010002_second.sql" in changed_again.stderr


def test_forward_migration_guard_handles_a_project_below_the_git_root(tmp_path: Path) -> None:
    git_root, project = _guard_project(tmp_path, subdirectory="nested/project")
    (project / "supabase/migrations/202601010002_second.sql").write_text(
        "create table b (id int primary key);\n"
    )
    pinned = _commit_and_guard(git_root, project)

    (project / "supabase/migrations/202601010001_first.sql").write_text(
        "create table a (id bigint);\n"
    )
    edited = _commit_and_guard(git_root, project)

    assert pinned.returncode == 0, pinned.stderr
    assert edited.returncode == 1
    assert "supabase/migrations/202601010001_first.sql" in edited.stderr


def test_forward_migration_guard_fails_when_it_cannot_compare(tmp_path: Path) -> None:
    git_root, project = _guard_project(tmp_path)

    result = subprocess.run(
        ["bash", "scripts/check_forward_migrations.sh", "missing-ref"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )

    # A single-commit repository has neither the base ref nor HEAD^.
    assert result.returncode == 1
    assert "cannot compare" in result.stderr.lower()


def test_pinned_migration_repairs_match_the_files() -> None:
    entries = {}
    for line in REPAIRS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        digest, path = line.split()
        entries[path] = digest

    assert set(entries) == PINNED_REPAIRS
    for path, digest in entries.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, path


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
        assert f"alter table public.{table} enable row level security" in content, (
            f"RLS not enabled for {table}"
        )


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


def test_no_migration_reordering_detected() -> None:
    """Verify migrations maintain chronological order based on filename timestamps."""
    migration_files = sorted(MIGRATIONS.glob("*.sql"))

    timestamps = [int(f.name[:12]) for f in migration_files]

    # Check for strict ascending order
    for i in range(len(timestamps) - 1):
        assert timestamps[i] < timestamps[i + 1], (
            f"Migration order violation: {migration_files[i].name} >= {migration_files[i + 1].name}"
        )


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
