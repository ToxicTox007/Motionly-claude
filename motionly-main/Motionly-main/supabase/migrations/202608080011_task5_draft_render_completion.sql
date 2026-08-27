-- Task 5 canonical workflow draft rendering and export completion.
-- Forward-only: v1 histories, migrations 008-010, direct render completion, and
-- Task 11 decision semantics remain unchanged.

create extension if not exists pgcrypto;

-- Re-entrant only for local forward-migration validation: if this migration was
-- previously applied, restore the migration-010 stable wrapper before rebuilding
-- the audited chain below. Fresh databases do not enter this branch.
do $$
begin
  if to_regprocedure(
    'public.motionly_complete_workflow_step_task5_proposal_gate_audited(uuid,uuid,uuid,text,uuid,text,jsonb,text,text)'
  ) is not null then
    drop function public.motionly_complete_workflow_step(
      uuid,uuid,uuid,text,uuid,text,jsonb,text,text
    );
    alter function public.motionly_complete_workflow_step_task5_proposal_gate_audited(
      uuid,uuid,uuid,text,uuid,text,jsonb,text,text
    ) rename to motionly_complete_workflow_step;
  end if;
end;
$$;

drop trigger if exists task5_guard_workflow_render_completion
on public.render_jobs;

-- Workflow render lineage must remain pinned to the approved active plan,
-- immutable approval timeline, canonical source upload, and source reference.
create or replace function public.motionly_validate_task5_render_lineage()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
  timeline public.timeline_revisions;
  plan public.edit_plans;
  upload public.uploaded_videos;
begin
  if new.workflow_run_id is null then
    return new;
  end if;
  select * into run
  from public.workflow_runs
  where id = new.workflow_run_id
    and workspace_id = new.workspace_id
    and project_id = new.project_id
  for share;
  if not found
     or run.workflow_type <> 'first_draft'
     or run.workflow_version <> 'first-draft-v2'
     or run.uploaded_video_id <> new.uploaded_video_id
     or run.source_media_revision <> new.source_media_revision
     or run.active_plan_id is distinct from new.edit_plan_id
     or run.active_plan_revision is distinct from new.source_plan_revision
  then
    raise exception 'RENDER_WORKFLOW_RUN_LINEAGE_INVALID' using errcode = 'P0001';
  end if;
  select * into upload
  from public.uploaded_videos
  where id = new.uploaded_video_id
    and workspace_id = new.workspace_id
    and project_id = new.project_id
  for share;
  if not found
     or upload.status <> 'ready'
     or upload.media_revision <> new.source_media_revision
     or upload.source_object_reference_id is distinct from run.source_object_reference_id
     or upload.verified_sha256 is null
  then
    raise exception 'RENDER_WORKFLOW_MEDIA_LINEAGE_INVALID' using errcode = 'P0001';
  end if;
  select * into plan
  from public.edit_plans
  where id = new.edit_plan_id
    and workspace_id = new.workspace_id
    and project_id = new.project_id
  for share;
  if not found
     or not plan.is_active
     or plan.status <> 'ready'
     or plan.revision <> new.source_plan_revision
  then
    raise exception 'RENDER_WORKFLOW_PLAN_LINEAGE_INVALID' using errcode = 'P0001';
  end if;
  select timeline_row.* into timeline
  from public.timeline_revisions timeline_row
  join public.workflow_run_artifacts artifact
    on artifact.workspace_id = timeline_row.workspace_id
   and artifact.project_id = timeline_row.project_id
   and artifact.workflow_run_id = new.workflow_run_id
   and artifact.artifact_type = 'timeline_revision'
   and artifact.timeline_revision_id = timeline_row.id
   and artifact.artifact_revision = timeline_row.revision
  where timeline_row.id = new.timeline_revision_id
    and timeline_row.workspace_id = new.workspace_id
    and timeline_row.project_id = new.project_id;
  if not found
     or timeline.edit_plan_id <> new.edit_plan_id
     or timeline.revision <> new.source_plan_revision
     or timeline.plan_snapshot is distinct from plan.plan
  then
    raise exception 'RENDER_WORKFLOW_TIMELINE_LINEAGE_INVALID' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

revoke all on function public.motionly_validate_task5_render_lineage()
from public, anon, authenticated, service_role;

