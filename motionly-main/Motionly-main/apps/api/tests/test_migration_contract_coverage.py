"""Ensure high-risk migration changes retain executable pgTAP coverage."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"
SQL_TESTS = ROOT / "supabase" / "tests"

MIGRATION_TEST_CONTRACTS = {
    "202610220101_remote_ai_feature_quota_categories.sql": (
        "remote_ai_feature_quota_categories_test.sql",
        (
            "cost_attribution_events_category_check",
            "vision_object_images",
            "vision_caption_images",
            "embedding_inputs",
            "diarization_seconds",
            "emotion_seconds",
            "stem_seconds",
            "generated_seconds",
        ),
    ),
    "202610200301_task18_worker_row_access.sql": (
        "task_18_worker_row_access_test.sql",
        ("motionly_worker", "password_reset_requests", "workspace_api_keys"),
    ),
    "202610191101_task25_realtime_channel_access.sql": (
        "task_25_realtime_channel_access_test.sql",
        (
            "realtime.messages",
            "motionly_can_access_topic",
            "motionly_members_join_private_topics",
            "motionly_members_broadcast_private_topics",
            "is_workspace_member",
        ),
    ),
    "202610191301_task27_music_catalog_import.sql": (
        "task_27_music_catalog_import_test.sql",
        (
            "source_page_url",
            "composition_rights_status",
            "recording_rights_status",
            "is_test_track",
            "music-catalog",
            "service_role",
        ),
    ),
    "202610191401_task28_music_mood_synonyms.sql": (
        "task_28_music_mood_synonyms_test.sql",
        (
            "music_mood_synonyms",
            "mood_synonyms",
            "compute_mood_synonyms",
            "anthemic",
        ),
    ),
    "202610200101_task32_retire_first_draft_v1.sql": (
        "task_32_retire_first_draft_v1_test.sql",
        (
            "motionly_refuse_retired_workflow_version",
            "task32_workflow_runs_refuse_retired_version",
            "WORKFLOW_VERSION_RETIRED",
            "first-draft-v2",
        ),
    ),
    "202608090001_music_rights_evidence.sql": (
        "task_22_music_rights_evidence_test.sql",
        ("licensed_music_tracks", "rights_snapshot", "service_role"),
    ),
    "202608090009_client_review_collaboration.sql": (
        "task_19_client_review_collaboration_test.sql",
        ("client_review_revisions", "client_review_comments", "service_role"),
    ),
    "202610190301_export_share_links.sql": (
        "task_16_export_share_links_test.sql",
        ("export_share_links", "token_digest", "service_role"),
    ),
    "202608090010_localized_publishing_asset_library.sql": (
        "task_20_localized_publishing_asset_library_test.sql",
        ("localized_publish_packages", "asset_library_items", "service_role"),
    ),
    "202610060002_drop_removed_features.sql": (
        "task_4_removed_features_test.sql",
        ("publish_jobs", "performance_feedback_metrics", "tenant_integrity_constraint_manifest"),
    ),
    "202610070001_project_deletion_removes_history.sql": (
        "task_26_project_deletion_test.sql",
        (
            "IMMUTABLE_RECORD",
            "workflow_step_attempts",
            "timeline_revisions",
            "ai_edit_proposal_decisions",
            "auth.users",
        ),
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
    "202610190001_task12_password_reset_requests.sql": (
        "task_12_password_reset_requests_test.sql",
        (
            "password_reset_requests",
            "admin_audit_events",
            "service_role",
        ),
    ),
    "202610190201_task15_workflow_step_retry_reenqueue.sql": (
        "task_15_workflow_step_retry_test.sql",
        (
            "motionly_prepare_workflow_step",
            "WORKFLOW_STEP_TRANSPORT_CONFLICT",
            "bullmq_job_id",
        ),
    ),
    "202610190203_task15_durable_workflow_cancellation.sql": (
        "task_15_project_delete_cancels_workflow_test.sql",
        (
            "workflow_cancellation_requests",
            "motionly_cancel_project_workflows_on_delete",
            "motionly_claim_workflow_cancellations",
            "temporal_workflow_id",
            "project_deleted",
        ),
    ),
    "202610190401_drop_legacy_upload_completion_functions.sql": (
        "task_17_resumable_upload_path_test.sql",
        (
            "motionly_claim_legacy_upload",
            "motionly_finalize_legacy_upload",
            "motionly_cancel_legacy_upload",
            "motionly_initialize_upload",
        ),
    ),
    "202610190801_task22_mock_gateway_schema.sql": (
        "task_22_mock_gateway_schema_test.sql",
        (
            "mock_gateway",
            "mock_gateway_role",
            "card_last4",
        ),
    ),
    "202610190802_task22_public_function_execute_isolation.sql": (
        "task_22_public_function_execute_isolation_test.sql",
        (
            "revoke execute on function",
            "mock_gateway_role",
            "has_function_privilege",
        ),
    ),
    "202610190901_task23_billing_gateway_rename.sql": (
        "task_23_billing_gateway_rename_test.sql",
        (
            "gateway_customer_id",
            "gateway_webhook_events",
            "amount_paise",
        ),
    ),
    "202610190501_task18_restricted_worker_role.sql": (
        "task_18_restricted_worker_role_test.sql",
        (
            "motionly_worker",
            "authenticator",
            "motionly_worker_bucket_scope",
        ),
    ),
    "202610191001_task24_workspace_api_keys.sql": (
        "task_24_workspace_api_keys_test.sql",
        (
            "workspace_api_keys",
            "fingerprint",
            "service_role",
        ),
    ),
    "202610190701_task20_workspace_invitations.sql": (
        "task_20_workspace_invitations_test.sql",
        (
            "workspace_invitations",
            "token_digest",
            "motionly_create_workspace_invitation",
            "motionly_accept_workspace_invitation",
            "SEAT_LIMIT_REACHED",
            "LAST_OWNER_PROTECTED",
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
