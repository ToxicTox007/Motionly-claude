-- Online pre-build for supabase/migrations/202610280203_db_audit_foreign_key_indexes.sql.
--
-- Run before deploying that migration to a database whose tables are large:
--   psql "service=motionly_production" -v ON_ERROR_STOP=1 -f scripts/db/prebuild_foreign_key_indexes.sql
-- Each CREATE INDEX CONCURRENTLY builds without blocking writes (it cannot run
-- inside a transaction, so run this file as plain psql, not through
-- db-migrate). The names match the migration, so the migration's
-- "create index if not exists" then does nothing.
-- If a build is interrupted it leaves an INVALID index; find and drop it, then
-- rerun this file:
--   select indexrelid::regclass from pg_index where not indisvalid;
--   drop index concurrently <name>;

set lock_timeout = '10s';

create index concurrently if not exists ai_edit_feedback_events_user_id_fk_idx on public.ai_edit_feedback_events (user_id);
create index concurrently if not exists ai_edit_proposal_decisions_decided_by_fk_idx on public.ai_edit_proposal_decisions (decided_by) where decided_by is not null;
create index concurrently if not exists ai_edit_proposal_decisions_edit_plan_id_fk_idx on public.ai_edit_proposal_decisions (edit_plan_id) where edit_plan_id is not null;
create index concurrently if not exists ai_edit_proposals_base_active_plan_id_fk_idx on public.ai_edit_proposals (base_active_plan_id) where base_active_plan_id is not null;
create index concurrently if not exists ai_edit_proposals_created_by_fk_idx on public.ai_edit_proposals (created_by) where created_by is not null;
create index concurrently if not exists ai_edit_proposals_transcript_id_fk_idx on public.ai_edit_proposals (transcript_id);
create index concurrently if not exists ai_edit_proposals_upload_id_fk_idx on public.ai_edit_proposals (upload_id);
create index concurrently if not exists ai_routes_adapter_id_fk_idx on public.ai_routes (adapter_id);
create index concurrently if not exists ai_routes_model_ref_fk_idx on public.ai_routes (model_ref) where model_ref is not null;
create index concurrently if not exists asset_collection_items_asset_id_fk_idx on public.asset_collection_items (asset_id);
create index concurrently if not exists broll_slots_project_id_fk_idx on public.broll_slots (project_id);
create index concurrently if not exists broll_slots_selected_asset_id_fk_idx on public.broll_slots (selected_asset_id) where selected_asset_id is not null;
create index concurrently if not exists caption_segments_project_id_fk_idx on public.caption_segments (project_id);
create index concurrently if not exists caption_segments_workspace_id_fk_idx on public.caption_segments (workspace_id);
create index concurrently if not exists caption_tracks_source_track_id_fk_idx on public.caption_tracks (source_track_id) where source_track_id is not null;
create index concurrently if not exists caption_tracks_transcript_id_fk_idx on public.caption_tracks (transcript_id);
create index concurrently if not exists client_review_comment_assignees_assigned_by_fk_idx on public.client_review_comment_assignees (assigned_by) where assigned_by is not null;
create index concurrently if not exists client_review_comment_assignees_review_id_fk_idx on public.client_review_comment_assignees (review_id);
create index concurrently if not exists client_review_comments_author_user_id_fk_idx on public.client_review_comments (author_user_id) where author_user_id is not null;
create index concurrently if not exists client_review_comments_parent_comment_id_fk_idx on public.client_review_comments (parent_comment_id) where parent_comment_id is not null;
create index concurrently if not exists client_review_revisions_created_by_fk_idx on public.client_review_revisions (created_by);
create index concurrently if not exists client_review_revisions_decided_by_fk_idx on public.client_review_revisions (decided_by) where decided_by is not null;
create index concurrently if not exists client_review_revisions_source_object_reference_id_fk_idx on public.client_review_revisions (source_object_reference_id);
create index concurrently if not exists client_review_revisions_uploaded_video_id_fk_idx on public.client_review_revisions (uploaded_video_id);
create index concurrently if not exists client_review_share_links_created_by_fk_idx on public.client_review_share_links (created_by);
create index concurrently if not exists cost_attribution_events_project_id_fk_idx on public.cost_attribution_events (project_id) where project_id is not null;
create index concurrently if not exists cost_attribution_events_render_job_id_fk_idx on public.cost_attribution_events (render_job_id) where render_job_id is not null;
create index concurrently if not exists edit_plan_snapshots_created_by_user_id_fk_idx on public.edit_plan_snapshots (created_by_user_id);
create index concurrently if not exists edit_plan_snapshots_source_plan_id_fk_idx on public.edit_plan_snapshots (source_plan_id);
create index concurrently if not exists edit_plan_snapshots_transcript_id_fk_idx on public.edit_plan_snapshots (transcript_id);
create index concurrently if not exists edit_plan_snapshots_workspace_id_fk_idx on public.edit_plan_snapshots (workspace_id);
create index concurrently if not exists edit_plans_transcript_id_fk_idx on public.edit_plans (transcript_id);
create index concurrently if not exists editorial_feedback_deltas_policy_id_fk_idx on public.editorial_feedback_deltas (policy_id) where policy_id is not null;
create index concurrently if not exists editorial_feedback_deltas_project_id_fk_idx on public.editorial_feedback_deltas (project_id);
create index concurrently if not exists editorial_feedback_deltas_user_id_fk_idx on public.editorial_feedback_deltas (user_id);
create index concurrently if not exists editorial_learning_deployments_active_dataset_id_fk_idx on public.editorial_learning_deployments (active_dataset_id) where active_dataset_id is not null;
create index concurrently if not exists editorial_learning_deployments_previous_dataset_id_fk_idx on public.editorial_learning_deployments (previous_dataset_id) where previous_dataset_id is not null;
create index concurrently if not exists editorial_learning_deployments_project_id_fk_idx on public.editorial_learning_deployments (project_id);
create index concurrently if not exists editorial_learning_deployments_user_id_fk_idx on public.editorial_learning_deployments (user_id);
create index concurrently if not exists editorial_learning_evaluations_dataset_id_fk_idx on public.editorial_learning_evaluations (dataset_id);
create index concurrently if not exists editorial_learning_policies_user_id_fk_idx on public.editorial_learning_policies (user_id);
create index concurrently if not exists editorial_learning_policy_events_policy_id_fk_idx on public.editorial_learning_policy_events (policy_id) where policy_id is not null;
create index concurrently if not exists editorial_training_dataset_items_delta_id_fk_idx on public.editorial_training_dataset_items (delta_id);
create index concurrently if not exists editorial_training_datasets_project_id_fk_idx on public.editorial_training_datasets (project_id);
create index concurrently if not exists editorial_training_datasets_user_id_fk_idx on public.editorial_training_datasets (user_id);
create index concurrently if not exists editorial_training_datasets_workspace_id_fk_idx on public.editorial_training_datasets (workspace_id);
create index concurrently if not exists export_share_links_created_by_fk_idx on public.export_share_links (created_by);
create index concurrently if not exists exports_render_job_id_fk_idx on public.exports (render_job_id);
create index concurrently if not exists exports_workflow_run_id_fk_idx on public.exports (workflow_run_id) where workflow_run_id is not null;
create index concurrently if not exists generic_media_upload_sessions_object_reference_id_fk_idx on public.generic_media_upload_sessions (object_reference_id) where object_reference_id is not null;
create index concurrently if not exists generic_media_upload_sessions_quota_reservation_id_fk_idx on public.generic_media_upload_sessions (quota_reservation_id);
create index concurrently if not exists inference_operations_project_id_fk_idx on public.inference_operations (project_id) where project_id is not null;
create index concurrently if not exists localized_publish_packages_caption_track_id_fk_idx on public.localized_publish_packages (caption_track_id);
create index concurrently if not exists localized_publish_packages_edit_plan_id_fk_idx on public.localized_publish_packages (edit_plan_id) where edit_plan_id is not null;
create index concurrently if not exists localized_publish_packages_source_caption_track_id_fk_idx on public.localized_publish_packages (source_caption_track_id);
create index concurrently if not exists music_tracks_project_id_fk_idx on public.music_tracks (project_id);
create index concurrently if not exists project_model_settings_workspace_id_fk_idx on public.project_model_settings (workspace_id);
create index concurrently if not exists project_music_selections_music_track_id_fk_idx on public.project_music_selections (music_track_id) where music_track_id is not null;
create index concurrently if not exists project_music_selections_project_id_fk_idx on public.project_music_selections (project_id);
create index concurrently if not exists projects_active_video_id_fk_idx on public.projects (active_video_id) where active_video_id is not null;
create index concurrently if not exists projects_created_by_fk_idx on public.projects (created_by);
create index concurrently if not exists provider_slo_events_project_id_fk_idx on public.provider_slo_events (project_id) where project_id is not null;
create index concurrently if not exists provider_slo_events_workspace_id_fk_idx on public.provider_slo_events (workspace_id) where workspace_id is not null;
create index concurrently if not exists render_job_events_project_id_fk_idx on public.render_job_events (project_id);
create index concurrently if not exists render_job_events_workspace_id_fk_idx on public.render_job_events (workspace_id);
create index concurrently if not exists render_jobs_caption_track_id_fk_idx on public.render_jobs (caption_track_id) where caption_track_id is not null;
create index concurrently if not exists render_jobs_edit_plan_id_fk_idx on public.render_jobs (edit_plan_id) where edit_plan_id is not null;
create index concurrently if not exists render_jobs_timeline_revision_id_fk_idx on public.render_jobs (timeline_revision_id) where timeline_revision_id is not null;
create index concurrently if not exists render_jobs_uploaded_video_id_fk_idx on public.render_jobs (uploaded_video_id);
create index concurrently if not exists segmentation_mattes_project_id_fk_idx on public.segmentation_mattes (project_id);
create index concurrently if not exists stock_assets_media_upload_session_id_fk_idx on public.stock_assets (media_upload_session_id) where media_upload_session_id is not null;
create index concurrently if not exists stock_assets_project_id_fk_idx on public.stock_assets (project_id);
create index concurrently if not exists stock_assets_storage_quota_reservation_id_fk_idx on public.stock_assets (storage_quota_reservation_id) where storage_quota_reservation_id is not null;
create index concurrently if not exists tenant_integrity_findings_audit_run_id_fk_idx on public.tenant_integrity_findings (audit_run_id);
create index concurrently if not exists tenant_integrity_remediation_events_finding_id_fk_idx on public.tenant_integrity_remediation_events (finding_id);
create index concurrently if not exists timeline_revisions_changed_by_fk_idx on public.timeline_revisions (changed_by) where changed_by is not null;
create index concurrently if not exists timeline_revisions_decision_id_fk_idx on public.timeline_revisions (decision_id) where decision_id is not null;
create index concurrently if not exists timeline_revisions_proposal_id_fk_idx on public.timeline_revisions (proposal_id) where proposal_id is not null;
create index concurrently if not exists transactional_outbox_workflow_event_id_fk_idx on public.transactional_outbox (workflow_event_id);
create index concurrently if not exists transactional_outbox_workflow_run_id_fk_idx on public.transactional_outbox (workflow_run_id);
create index concurrently if not exists transactional_outbox_workflow_step_id_fk_idx on public.transactional_outbox (workflow_step_id) where workflow_step_id is not null;
create index concurrently if not exists transcript_words_project_id_fk_idx on public.transcript_words (project_id);
create index concurrently if not exists transcript_words_workspace_id_fk_idx on public.transcript_words (workspace_id);
create index concurrently if not exists transcripts_uploaded_video_id_fk_idx on public.transcripts (uploaded_video_id);
create index concurrently if not exists upload_events_project_id_fk_idx on public.upload_events (project_id);
create index concurrently if not exists upload_events_workspace_id_fk_idx on public.upload_events (workspace_id);
create index concurrently if not exists upload_sessions_quota_reservation_id_fk_idx on public.upload_sessions (quota_reservation_id);
create index concurrently if not exists uploaded_videos_source_object_reference_id_fk_idx on public.uploaded_videos (source_object_reference_id) where source_object_reference_id is not null;
create index concurrently if not exists video_manifests_project_id_fk_idx on public.video_manifests (project_id);
create index concurrently if not exists visual_presets_edit_plan_id_fk_idx on public.visual_presets (edit_plan_id) where edit_plan_id is not null;
create index concurrently if not exists visual_presets_project_id_fk_idx on public.visual_presets (project_id);
create index concurrently if not exists workflow_events_workflow_step_id_fk_idx on public.workflow_events (workflow_step_id) where workflow_step_id is not null;
create index concurrently if not exists workflow_run_artifacts_decision_id_fk_idx on public.workflow_run_artifacts (decision_id) where decision_id is not null;
create index concurrently if not exists workflow_run_artifacts_export_id_fk_idx on public.workflow_run_artifacts (export_id) where export_id is not null;
create index concurrently if not exists workflow_run_artifacts_proposal_id_fk_idx on public.workflow_run_artifacts (proposal_id) where proposal_id is not null;
create index concurrently if not exists workflow_run_artifacts_render_job_id_fk_idx on public.workflow_run_artifacts (render_job_id) where render_job_id is not null;
create index concurrently if not exists workflow_run_artifacts_timeline_revision_id_fk_idx on public.workflow_run_artifacts (timeline_revision_id) where timeline_revision_id is not null;
create index concurrently if not exists workflow_run_artifacts_transcript_id_fk_idx on public.workflow_run_artifacts (transcript_id) where transcript_id is not null;
create index concurrently if not exists workflow_run_artifacts_visual_manifest_id_fk_idx on public.workflow_run_artifacts (visual_manifest_id) where visual_manifest_id is not null;
create index concurrently if not exists workflow_run_commands_requested_by_fk_idx on public.workflow_run_commands (requested_by) where requested_by is not null;
create index concurrently if not exists workflow_run_commands_result_workflow_run_id_fk_idx on public.workflow_run_commands (result_workflow_run_id) where result_workflow_run_id is not null;
create index concurrently if not exists workflow_runs_active_plan_id_fk_idx on public.workflow_runs (active_plan_id) where active_plan_id is not null;
create index concurrently if not exists workflow_runs_retry_of_workflow_run_id_fk_idx on public.workflow_runs (retry_of_workflow_run_id) where retry_of_workflow_run_id is not null;
create index concurrently if not exists workflow_runs_root_workflow_run_id_fk_idx on public.workflow_runs (root_workflow_run_id);
create index concurrently if not exists workflow_runs_source_object_reference_id_fk_idx on public.workflow_runs (source_object_reference_id);
create index concurrently if not exists workflow_runs_started_by_fk_idx on public.workflow_runs (started_by) where started_by is not null;
create index concurrently if not exists workflow_runs_uploaded_video_id_fk_idx on public.workflow_runs (uploaded_video_id);
create index concurrently if not exists workflow_step_attempts_workflow_run_id_fk_idx on public.workflow_step_attempts (workflow_run_id);
create index concurrently if not exists workspace_api_keys_created_by_fk_idx on public.workspace_api_keys (created_by);
create index concurrently if not exists workspace_invitations_accepted_by_fk_idx on public.workspace_invitations (accepted_by) where accepted_by is not null;
create index concurrently if not exists workspace_invitations_created_by_fk_idx on public.workspace_invitations (created_by);
create index concurrently if not exists workspace_members_user_id_fk_idx on public.workspace_members (user_id);
create index concurrently if not exists workspaces_owner_user_id_fk_idx on public.workspaces (owner_user_id);