-- Base exports inherit both workflow and plan lineage from their render. New
-- workflow base exports are legal only while the canonical run is rendering.
create or replace function public.motionly_validate_task5_export_lineage()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  render public.render_jobs;
  run_state text;
begin
  select * into render
  from public.render_jobs
  where id = new.render_job_id
    and workspace_id = new.workspace_id
    and project_id = new.project_id;
  if not found then
    return new;
  end if;
  if new.workflow_run_id is distinct from render.workflow_run_id
     or new.source_plan_revision is distinct from render.source_plan_revision
  then
    raise exception 'EXPORT_WORKFLOW_LINEAGE_INVALID' using errcode = 'P0001';
  end if;
  if new.workflow_run_id is not null and new.restored_from_export_id is null then
    select state into run_state
    from public.workflow_runs
    where id = new.workflow_run_id
      and workspace_id = new.workspace_id
      and project_id = new.project_id;
    if run_state is distinct from 'draft_rendering' then
      raise exception 'WORKFLOW_DRAFT_EXPORT_RUN_NOT_ACTIVE' using errcode = 'P0001';
    end if;
  end if;
  return new;
end;
$$;

revoke all on function public.motionly_validate_task5_export_lineage()
from public, anon, authenticated, service_role;

-- A workflow render cannot report complete after canonical cancellation and it
-- cannot become complete before its one canonical base export exists.
create or replace function public.motionly_guard_workflow_render_completion()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
  base_export_count integer;
begin
  if new.workflow_run_id is null
     or new.status::text not in ('complete', 'succeeded')
     or old.status::text in ('complete', 'succeeded')
  then
    return new;
  end if;
  select * into run
  from public.workflow_runs
  where id = new.workflow_run_id
    and workspace_id = new.workspace_id
    and project_id = new.project_id
  for share;
  if not found or run.state <> 'draft_rendering' then
    raise exception 'WORKFLOW_RENDER_RUN_NOT_ACTIVE' using errcode = 'P0001';
  end if;
  select count(*) into base_export_count
  from public.exports exported
  where exported.workspace_id = new.workspace_id
    and exported.project_id = new.project_id
    and exported.render_job_id = new.id
    and exported.workflow_run_id = new.workflow_run_id
    and exported.source_plan_revision = new.source_plan_revision
    and exported.restored_from_export_id is null;
  if base_export_count <> 1 then
    raise exception 'WORKFLOW_RENDER_EXPORT_MISSING' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger task5_guard_workflow_render_completion
before update of status on public.render_jobs
for each row execute function public.motionly_guard_workflow_render_completion();

revoke all on function public.motionly_guard_workflow_render_completion()
from public, anon, authenticated, service_role;

-- Exactly one canonical render UUID is allocated by PostgreSQL and atomically
-- bound to an assembling run. Lost responses rediscover the same immutable row.
create or replace function public.motionly_ensure_workflow_draft_render(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_uploaded_video_id uuid,
  p_edit_plan_id uuid,
  p_source_plan_revision integer,
  p_timeline_revision_id uuid,
  p_source_media_revision integer,
  p_caption_track_id uuid,
  p_planner_version text,
  p_manifest jsonb,
  p_keep_segments jsonb,
  p_specs jsonb,
  p_template_id text,
  p_render_engine text,
  p_renderer_version text,
  p_props_hash text,
  p_logs jsonb,
  p_progress_pct integer
)
returns setof public.render_jobs
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
  existing public.render_jobs;
  created public.render_jobs;
  timeline public.timeline_revisions;
  plan public.edit_plans;
  proposal public.ai_edit_proposals;
  seq integer;
  event_payload jsonb;
