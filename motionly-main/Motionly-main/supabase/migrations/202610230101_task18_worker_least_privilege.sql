-- Task 18 follow-up: motionly_worker gets least privilege.
--
-- 202610190501 granted the worker role select/insert/update/delete on every
-- table in public and EXECUTE on every function (now and, through default
-- privileges, in the future), and 202610200301 gave it a `using (true)` policy
-- on every RLS table but four. SECURITY DEFINER functions bypass RLS, so a
-- leaked or misused worker credential could call
-- motionly_transfer_workspace_ownership / motionly_change_member_role /
-- motionly_accept_workspace_invitation for any workspace, and write
-- workspace_members, workspaces and subscriptions directly.
--
-- The worker now gets exactly what worker code uses. The allowlist below is
-- derived from the code (apps/api/tests/worker_db_reachability.py: the Node
-- worker plus every Python function reachable from the app.workers entry
-- points) and apps/api/tests/test_worker_db_grants.py fails if worker code
-- starts using something this role is not granted. A future job table or RPC
-- needs an explicit grant to motionly_worker in its own migration.
begin;

-- 1. Nothing is granted automatically any more.
alter default privileges in schema public
  revoke select, insert, update, delete on tables from motionly_worker;
alter default privileges in schema public
  revoke usage, select on sequences from motionly_worker;
alter default privileges in schema public
  revoke execute on functions from motionly_worker;
-- PostgreSQL also grants EXECUTE on every new function to PUBLIC, i.e. to every
-- role including motionly_worker (202610190802 revoked it only from the
-- functions that existed then). That is a global default, which a per-schema
-- ALTER DEFAULT PRIVILEGES cannot remove, so revoke it globally for functions
-- the migration role creates. Supabase's own per-schema defaults still grant
-- EXECUTE in `public` to anon/authenticated/service_role; a function in any
-- other schema needs an explicit grant to the roles that call it.
alter default privileges revoke execute on functions from public;

-- 2. Tables: start from nothing; grant the job tables workers read and write.
-- Identity, membership, billing and credential tables (workspaces,
-- workspace_members, workspace_invitations, workspace_api_keys, profiles,
-- billing_adjustments, gateway_webhook_events, admin_audit_events,
-- password_reset_requests, ...) are deliberately absent.
revoke all on all tables in schema public from motionly_worker;
grant select, insert on public.ai_edit_feedback_events to motionly_worker;
grant select on public.ai_edit_proposal_actions to motionly_worker;
grant select on public.ai_edit_proposals to motionly_worker;
grant select, insert on public.artifact_versions to motionly_worker;
grant select, insert, update on public.broll_slots to motionly_worker;
grant select, insert on public.caption_segments to motionly_worker;
grant select, insert on public.caption_tracks to motionly_worker;
grant select, insert on public.cost_attribution_events to motionly_worker;
grant select on public.edit_plans to motionly_worker;
grant select on public.editorial_feedback_deltas to motionly_worker;
grant select on public.editorial_learning_deployments to motionly_worker;
grant select on public.editorial_learning_policies to motionly_worker;
grant select on public.editorial_training_dataset_items to motionly_worker;
grant select on public.editorial_training_datasets to motionly_worker;
grant select, insert on public.exports to motionly_worker;
grant select on public.generic_media_upload_session_parts to motionly_worker;
grant select on public.generic_media_upload_sessions to motionly_worker;
grant select on public.licensed_music_tracks to motionly_worker;
grant select, insert, update on public.music_tracks to motionly_worker;
grant select on public.object_references to motionly_worker;
grant select, delete on public.phase5_resources to motionly_worker;
grant select, insert on public.project_control_settings to motionly_worker;
grant select, insert on public.project_music_selections to motionly_worker;
grant select, update on public.projects to motionly_worker;
grant select, insert on public.provider_slo_events to motionly_worker;
grant select, insert on public.render_job_events to motionly_worker;
grant select, update on public.render_jobs to motionly_worker;
grant select, insert, update on public.segmentation_mattes to motionly_worker;
grant select, insert, update on public.stock_assets to motionly_worker;
grant select on public.subscriptions to motionly_worker;
grant select, insert, delete on public.transcript_words to motionly_worker;
grant select, insert, update on public.transcripts to motionly_worker;
grant select on public.upload_session_parts to motionly_worker;
grant select on public.upload_sessions to motionly_worker;
grant select, update on public.uploaded_videos to motionly_worker;
grant select, insert on public.usage_ledger to motionly_worker;
grant select, insert, update on public.video_manifests to motionly_worker;
grant select, insert on public.visual_presets to motionly_worker;
grant select on public.worker_heartbeats to motionly_worker;
grant select on public.workflow_events to motionly_worker;
grant select on public.workflow_run_artifacts to motionly_worker;
grant select on public.workflow_runs to motionly_worker;
grant select on public.workflow_steps to motionly_worker;
grant select on public.workspace_storage_quotas to motionly_worker;

-- 3. Row policies: keep the worker policy only where the worker has a grant.
do $$
declare
  target record;
begin
  for target in
    select c.relname
    from pg_policy p
    join pg_class c on c.oid = p.polrelid
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and p.polname = 'motionly_worker_jobs'
      and not (
        has_table_privilege('motionly_worker', c.oid, 'SELECT')
        or has_table_privilege('motionly_worker', c.oid, 'INSERT')
        or has_table_privilege('motionly_worker', c.oid, 'UPDATE')
        or has_table_privilege('motionly_worker', c.oid, 'DELETE')
      )
  loop
    execute format('drop policy motionly_worker_jobs on public.%I', target.relname);
  end loop;
end
$$;

-- 4. Functions. SECURITY DEFINER functions run as their owner and bypass RLS,
-- so the worker may execute only the job RPCs it calls, plus
-- is_workspace_member: the member-read policies on workflow tables the worker
-- reads reference it (it answers false for the worker, which has no auth.uid()).
-- SECURITY INVOKER functions run with the caller's own privileges (triggers
-- such as licensed_music_tracks_sync_synonyms call them), so they stay
-- executable: they cannot do more than the worker already may.
revoke execute on all functions in schema public from motionly_worker;
grant execute on function public.motionly_begin_workflow_step to motionly_worker;
grant execute on function public.motionly_capture_editorial_feedback_delta to motionly_worker;
grant execute on function public.motionly_commit_storage_quota to motionly_worker;
grant execute on function public.motionly_complete_render to motionly_worker;
grant execute on function public.motionly_complete_workflow_step to motionly_worker;
grant execute on function public.motionly_create_active_edit_plan to motionly_worker;
grant execute on function public.motionly_create_ai_edit_proposal to motionly_worker;
grant execute on function public.motionly_fail_workflow_step to motionly_worker;
grant execute on function public.motionly_prepare_workflow_step to motionly_worker;
grant execute on function public.motionly_reserve_storage_quota to motionly_worker;
grant execute on function public.motionly_transition_ai_edit_proposal to motionly_worker;
grant execute on function public.motionly_upsert_base_export to motionly_worker;
grant execute on function public.is_workspace_member to motionly_worker;
do $$
declare
  fn record;
begin
  for fn in
    select p.oid::regprocedure as signature
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.prokind = 'f' and not p.prosecdef
  loop
    execute format('grant execute on function %s to motionly_worker', fn.signature);
  end loop;
end
$$;

commit;
