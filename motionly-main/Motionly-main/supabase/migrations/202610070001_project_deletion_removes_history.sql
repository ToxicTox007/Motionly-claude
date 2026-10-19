-- Task 6: deleting a project (and deleting an account, which deletes the
-- account's workspaces) removes the project's workflow and proposal history.
--
-- Before this migration a project could not be deleted once it had any
-- history: the append-only triggers rejected the cascaded deletes, and
-- ON DELETE RESTRICT foreign keys between rows of the same project stopped
-- the cascade before it reached them. Deleting an account failed for the same
-- reasons, and because projects.created_by restricted the user row.
--
-- History stays append-only otherwise: rows can never be updated, and can only
-- be deleted once the project they belong to is gone (so only by a project,
-- workspace or account delete). Every foreign key keeps checking immediately;
-- only a project or account delete defers the checks inside the rows it
-- removes, to the end of that transaction, where they still run.
--
-- Constraints are listed explicitly (not discovered at run time): an
-- unexpected schema must stop the release.

begin;

-- 1. Append-only history: deletable only after its project row is gone.
create or replace function public.motionly_reject_immutable_mutation()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if tg_op = 'DELETE'
     and not exists (select 1 from public.projects where id = old.project_id) then
    return old;
  end if;
  raise exception 'IMMUTABLE_RECORD: % rows are append-only', tg_table_name
    using errcode = '55000';
end;
$$;

create or replace function public.motionly_protect_workflow_attempt()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if tg_op = 'DELETE' then
    if not exists (select 1 from public.projects where id = old.project_id) then
      return old;
    end if;
    raise exception 'IMMUTABLE_RECORD: workflow_step_attempts rows cannot be deleted'
      using errcode = '55000';
  end if;
  if old.state <> 'active' then
    raise exception 'IMMUTABLE_RECORD: terminal workflow attempts cannot change'
      using errcode = '55000';
  end if;
  if new.workspace_id <> old.workspace_id
     or new.project_id <> old.project_id
     or new.workflow_run_id <> old.workflow_run_id
     or new.workflow_step_id <> old.workflow_step_id
     or new.attempt_number <> old.attempt_number
     or new.attempt_key <> old.attempt_key
     or new.claim_token <> old.claim_token
     or new.lease_owner <> old.lease_owner
     or new.started_at <> old.started_at
  then
    raise exception 'IMMUTABLE_IDENTITY: workflow attempt identity cannot change'
      using errcode = '55000';
  end if;
  return new;
end;
$$;

-- 2. History tables that no project cascade reached now follow their parent.
alter table public.workflow_run_commands
  drop constraint task5_workflow_command_run_fk,
  add constraint task5_workflow_command_run_fk
    foreign key (workspace_id, project_id, workflow_run_id)
    references public.workflow_runs (workspace_id, project_id, id) on delete cascade;
alter table public.ai_edit_proposal_actions
  drop constraint task11_action_proposal_fk,
  add constraint task11_action_proposal_fk
    foreign key (workspace_id, project_id, proposal_id)
    references public.ai_edit_proposals (workspace_id, project_id, id) on delete cascade;
alter table public.ai_edit_proposal_decisions
  drop constraint task11_decision_proposal_fk,
  add constraint task11_decision_proposal_fk
    foreign key (workspace_id, project_id, proposal_id)
    references public.ai_edit_proposals (workspace_id, project_id, id) on delete cascade;

-- 3. Foreign keys between rows that a project delete removes together. They
-- were ON DELETE RESTRICT (checked immediately, cannot be deferred); NO ACTION
-- DEFERRABLE INITIALLY IMMEDIATE keeps immediate checking for every other
-- statement and lets a project delete check them once it has finished.
alter table public.ai_edit_proposal_decisions
  drop constraint task11_decision_plan_fk,
  add constraint task11_decision_plan_fk
    foreign key (workspace_id, project_id, edit_plan_id) references public.edit_plans (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.ai_edit_proposals
  drop constraint task11_proposal_base_plan_fk,
  add constraint task11_proposal_base_plan_fk
    foreign key (workspace_id, project_id, base_active_plan_id) references public.edit_plans (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task11_proposal_transcript_fk,
  add constraint task11_proposal_transcript_fk
    foreign key (workspace_id, project_id, transcript_id) references public.transcripts (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task11_proposal_upload_fk,
  add constraint task11_proposal_upload_fk
    foreign key (workspace_id, project_id, upload_id) references public.uploaded_videos (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task5_proposal_workflow_run_fk,
  add constraint task5_proposal_workflow_run_fk
    foreign key (workspace_id, project_id, workflow_run_id) references public.workflow_runs (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.client_review_revisions
  drop constraint client_review_revisions_plan_tenant_fk,
  add constraint client_review_revisions_plan_tenant_fk
    foreign key (workspace_id, project_id, edit_plan_id) references public.edit_plans (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint client_review_revisions_reference_tenant_fk,
  add constraint client_review_revisions_reference_tenant_fk
    foreign key (workspace_id, project_id, source_object_reference_id) references public.object_references (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint client_review_revisions_upload_tenant_fk,
  add constraint client_review_revisions_upload_tenant_fk
    foreign key (workspace_id, project_id, uploaded_video_id) references public.uploaded_videos (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.editorial_training_dataset_items
  drop constraint editorial_training_dataset_items_delta_id_fkey,
  add constraint editorial_training_dataset_items_delta_id_fkey
    foreign key (delta_id) references public.editorial_feedback_deltas (id)
    deferrable initially immediate;

alter table public.exports alter constraint exports_render_job_id_fkey deferrable initially immediate;

alter table public.exports
  drop constraint task5_export_workflow_run_fk,
  add constraint task5_export_workflow_run_fk
    foreign key (workspace_id, project_id, workflow_run_id) references public.workflow_runs (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.generic_media_upload_sessions
  drop constraint generic_media_sessions_reference_tenant_fk,
  add constraint generic_media_sessions_reference_tenant_fk
    foreign key (workspace_id, project_id, object_reference_id) references public.object_references (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.localized_publish_packages
  drop constraint localized_publish_packages_source_track_tenant_fk,
  add constraint localized_publish_packages_source_track_tenant_fk
    foreign key (workspace_id, project_id, source_caption_track_id) references public.caption_tracks (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint localized_publish_packages_track_tenant_fk,
  add constraint localized_publish_packages_track_tenant_fk
    foreign key (workspace_id, project_id, caption_track_id) references public.caption_tracks (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.render_jobs
  drop constraint task5_render_plan_fk,
  add constraint task5_render_plan_fk
    foreign key (workspace_id, project_id, edit_plan_id) references public.edit_plans (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task5_render_timeline_revision_fk,
  add constraint task5_render_timeline_revision_fk
    foreign key (workspace_id, project_id, timeline_revision_id) references public.timeline_revisions (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task5_render_workflow_run_fk,
  add constraint task5_render_workflow_run_fk
    foreign key (workspace_id, project_id, workflow_run_id) references public.workflow_runs (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.stock_assets
  drop constraint stock_assets_media_reference_tenant_fk,
  add constraint stock_assets_media_reference_tenant_fk
    foreign key (workspace_id, project_id, object_reference_id) references public.object_references (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint stock_assets_media_session_tenant_fk,
  add constraint stock_assets_media_session_tenant_fk
    foreign key (workspace_id, project_id, media_upload_session_id) references public.generic_media_upload_sessions (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.timeline_revisions
  drop constraint task11_timeline_decision_fk,
  add constraint task11_timeline_decision_fk
    foreign key (workspace_id, project_id, decision_id) references public.ai_edit_proposal_decisions (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task11_timeline_proposal_fk,
  add constraint task11_timeline_proposal_fk
    foreign key (workspace_id, project_id, proposal_id) references public.ai_edit_proposals (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.transactional_outbox
  drop constraint task7_outbox_event_fk,
  add constraint task7_outbox_event_fk
    foreign key (workspace_id, project_id, workflow_event_id) references public.workflow_events (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task7_outbox_same_run_event_fk,
  add constraint task7_outbox_same_run_event_fk
    foreign key (workspace_id, project_id, workflow_run_id, workflow_event_id) references public.workflow_events (workspace_id, project_id, workflow_run_id, id)
    deferrable initially immediate,
  drop constraint task7_outbox_same_run_step_fk,
  add constraint task7_outbox_same_run_step_fk
    foreign key (workspace_id, project_id, workflow_run_id, workflow_step_id) references public.workflow_steps (workspace_id, project_id, workflow_run_id, id)
    deferrable initially immediate,
  drop constraint task7_outbox_step_fk,
  add constraint task7_outbox_step_fk
    foreign key (workspace_id, project_id, workflow_step_id) references public.workflow_steps (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.workflow_events
  drop constraint task7_workflow_events_same_run_step_fk,
  add constraint task7_workflow_events_same_run_step_fk
    foreign key (workspace_id, project_id, workflow_run_id, workflow_step_id) references public.workflow_steps (workspace_id, project_id, workflow_run_id, id)
    deferrable initially immediate,
  drop constraint task7_workflow_events_step_fk,
  add constraint task7_workflow_events_step_fk
    foreign key (workspace_id, project_id, workflow_step_id) references public.workflow_steps (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.workflow_run_artifacts
  drop constraint task5_workflow_artifact_decision_fk,
  add constraint task5_workflow_artifact_decision_fk
    foreign key (workspace_id, project_id, decision_id) references public.ai_edit_proposal_decisions (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task5_workflow_artifact_export_fk,
  add constraint task5_workflow_artifact_export_fk
    foreign key (workspace_id, project_id, export_id) references public.exports (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task5_workflow_artifact_proposal_fk,
  add constraint task5_workflow_artifact_proposal_fk
    foreign key (workspace_id, project_id, proposal_id) references public.ai_edit_proposals (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task5_workflow_artifact_render_fk,
  add constraint task5_workflow_artifact_render_fk
    foreign key (workspace_id, project_id, render_job_id) references public.render_jobs (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task5_workflow_artifact_timeline_fk,
  add constraint task5_workflow_artifact_timeline_fk
    foreign key (workspace_id, project_id, timeline_revision_id) references public.timeline_revisions (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task5_workflow_artifact_transcript_fk,
  add constraint task5_workflow_artifact_transcript_fk
    foreign key (workspace_id, project_id, transcript_id) references public.transcripts (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task5_workflow_artifact_visual_fk,
  add constraint task5_workflow_artifact_visual_fk
    foreign key (workspace_id, project_id, visual_manifest_id) references public.video_manifests (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.workflow_run_commands
  drop constraint task5_workflow_command_result_fk,
  add constraint task5_workflow_command_result_fk
    foreign key (workspace_id, project_id, result_workflow_run_id) references public.workflow_runs (workspace_id, project_id, id)
    deferrable initially immediate;

alter table public.workflow_runs
  drop constraint task5_workflow_retry_parent_fk,
  add constraint task5_workflow_retry_parent_fk
    foreign key (workspace_id, project_id, retry_of_workflow_run_id) references public.workflow_runs (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task5_workflow_root_fk,
  add constraint task5_workflow_root_fk
    foreign key (workspace_id, project_id, root_workflow_run_id) references public.workflow_runs (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task7_workflow_runs_plan_fk,
  add constraint task7_workflow_runs_plan_fk
    foreign key (workspace_id, project_id, active_plan_id) references public.edit_plans (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task7_workflow_runs_reference_fk,
  add constraint task7_workflow_runs_reference_fk
    foreign key (workspace_id, project_id, source_object_reference_id) references public.object_references (workspace_id, project_id, id)
    deferrable initially immediate,
  drop constraint task7_workflow_runs_upload_fk,
  add constraint task7_workflow_runs_upload_fk
    foreign key (workspace_id, project_id, uploaded_video_id) references public.uploaded_videos (workspace_id, project_id, id)
    deferrable initially immediate;

-- projects.created_by restricted the user row, so an account with projects
-- could not be deleted. Same treatment: still checked at commit.
alter table public.projects
  drop constraint projects_created_by_fkey,
  add constraint projects_created_by_fkey
    foreign key (created_by) references auth.users (id)
    deferrable initially immediate;

-- 4. A project or account delete defers the checks above to the end of its
-- transaction. SET CONSTRAINTS ALL only affects deferrable constraints, and
-- these are the only deferrable ones.
create function public.motionly_defer_cascade_checks()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  set constraints all deferred;
  return old;
end;
$$;
revoke all on function public.motionly_defer_cascade_checks() from public;

create trigger motionly_project_delete_defers_cascade_checks
before delete on public.projects
for each row execute function public.motionly_defer_cascade_checks();

-- An account's workspaces (and so its projects and history) are deleted
-- before the account row, so the audit tables' user columns are never nulled
-- while their rows still exist (that would be an update of append-only rows).
create function public.motionly_delete_account_workspaces()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  set constraints all deferred;
  delete from public.workspaces where owner_user_id = old.id;
  return old;
end;
$$;
revoke all on function public.motionly_delete_account_workspaces() from public;

create trigger motionly_account_delete_removes_workspaces
before delete on auth.users
for each row execute function public.motionly_delete_account_workspaces();

commit;