begin
  if p_props_hash is null or p_props_hash !~ '^[0-9a-f]{64}$'
     or p_source_plan_revision is null or p_source_plan_revision < 1
     or p_source_media_revision is null or p_source_media_revision < 1
     or p_render_engine not in ('ffmpeg_basic', 'remotion_premium')
     or jsonb_typeof(p_manifest) <> 'object'
     or jsonb_typeof(p_keep_segments) <> 'array'
     or jsonb_typeof(p_specs) <> 'object'
     or jsonb_typeof(p_logs) <> 'array'
     or p_progress_pct not between 0 and 100
  then
    raise exception 'WORKFLOW_RENDER_PAYLOAD_INVALID' using errcode = '22023';
  end if;
  if p_manifest->>'workflow_run_id' is distinct from p_workflow_run_id::text
     or p_manifest->>'edit_plan_id' is distinct from p_edit_plan_id::text
     or p_manifest->>'timeline_revision_id' is distinct from p_timeline_revision_id::text
     or p_manifest->>'source_plan_revision' is distinct from p_source_plan_revision::text
     or p_manifest->>'source_media_revision' is distinct from p_source_media_revision::text
     or p_manifest->>'props_hash' is distinct from p_props_hash
     or p_manifest->>'render_engine' is distinct from p_render_engine
     or jsonb_typeof(p_manifest->'timeline') <> 'object'
     or p_manifest->'timeline'->>'schema_version' is distinct from '1'
     or coalesce((p_manifest->'timeline'->>'duration_ms')::integer, 0) < 1
     or jsonb_typeof(p_manifest->'timeline'->'clips') <> 'array'
     or jsonb_array_length(p_manifest->'timeline'->'clips') < 1
     or p_manifest->'render_props'->'timeline' is distinct from p_manifest->'timeline'
     or p_manifest->'source'->>'uploaded_video_id' is distinct from p_uploaded_video_id::text
     or p_manifest->'source'->>'reference_id' is null
  then
    raise exception 'WORKFLOW_RENDER_MANIFEST_INVALID' using errcode = 'P0001';
  end if;

  select * into run
  from public.workflow_runs
  where id = p_workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002';
  end if;
  if run.workflow_type <> 'first_draft'
     or run.workflow_version <> 'first-draft-v2'
     or run.uploaded_video_id <> p_uploaded_video_id
     or run.source_media_revision <> p_source_media_revision
     or run.active_plan_id is distinct from p_edit_plan_id
     or run.active_plan_revision is distinct from p_source_plan_revision
  then
    raise exception 'WORKFLOW_RENDER_RUN_LINEAGE_INVALID' using errcode = 'P0001';
  end if;

  select * into existing
  from public.render_jobs
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and workflow_run_id = p_workflow_run_id;
  if found then
    if run.state <> 'draft_rendering'
       or existing.uploaded_video_id <> p_uploaded_video_id
       or existing.edit_plan_id is distinct from p_edit_plan_id
       or existing.source_plan_revision is distinct from p_source_plan_revision
       or existing.timeline_revision_id is distinct from p_timeline_revision_id
       or existing.source_media_revision is distinct from p_source_media_revision
       or existing.props_hash is distinct from p_props_hash
       or existing.render_engine::text is distinct from p_render_engine
       or existing.manifest is distinct from p_manifest
    then
      raise exception 'WORKFLOW_RENDER_IDENTITY_CONFLICT' using errcode = 'P0001';
    end if;
    return next existing;
    return;
  end if;
  if run.state <> 'assembling' then
    raise exception 'WORKFLOW_NOT_ASSEMBLING' using errcode = 'P0001';
  end if;

  select timeline_row.* into timeline
  from public.timeline_revisions timeline_row
  join public.workflow_run_artifacts artifact
    on artifact.workspace_id = timeline_row.workspace_id
   and artifact.project_id = timeline_row.project_id
   and artifact.workflow_run_id = p_workflow_run_id
   and artifact.artifact_type = 'timeline_revision'
   and artifact.timeline_revision_id = timeline_row.id
   and artifact.artifact_revision = timeline_row.revision
  where timeline_row.id = p_timeline_revision_id
    and timeline_row.workspace_id = p_workspace_id
    and timeline_row.project_id = p_project_id;
  if not found
     or timeline.edit_plan_id <> p_edit_plan_id
     or timeline.revision <> p_source_plan_revision
  then
    raise exception 'WORKFLOW_APPROVAL_TIMELINE_INVALID' using errcode = 'P0001';
  end if;
  select * into plan
  from public.edit_plans plan_row
  where plan_row.id = p_edit_plan_id
    and plan_row.workspace_id = p_workspace_id
    and plan_row.project_id = p_project_id;
  if not found
     or timeline.plan_snapshot is distinct from plan.plan
     or not plan.is_active
     or plan.status <> 'ready'
     or plan.revision <> p_source_plan_revision
  then
    raise exception 'WORKFLOW_APPROVAL_PLAN_INVALID' using errcode = 'P0001';
  end if;
  select * into proposal
  from public.ai_edit_proposals proposal_row
  where proposal_row.id = timeline.proposal_id
    and proposal_row.workspace_id = p_workspace_id
    and proposal_row.project_id = p_project_id
    and proposal_row.workflow_run_id = p_workflow_run_id;
  if not found
     or proposal.state not in ('approved', 'partially_approved')
     or not exists (
       select 1 from public.workflow_run_artifacts decision_artifact
       where decision_artifact.workspace_id = p_workspace_id
         and decision_artifact.project_id = p_project_id
         and decision_artifact.workflow_run_id = p_workflow_run_id
         and decision_artifact.artifact_type = 'decision'
         and decision_artifact.decision_id = timeline.decision_id
     )
  then
    raise exception 'WORKFLOW_APPROVAL_DECISION_INVALID' using errcode = 'P0001';
  end if;

  insert into public.render_jobs (
    workspace_id, project_id, uploaded_video_id, caption_track_id,
    workflow_run_id, edit_plan_id, source_plan_revision, timeline_revision_id,
    source_media_revision, status, planner_version, manifest, keep_segments,
    specs, template_id, render_engine, renderer_version, props_hash, logs,
    progress_pct
  ) values (
    p_workspace_id, p_project_id, p_uploaded_video_id, p_caption_track_id,
    p_workflow_run_id, p_edit_plan_id, p_source_plan_revision,
    p_timeline_revision_id, p_source_media_revision, 'queued',
    p_planner_version, p_manifest, p_keep_segments, p_specs,
    p_template_id::public.render_template_id,
    p_render_engine::public.render_engine, p_renderer_version, p_props_hash,
    p_logs, p_progress_pct
  ) returning * into created;

  insert into public.render_job_events (
    workspace_id, project_id, render_job_id, event_type, to_status,
    progress_pct, message, metadata
  ) values (
    p_workspace_id, p_project_id, created.id, 'queued', 'queued',
    p_progress_pct, 'Workflow draft render queued.',
    jsonb_build_object('workflow_run_id', p_workflow_run_id)
  );

  update public.workflow_runs
  set state = 'draft_rendering',
      current_step_key = 'render-first-draft-v2',
      revision = revision + 1,
      completed_at = null
  where id = run.id
  returning * into run;
  select coalesce(max(sequence_number), 0) + 1 into seq
  from public.workflow_events where workflow_run_id = run.id;
  event_payload := jsonb_build_object(
    'workflow_run_id', run.id,
    'render_job_id', created.id,
    'edit_plan_id', created.edit_plan_id,
    'source_plan_revision', created.source_plan_revision,
    'timeline_revision_id', created.timeline_revision_id,
    'source_media_revision', created.source_media_revision,
    'state', run.state
  );
  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, sequence_number,
    event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, run.id, seq,
    'workflow.draft_render.ensured',
    'draft-render-ensured:' || run.id || ':' || created.id,
    event_payload
  );
  return next created;
