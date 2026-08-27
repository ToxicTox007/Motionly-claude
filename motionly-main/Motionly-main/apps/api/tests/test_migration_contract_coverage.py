"""Ensure high-risk migration changes retain executable pgTAP coverage."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"
SQL_TESTS = ROOT / "supabase" / "tests"

MIGRATION_TEST_CONTRACTS = {
    "202608090001_music_rights_evidence.sql": (
        "task_22_music_rights_evidence_test.sql",
        ("licensed_music_tracks", "rights_snapshot", "service_role"),
    ),
    "202608090009_client_review_collaboration.sql": (
        "task_19_client_review_collaboration_test.sql",
        ("client_review_revisions", "client_review_comments", "service_role"),
    ),
    "202608090010_localized_publishing_asset_library.sql": (
        "task_20_localized_publishing_asset_library_test.sql",
        ("localized_publish_packages", "asset_library_items", "service_role"),
    ),
    "202608090011_performance_feedback_loop.sql": (
        "task_21_performance_feedback_test.sql",
        ("performance_feedback_policies", "motionly_capture_performance_metric", "service_role"),
    ),
    "202608110001_asset_library_durable_references.sql": (
        "task_23_asset_library_durable_references_test.sql",
        (
            "asset_library_items",
            "motionly_backfill_asset_library_references",
            "service_role",
        ),
    ),
    "202608110003_render_completion_cas.sql": (
        "task_25_render_completion_cas_test.sql",
        (
            "motionly_complete_render",
            "RENDER_TERMINAL_STATE",
            "RENDER_WORKFLOW_LINEAGE_INVALID",
            "service_role",
        ),
    ),
}


def test_high_risk_migrations_have_executable_pgtap_contracts() -> None:
    for migration_name, (test_name, required_assertions) in MIGRATION_TEST_CONTRACTS.items():
        migration = (MIGRATIONS / migration_name).read_text()
        sql_test = (SQL_TESTS / test_name).read_text()

        assert migration
        assert "select plan(" in sql_test.lower()
        assert "select * from finish();" in sql_test.lower()
        assert "rollback;" in sql_test.lower()
        for assertion in required_assertions:
            assert assertion in migration
            assert assertion in sql_test