end;
$$;

revoke all on function public.motionly_ensure_workflow_draft_render(
  uuid,uuid,uuid,uuid,uuid,integer,uuid,integer,uuid,text,jsonb,jsonb,jsonb,
  text,text,text,text,jsonb,integer
) from public, anon, authenticated;
grant execute on function public.motionly_ensure_workflow_draft_render(
  uuid,uuid,uuid,uuid,uuid,integer,uuid,integer,uuid,text,jsonb,jsonb,jsonb,
  text,text,text,text,jsonb,integer
) to service_role;

-- Preserve the race-safe base export boundary while carrying immutable plan
-- lineage as well as the workflow run identifier.
create or replace function public.motionly_upsert_base_export(
  p_workspace_id uuid,
  p_project_id uuid,
  p_render_job_id uuid,
  p_storage_bucket text,
  p_storage_path text,
  p_format text,
  p_width integer,
  p_height integer,
  p_fps integer,
  p_duration_ms integer,
  p_file_size_bytes bigint,
  p_thumbnail_url text,
  p_settings_summary jsonb
)
returns setof public.exports
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  render public.render_jobs;
  exported public.exports;
  next_version integer;
begin
  perform pg_advisory_xact_lock(hashtextextended(
    p_workspace_id || ':' || p_project_id || ':base-export', 0
  ));
  select * into render from public.render_jobs
  where id = p_render_job_id and workspace_id = p_workspace_id and project_id = p_project_id
  for share;
  if not found then raise exception 'RENDER_NOT_FOUND' using errcode = 'P0002'; end if;
  select * into exported from public.exports
  where workspace_id = p_workspace_id and project_id = p_project_id
    and render_job_id = p_render_job_id and restored_from_export_id is null
  for update;
  if found then
    if exported.workflow_run_id is distinct from render.workflow_run_id
       or exported.source_plan_revision is distinct from render.source_plan_revision
    then
      raise exception 'EXPORT_WORKFLOW_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    update public.exports set
      storage_bucket = p_storage_bucket,
      storage_path = p_storage_path,
      format = p_format::public.export_format,
      width = p_width,
      height = p_height,
      fps = p_fps,
      duration_ms = p_duration_ms,
      file_size_bytes = p_file_size_bytes,
      thumbnail_url = p_thumbnail_url,
      settings_summary = coalesce(p_settings_summary, '{}'::jsonb)
    where id = exported.id returning * into exported;
    return next exported;
    return;
  end if;
  select coalesce(max(version), 0) + 1 into next_version
  from public.exports where workspace_id = p_workspace_id and project_id = p_project_id;
  insert into public.exports (
    workspace_id, project_id, render_job_id, workflow_run_id,
    source_plan_revision, version, storage_bucket, storage_path, format,
    width, height, fps, duration_ms, file_size_bytes, thumbnail_url,
    settings_summary
  ) values (
    p_workspace_id, p_project_id, p_render_job_id, render.workflow_run_id,
    render.source_plan_revision, next_version, p_storage_bucket, p_storage_path,
    p_format::public.export_format, p_width, p_height, p_fps, p_duration_ms,
    p_file_size_bytes, p_thumbnail_url, coalesce(p_settings_summary, '{}'::jsonb)
  ) returning * into exported;
  return next exported;
end;
$$;

revoke all on function public.motionly_upsert_base_export(
  uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb
) from public, anon, authenticated;
grant execute on function public.motionly_upsert_base_export(
  uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb
) to service_role;

-- Only the non-restored base export is the canonical workflow artifact.
create or replace function public.motionly_capture_task5_artifact()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run_id uuid;
  artifact_type text;
  artifact_id uuid;
  artifact_revision integer;
begin
  if tg_table_name = 'ai_edit_proposal_decisions' then
    select workflow_run_id into run_id from public.ai_edit_proposals
    where id = new.proposal_id and workspace_id = new.workspace_id and project_id = new.project_id;
    artifact_type := 'decision'; artifact_id := new.id;
  elsif tg_table_name = 'timeline_revisions' then
    select workflow_run_id into run_id from public.ai_edit_proposals
    where id = new.proposal_id and workspace_id = new.workspace_id and project_id = new.project_id;
    artifact_type := 'timeline_revision'; artifact_id := new.id; artifact_revision := new.revision;
  elsif tg_table_name = 'render_jobs' then
    run_id := new.workflow_run_id; artifact_type := 'draft_render_job'; artifact_id := new.id;
    artifact_revision := new.source_plan_revision;
  elsif tg_table_name = 'exports' then
    if new.restored_from_export_id is not null then
      return new;
    end if;
    run_id := new.workflow_run_id; artifact_type := 'draft_export'; artifact_id := new.id;
    artifact_revision := new.version;
  end if;
  if run_id is not null then
    perform * from public.motionly_link_workflow_artifact(
      new.workspace_id, new.project_id, run_id,
      artifact_type, artifact_id, artifact_revision
    );
  end if;
  return new;
end;
$$;

revoke all on function public.motionly_capture_task5_artifact()
from public, anon, authenticated, service_role;

-- Extend the stable completion wrapper narrowly. The migration-010 wrapper is
-- retained as an audited body and is not directly executable.
alter function public.motionly_complete_workflow_step(
  uuid,uuid,uuid,text,uuid,text,jsonb,text,text
) rename to motionly_complete_workflow_step_task5_proposal_gate_audited;

revoke all on function public.motionly_complete_workflow_step_task5_proposal_gate_audited(
  uuid,uuid,uuid,text,uuid,text,jsonb,text,text
) from public, anon, authenticated, service_role;

create or replace function public.motionly_complete_workflow_step(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_step_key text,
  p_claim_token uuid,
  p_output_hash text,
  p_output jsonb,
  p_next_run_state text default null,
  p_next_event_type text default 'workflow.step.completed'
)
returns setof jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
  step public.workflow_steps;
  render public.render_jobs;
  timeline_artifact public.workflow_run_artifacts;
  exported public.exports;
  export_count integer;
begin
  select * into step
  from public.workflow_steps
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and workflow_run_id = p_workflow_run_id
    and step_key = p_step_key;

  if found and step.step_kind = 'render_draft' then
    select * into run
    from public.workflow_runs
    where id = p_workflow_run_id
      and workspace_id = p_workspace_id
      and project_id = p_project_id
    for update;
    if not found
       or run.state not in ('draft_rendering', 'draft_ready')
       or p_next_run_state is distinct from 'draft_ready'
       or p_next_event_type is distinct from 'workflow.step.completed'
       or jsonb_typeof(p_output) <> 'object'
       or p_output - array['render_job_id','render_status','failed'] <> '{}'::jsonb
       or p_output->>'render_job_id' is distinct from step.resource_id::text
       or p_output->>'render_status' is distinct from 'complete'
       or p_output->'failed' is distinct from 'false'::jsonb
    then
      raise exception 'WORKFLOW_DRAFT_RENDER_RESULT_INVALID' using errcode = 'P0001';
    end if;
    select * into render
    from public.render_jobs
    where id = step.resource_id
      and workspace_id = p_workspace_id
      and project_id = p_project_id
      and workflow_run_id = p_workflow_run_id
    for share;
    select * into timeline_artifact
    from public.workflow_run_artifacts
    where workspace_id = p_workspace_id
      and project_id = p_project_id
      and workflow_run_id = p_workflow_run_id
      and artifact_type = 'timeline_revision';
    if not found
       or render.status::text not in ('complete', 'succeeded')
       or render.uploaded_video_id <> run.uploaded_video_id
       or render.edit_plan_id is distinct from run.active_plan_id
       or render.source_plan_revision is distinct from run.active_plan_revision
       or render.source_media_revision <> run.source_media_revision
       or timeline_artifact.timeline_revision_id is distinct from render.timeline_revision_id
       or timeline_artifact.artifact_revision is distinct from render.source_plan_revision
    then
      raise exception 'WORKFLOW_DRAFT_RENDER_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    select count(*) into export_count
    from public.exports export_row
    where export_row.workspace_id = p_workspace_id
      and export_row.project_id = p_project_id
      and export_row.render_job_id = render.id
      and export_row.workflow_run_id = p_workflow_run_id
      and export_row.source_plan_revision = render.source_plan_revision
      and export_row.restored_from_export_id is null;
    if export_count <> 1 then
      raise exception 'WORKFLOW_DRAFT_EXPORT_MISSING' using errcode = 'P0001';
    end if;
    select * into exported
    from public.exports export_row
    where export_row.workspace_id = p_workspace_id
      and export_row.project_id = p_project_id
      and export_row.render_job_id = render.id
      and export_row.workflow_run_id = p_workflow_run_id
      and export_row.source_plan_revision = render.source_plan_revision
      and export_row.restored_from_export_id is null
    limit 1;
    perform * from public.motionly_link_workflow_artifact(
      p_workspace_id, p_project_id, p_workflow_run_id,
      'draft_export', exported.id, exported.version
    );
  end if;

  return query
  select * from public.motionly_complete_workflow_step_task5_proposal_gate_audited(
    p_workspace_id,
    p_project_id,
    p_workflow_run_id,
    p_step_key,
    p_claim_token,
    p_output_hash,
    p_output,
    p_next_run_state,
    p_next_event_type
  );
end;
$$;

revoke all on function public.motionly_complete_workflow_step(
  uuid,uuid,uuid,text,uuid,text,jsonb,text,text
) from public, anon, authenticated;
grant execute on function public.motionly_complete_workflow_step(
  uuid,uuid,uuid,text,uuid,text,jsonb,text,text
) to service_role;
