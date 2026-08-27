-- Task 5 backend foundation: typed workflow lineage, customer commands, and
-- upload-to-first-draft-v2 recovery. Forward-only; first-draft-v1 rows and
-- Temporal history contracts are not rewritten.

create extension if not exists pgcrypto;

-- Multipart sessions are the v2-owned upload path. Legacy/direct uploads keep a
-- NULL workflow_version and retain their compatibility preprocessing behavior.
alter table public.uploaded_videos
  add column workflow_version text,
  add constraint task5_upload_workflow_version_check check (
    workflow_version is null or workflow_version = 'first-draft-v2'
  );

alter table public.upload_sessions
  add column workflow_version text not null default 'first-draft-v2',
  add constraint task5_upload_session_workflow_version_check check (
    workflow_version = 'first-draft-v2'
  );

update public.uploaded_videos upload
set workflow_version = session.workflow_version
from public.upload_sessions session
where session.uploaded_video_id = upload.id
  and session.workspace_id = upload.workspace_id
  and session.project_id = upload.project_id
  and upload.workflow_version is null;

create or replace function public.motionly_mark_multipart_workflow_owner()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  update public.uploaded_videos
  set workflow_version = new.workflow_version
  where id = new.uploaded_video_id
    and workspace_id = new.workspace_id
    and project_id = new.project_id
    and workflow_version is null;
  if not found then
    perform 1
    from public.uploaded_videos
    where id = new.uploaded_video_id
      and workspace_id = new.workspace_id
      and project_id = new.project_id
      and workflow_version = new.workflow_version;
    if not found then
      raise exception 'UPLOAD_WORKFLOW_OWNERSHIP_CONFLICT' using errcode = 'P0001';
    end if;
  end if;
  return new;
end;
$$;

create trigger task5_upload_sessions_mark_workflow_owner
after insert or update of workflow_version on public.upload_sessions
for each row execute function public.motionly_mark_multipart_workflow_owner();

-- Retry lineage is immutable and each run belongs to one root attempt chain.
alter table public.workflow_runs
  add column retry_of_workflow_run_id uuid,
  add column root_workflow_run_id uuid;

update public.workflow_runs set root_workflow_run_id = id
where root_workflow_run_id is null;

alter table public.workflow_runs
  alter column root_workflow_run_id set not null,
  add constraint task5_workflow_retry_parent_fk foreign key (
    workspace_id, project_id, retry_of_workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete restrict,
  add constraint task5_workflow_root_fk foreign key (
    workspace_id, project_id, root_workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete restrict,
  add constraint task5_workflow_retry_shape check (
    (retry_of_workflow_run_id is null and root_workflow_run_id = id)
    or retry_of_workflow_run_id is not null
  );

create or replace function public.motionly_set_workflow_root()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if new.root_workflow_run_id is null then
    new.root_workflow_run_id := new.id;
  end if;
  return new;
end;
$$;

create trigger task5_workflow_runs_set_root
before insert on public.workflow_runs
for each row execute function public.motionly_set_workflow_root();

create or replace function public.motionly_protect_workflow_retry_lineage()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if new.retry_of_workflow_run_id is distinct from old.retry_of_workflow_run_id
     or new.root_workflow_run_id is distinct from old.root_workflow_run_id
  then
    raise exception 'IMMUTABLE_IDENTITY: workflow retry lineage cannot change'
      using errcode = '55000';
  end if;
  return new;
end;
$$;

create trigger task5_workflow_runs_protect_retry_lineage
before update on public.workflow_runs
for each row execute function public.motionly_protect_workflow_retry_lineage();

-- Proposal/run is explicit and at most one proposal may belong to a run.
alter table public.ai_edit_proposals
  add column workflow_run_id uuid,
  add constraint task5_proposal_workflow_run_fk foreign key (
    workspace_id, project_id, workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete restrict;

create unique index task5_one_proposal_per_workflow_run_idx
  on public.ai_edit_proposals (workspace_id, project_id, workflow_run_id)
  where workflow_run_id is not null;

-- Workflow renders pin the canonical run, immutable plan/timeline revision, and
-- upload media revision. Direct compatibility renders may leave these nullable.
alter table public.render_jobs
  add column workflow_run_id uuid,
  add column edit_plan_id uuid,
  add column timeline_revision_id uuid,
  add constraint task5_render_plan_revision_positive check (
    source_plan_revision is null or source_plan_revision > 0
  ),
  add constraint task5_render_media_revision_positive check (
    source_media_revision is null or source_media_revision > 0
  ),
  add constraint task5_render_workflow_run_fk foreign key (
    workspace_id, project_id, workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete restrict,
  add constraint task5_render_plan_fk foreign key (
    workspace_id, project_id, edit_plan_id
  ) references public.edit_plans (workspace_id, project_id, id) on delete restrict,
  add constraint task5_render_timeline_revision_fk foreign key (
    workspace_id, project_id, timeline_revision_id
  ) references public.timeline_revisions (workspace_id, project_id, id) on delete restrict,
  add constraint task5_render_workflow_lineage_shape check (
    workflow_run_id is null
    or (
      edit_plan_id is not null
      and source_plan_revision is not null
      and timeline_revision_id is not null
      and source_media_revision is not null
    )
  );

create unique index task5_one_draft_render_per_workflow_run_idx
  on public.render_jobs (workspace_id, project_id, workflow_run_id)
  where workflow_run_id is not null;

alter table public.exports add column workflow_run_id uuid;

alter table public.exports
  add constraint task5_export_workflow_run_fk foreign key (
    workspace_id, project_id, workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete restrict;

-- The partial unique index is the final race barrier. Restored versions are
-- intentionally excluded and remain append-only historical versions.
create unique index task5_one_base_export_per_render_idx
  on public.exports (workspace_id, project_id, render_job_id)
  where restored_from_export_id is null;

create or replace function public.motionly_validate_task5_render_lineage()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
  timeline public.timeline_revisions;
begin
  if new.workflow_run_id is null then
    return new;
  end if;
  select * into run
  from public.workflow_runs
  where id = new.workflow_run_id
    and workspace_id = new.workspace_id
    and project_id = new.project_id;
  if not found
     or run.uploaded_video_id <> new.uploaded_video_id
     or run.source_media_revision <> new.source_media_revision
  then
    raise exception 'RENDER_WORKFLOW_MEDIA_LINEAGE_INVALID' using errcode = 'P0001';
  end if;
  select * into timeline
  from public.timeline_revisions
  where id = new.timeline_revision_id
    and workspace_id = new.workspace_id
    and project_id = new.project_id;
  if not found
     or timeline.edit_plan_id <> new.edit_plan_id
     or timeline.revision <> new.source_plan_revision
  then
    raise exception 'RENDER_WORKFLOW_PLAN_LINEAGE_INVALID' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger task5_render_jobs_validate_lineage
before insert or update of workflow_run_id, uploaded_video_id, source_media_revision,
  edit_plan_id, source_plan_revision, timeline_revision_id
on public.render_jobs
for each row execute function public.motionly_validate_task5_render_lineage();

create or replace function public.motionly_validate_task5_export_lineage()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  render_run_id uuid;
begin
  select workflow_run_id into render_run_id
  from public.render_jobs
  where id = new.render_job_id
    and workspace_id = new.workspace_id
    and project_id = new.project_id;
  if not found then
    raise exception 'EXPORT_RENDER_NOT_FOUND' using errcode = 'P0002';
  end if;
  if new.workflow_run_id is distinct from render_run_id then
    raise exception 'EXPORT_WORKFLOW_LINEAGE_INVALID' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger task5_exports_validate_lineage
before insert or update of workflow_run_id, render_job_id
on public.exports
for each row execute function public.motionly_validate_task5_export_lineage();

-- A normalized typed artifact relation gives every first-draft artifact a
-- composite tenant FK without polymorphic/unverified identifiers.
alter table public.video_manifests
  add constraint task5_video_manifests_workspace_project_id_key
  unique (workspace_id, project_id, id);

create table public.workflow_run_artifacts (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  workflow_run_id uuid not null,
  artifact_type text not null check (artifact_type in (
    'transcript', 'visual_manifest', 'proposal', 'decision',
    'timeline_revision', 'draft_render_job', 'draft_export'
  )),
  transcript_id uuid,
  visual_manifest_id uuid,
  proposal_id uuid,
  decision_id uuid,
  timeline_revision_id uuid,
  render_job_id uuid,
  export_id uuid,
  artifact_revision integer check (artifact_revision is null or artifact_revision > 0),
  created_at timestamptz not null default now(),
  unique (workspace_id, project_id, id),
  unique (workspace_id, project_id, workflow_run_id, artifact_type),
  constraint task5_workflow_artifact_exact_type check (
    num_nonnulls(
      transcript_id, visual_manifest_id, proposal_id, decision_id,
      timeline_revision_id, render_job_id, export_id
    ) = 1
    and case artifact_type
      when 'transcript' then transcript_id is not null
      when 'visual_manifest' then visual_manifest_id is not null
      when 'proposal' then proposal_id is not null
      when 'decision' then decision_id is not null
      when 'timeline_revision' then timeline_revision_id is not null
      when 'draft_render_job' then render_job_id is not null
      when 'draft_export' then export_id is not null
      else false
    end
  ),
  constraint task5_workflow_artifact_run_fk foreign key (
    workspace_id, project_id, workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete cascade,
  constraint task5_workflow_artifact_transcript_fk foreign key (
    workspace_id, project_id, transcript_id
  ) references public.transcripts (workspace_id, project_id, id) on delete restrict,
  constraint task5_workflow_artifact_visual_fk foreign key (
    workspace_id, project_id, visual_manifest_id
  ) references public.video_manifests (workspace_id, project_id, id) on delete restrict,
  constraint task5_workflow_artifact_proposal_fk foreign key (
    workspace_id, project_id, proposal_id
  ) references public.ai_edit_proposals (workspace_id, project_id, id) on delete restrict,
  constraint task5_workflow_artifact_decision_fk foreign key (
    workspace_id, project_id, decision_id
  ) references public.ai_edit_proposal_decisions (workspace_id, project_id, id) on delete restrict,
  constraint task5_workflow_artifact_timeline_fk foreign key (
    workspace_id, project_id, timeline_revision_id
  ) references public.timeline_revisions (workspace_id, project_id, id) on delete restrict,
  constraint task5_workflow_artifact_render_fk foreign key (
    workspace_id, project_id, render_job_id
  ) references public.render_jobs (workspace_id, project_id, id) on delete restrict,
  constraint task5_workflow_artifact_export_fk foreign key (
    workspace_id, project_id, export_id
  ) references public.exports (workspace_id, project_id, id) on delete restrict
);

create index task5_workflow_artifacts_run_idx
  on public.workflow_run_artifacts (workspace_id, project_id, workflow_run_id, created_at);

create trigger task5_workflow_artifacts_append_only
before update or delete on public.workflow_run_artifacts
for each row execute function public.motionly_reject_immutable_mutation();

alter table public.workflow_run_artifacts enable row level security;
create policy task5_members_read_workflow_artifacts on public.workflow_run_artifacts
for select using (public.is_workspace_member(workspace_id));
revoke all on public.workflow_run_artifacts from public, anon, authenticated;
grant select on public.workflow_run_artifacts to authenticated;
grant all on public.workflow_run_artifacts to service_role;

create or replace function public.motionly_link_workflow_artifact(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_artifact_type text,
  p_artifact_id uuid,
  p_artifact_revision integer default null
)
returns setof public.workflow_run_artifacts
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
  existing public.workflow_run_artifacts;
  linked public.workflow_run_artifacts;
  proposal_run_id uuid;
  related_proposal_id uuid;
  related_render_id uuid;
  artifact_upload_id uuid;
  event_row public.workflow_events;
  seq integer;
  payload jsonb;
begin
  select * into run from public.workflow_runs
  where id = p_workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002';
  end if;

  select * into existing from public.workflow_run_artifacts
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and workflow_run_id = p_workflow_run_id
    and artifact_type = p_artifact_type;
  if found then
    if p_artifact_id is distinct from coalesce(
      existing.transcript_id, existing.visual_manifest_id, existing.proposal_id,
      existing.decision_id, existing.timeline_revision_id,
      existing.render_job_id, existing.export_id
    ) or existing.artifact_revision is distinct from p_artifact_revision then
      raise exception 'WORKFLOW_ARTIFACT_CONFLICT' using errcode = 'P0001';
    end if;
    return next existing;
    return;
  end if;

  if run.state in ('export_ready', 'failed', 'cancelled', 'superseded') then
    raise exception 'WORKFLOW_TERMINAL' using errcode = 'P0001';
  end if;

  if p_artifact_type = 'transcript' then
    select uploaded_video_id into artifact_upload_id from public.transcripts
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or artifact_upload_id <> run.uploaded_video_id then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      transcript_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'visual_manifest' then
    select uploaded_video_id into artifact_upload_id from public.video_manifests
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or artifact_upload_id <> run.uploaded_video_id then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      visual_manifest_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'proposal' then
    select workflow_run_id into proposal_run_id from public.ai_edit_proposals
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id
    for update;
    if not found or proposal_run_id is not null and proposal_run_id <> p_workflow_run_id then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    update public.ai_edit_proposals set workflow_run_id = p_workflow_run_id
    where id = p_artifact_id;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      proposal_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'decision' then
    select proposal_id into related_proposal_id from public.ai_edit_proposal_decisions
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or not exists (
      select 1 from public.ai_edit_proposals
      where id = related_proposal_id and workflow_run_id = p_workflow_run_id
        and workspace_id = p_workspace_id and project_id = p_project_id
    ) then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      decision_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'timeline_revision' then
    select proposal_id into related_proposal_id from public.timeline_revisions
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or not exists (
      select 1 from public.ai_edit_proposals
      where id = related_proposal_id and workflow_run_id = p_workflow_run_id
        and workspace_id = p_workspace_id and project_id = p_project_id
    ) then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      timeline_revision_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'draft_render_job' then
    select workflow_run_id into proposal_run_id from public.render_jobs
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or proposal_run_id is distinct from p_workflow_run_id then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      render_job_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'draft_export' then
    select render_job_id into related_render_id from public.exports
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or not exists (
      select 1 from public.render_jobs
      where id = related_render_id and workflow_run_id = p_workflow_run_id
        and workspace_id = p_workspace_id and project_id = p_project_id
    ) then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      export_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  else
    raise exception 'WORKFLOW_ARTIFACT_TYPE_INVALID' using errcode = '22023';
  end if;

  update public.workflow_runs set revision = revision + 1 where id = run.id;
  select coalesce(max(sequence_number), 0) + 1 into seq
  from public.workflow_events where workflow_run_id = run.id;
  payload := jsonb_build_object(
    'workflow_run_id', run.id,
    'artifact_type', p_artifact_type,
    'artifact_id', p_artifact_id,
    'artifact_revision', p_artifact_revision
  );
  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, sequence_number,
    event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, run.id, seq,
    'workflow.artifact.linked',
    'artifact-linked:' || run.id || ':' || p_artifact_type,
    payload
  ) returning * into event_row;
  return next linked;
end;
$$;

-- Persist the deterministic BullMQ and resource identities before publication.
create or replace function public.motionly_prepare_workflow_step(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_step_key text,
  p_step_kind text,
  p_input_hash text,
  p_max_attempts integer,
  p_bullmq_job_id text,
  p_resource_id uuid default null
)
returns setof public.workflow_steps
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
  step public.workflow_steps;
begin
  if p_bullmq_job_id is null or length(p_bullmq_job_id) not between 8 and 240 then
    raise exception 'WORKFLOW_BULLMQ_ID_INVALID' using errcode = '22023';
  end if;
  select * into run from public.workflow_runs
  where id = p_workflow_run_id and workspace_id = p_workspace_id and project_id = p_project_id
  for update;
  if not found then raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002'; end if;
  if run.state in ('export_ready', 'failed', 'cancelled', 'superseded') then
    raise exception 'WORKFLOW_TERMINAL' using errcode = 'P0001';
  end if;

  insert into public.workflow_steps (
    workspace_id, project_id, workflow_run_id, step_key, step_kind,
    input_hash, max_attempts, bullmq_job_id, resource_id
  ) values (
    p_workspace_id, p_project_id, p_workflow_run_id, p_step_key, p_step_kind,
    p_input_hash, p_max_attempts, p_bullmq_job_id, p_resource_id
  ) on conflict (workspace_id, project_id, workflow_run_id, step_key) do nothing;

  select * into step from public.workflow_steps
  where workflow_run_id = p_workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
    and step_key = p_step_key
  for update;
  if step.step_kind is distinct from p_step_kind
     or step.input_hash is distinct from p_input_hash
     or step.max_attempts is distinct from p_max_attempts
     or step.bullmq_job_id is distinct from p_bullmq_job_id
     or step.resource_id is distinct from p_resource_id
  then
    raise exception 'WORKFLOW_STEP_TRANSPORT_CONFLICT' using errcode = 'P0001';
  end if;
  return next step;
end;
$$;

-- Immutable command ledger provides idempotency evidence independent of mutable
-- run projections.
create table public.workflow_run_commands (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  workflow_run_id uuid not null,
  command_type text not null check (command_type in ('cancel', 'retry')),
  idempotency_key text not null check (length(idempotency_key) between 8 and 128),
  request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
  expected_revision integer not null check (expected_revision > 0),
  result_workflow_run_id uuid,
  requested_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (workspace_id, project_id, workflow_run_id, command_type, idempotency_key),
  unique (workspace_id, project_id, id),
  constraint task5_workflow_command_run_fk foreign key (
    workspace_id, project_id, workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete restrict,
  constraint task5_workflow_command_result_fk foreign key (
    workspace_id, project_id, result_workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete restrict,
  constraint task5_workflow_command_result_shape check (
    (command_type = 'retry' and result_workflow_run_id is not null)
    or (command_type = 'cancel' and result_workflow_run_id is null)
  )
);

create trigger task5_workflow_commands_append_only
before update or delete on public.workflow_run_commands
for each row execute function public.motionly_reject_immutable_mutation();

alter table public.workflow_run_commands enable row level security;
create policy task5_members_read_workflow_commands on public.workflow_run_commands
for select using (public.is_workspace_member(workspace_id));
revoke all on public.workflow_run_commands from public, anon, authenticated;
grant select on public.workflow_run_commands to authenticated;
grant all on public.workflow_run_commands to service_role;

create or replace function public.motionly_cancel_workflow_run(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_expected_revision integer,
  p_idempotency_key text,
  p_requested_by uuid
)
returns setof jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
  run public.workflow_runs;
  command public.workflow_run_commands;
  event_row public.workflow_events;
  seq integer;
  request_digest text;
  payload jsonb;
begin
  request_digest := encode(extensions.digest(
    'cancel:' || p_workflow_run_id || ':' || p_expected_revision, 'sha256'
  ), 'hex');
  select * into command from public.workflow_run_commands
  where workspace_id = p_workspace_id and project_id = p_project_id
    and workflow_run_id = p_workflow_run_id and command_type = 'cancel'
    and idempotency_key = p_idempotency_key;
  if found then
    if command.request_hash <> request_digest then
      raise exception 'WORKFLOW_COMMAND_IDEMPOTENCY_CONFLICT' using errcode = 'P0001';
    end if;
    select * into run from public.workflow_runs where id = p_workflow_run_id;
    return next to_jsonb(run) || jsonb_build_object('deduplicated', true);
    return;
  end if;

  select * into run from public.workflow_runs
  where id = p_workflow_run_id and workspace_id = p_workspace_id and project_id = p_project_id
  for update;
  if not found then raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002'; end if;
  if run.revision <> p_expected_revision then
    raise exception 'WORKFLOW_REVISION_STALE' using errcode = 'P0001';
  end if;
  if run.state in ('export_ready', 'failed', 'cancelled', 'superseded') then
    raise exception 'WORKFLOW_NOT_CANCELABLE' using errcode = 'P0001';
  end if;

  update public.workflow_runs
  set state = 'cancelled', revision = revision + 1,
      completed_at = clock_timestamp(), current_step_key = null
  where id = run.id returning * into run;
  select coalesce(max(sequence_number), 0) + 1 into seq
  from public.workflow_events where workflow_run_id = run.id;
  payload := jsonb_build_object('workflow_run_id', run.id, 'state', 'cancelled');
  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, sequence_number,
    event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, run.id, seq,
    'workflow.cancelled', 'workflow-cancelled:' || run.id || ':' || p_idempotency_key,
    payload
  ) returning * into event_row;
  insert into public.transactional_outbox (
    workspace_id, project_id, workflow_run_id, workflow_event_id,
    destination, event_type, deduplication_key, payload_hash, payload
  ) values (
    p_workspace_id, p_project_id, run.id, event_row.id,
    'temporal', 'workflow.cancelled',
    'workflow-cancelled:' || run.id || ':' || p_idempotency_key,
    encode(extensions.digest(payload::text, 'sha256'), 'hex'), payload
  );
  insert into public.workflow_run_commands (
    workspace_id, project_id, workflow_run_id, command_type,
    idempotency_key, request_hash, expected_revision, requested_by
  ) values (
    p_workspace_id, p_project_id, run.id, 'cancel',
    p_idempotency_key, request_digest, p_expected_revision, p_requested_by
  );
  return next to_jsonb(run) || jsonb_build_object('deduplicated', false);
end;
$$;

create or replace function public.motionly_retry_workflow_run(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_expected_revision integer,
  p_idempotency_key text,
  p_requested_by uuid
)
returns setof jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
  source public.workflow_runs;
  retried public.workflow_runs;
  upload public.uploaded_videos;
  command public.workflow_run_commands;
  event_row public.workflow_events;
  seq integer;
  request_digest text;
  new_request_hash text;
  new_idempotency_key text;
  temporal_id text;
begin
  request_digest := encode(extensions.digest(
    'retry:' || p_workflow_run_id || ':' || p_expected_revision, 'sha256'
  ), 'hex');
  select * into command from public.workflow_run_commands
  where workspace_id = p_workspace_id and project_id = p_project_id
    and workflow_run_id = p_workflow_run_id and command_type = 'retry'
    and idempotency_key = p_idempotency_key;
  if found then
    if command.request_hash <> request_digest then
      raise exception 'WORKFLOW_COMMAND_IDEMPOTENCY_CONFLICT' using errcode = 'P0001';
    end if;
    select * into retried from public.workflow_runs where id = command.result_workflow_run_id;
    return next to_jsonb(retried) || jsonb_build_object('deduplicated', true);
    return;
  end if;

  select * into source from public.workflow_runs
  where id = p_workflow_run_id and workspace_id = p_workspace_id and project_id = p_project_id
  for update;
  if not found then raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002'; end if;
  if source.revision <> p_expected_revision then
    raise exception 'WORKFLOW_REVISION_STALE' using errcode = 'P0001';
  end if;
  if source.state not in ('failed', 'cancelled') then
    raise exception 'WORKFLOW_NOT_RETRYABLE' using errcode = 'P0001';
  end if;
  select * into upload from public.uploaded_videos
  where id = source.uploaded_video_id and workspace_id = p_workspace_id
    and project_id = p_project_id for share;
  if upload.media_revision <> source.source_media_revision
     or upload.source_object_reference_id is distinct from source.source_object_reference_id
  then
    raise exception 'WORKFLOW_SOURCE_STALE' using errcode = 'P0001';
  end if;

  new_idempotency_key := 'retry:' || source.id || ':' || left(
    encode(extensions.digest(p_idempotency_key, 'sha256'), 'hex'), 20
  );
  new_request_hash := encode(extensions.digest(
    source.request_hash || ':' || request_digest, 'sha256'
  ), 'hex');
  temporal_id := concat(
    'motionly:', source.workflow_type, ':', p_workspace_id, ':', p_project_id, ':',
    source.uploaded_video_id, ':', source.source_media_revision, ':',
    left(encode(extensions.digest(new_idempotency_key, 'sha256'), 'hex'), 16)
  );
  insert into public.workflow_runs (
    workspace_id, project_id, uploaded_video_id, source_media_revision,
    source_object_reference_id, source_size_bytes, source_sha256,
    workflow_type, workflow_version, idempotency_key, request_hash,
    temporal_workflow_id, state, input, started_by,
    retry_of_workflow_run_id, root_workflow_run_id
  ) values (
    source.workspace_id, source.project_id, source.uploaded_video_id,
    source.source_media_revision, source.source_object_reference_id,
    source.source_size_bytes, source.source_sha256,
    source.workflow_type, source.workflow_version, new_idempotency_key,
    new_request_hash, temporal_id, 'uploaded',
    source.input || jsonb_build_object('retry_of_workflow_run_id', source.id),
    p_requested_by, source.id, source.root_workflow_run_id
  ) returning * into retried;

  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, sequence_number,
    event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, retried.id, 1,
    'workflow.requested', 'workflow-requested:' || retried.id,
    jsonb_build_object(
      'workflow_run_id', retried.id,
      'workflow_type', retried.workflow_type,
      'uploaded_video_id', retried.uploaded_video_id,
      'source_media_revision', retried.source_media_revision
    )
  ) returning * into event_row;
  insert into public.transactional_outbox (
    workspace_id, project_id, workflow_run_id, workflow_event_id,
    destination, event_type, deduplication_key, payload_hash, payload
  ) values (
    p_workspace_id, p_project_id, retried.id, event_row.id,
    'temporal', 'workflow.start', 'workflow-start:' || retried.id,
    encode(extensions.digest(event_row.payload::text, 'sha256'), 'hex'), event_row.payload
  );
  select coalesce(max(sequence_number), 0) + 1 into seq
  from public.workflow_events where workflow_run_id = source.id;
  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, sequence_number,
    event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, source.id, seq,
    'workflow.retry.requested',
    'workflow-retry:' || source.id || ':' || p_idempotency_key,
    jsonb_build_object(
      'workflow_run_id', source.id,
      'retry_workflow_run_id', retried.id
    )
  );
  insert into public.workflow_run_commands (
    workspace_id, project_id, workflow_run_id, command_type,
    idempotency_key, request_hash, expected_revision,
    result_workflow_run_id, requested_by
  ) values (
    p_workspace_id, p_project_id, source.id, 'retry',
    p_idempotency_key, request_digest, p_expected_revision,
    retried.id, p_requested_by
  );
  return next to_jsonb(retried) || jsonb_build_object('deduplicated', false);
end;
$$;

-- Multipart finalization and workflow start are separate transactions at the
-- API boundary. This idempotent function plus the reconciler below closes the
-- crash window without claiming cross-service atomicity.
create or replace function public.motionly_ensure_first_draft_v2(
  p_workspace_id uuid,
  p_project_id uuid,
  p_uploaded_video_id uuid,
  p_started_by uuid default null
)
returns setof jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
  upload public.uploaded_videos;
  result jsonb;
  key text;
  request_digest text;
begin
  select * into upload from public.uploaded_videos
  where id = p_uploaded_video_id and workspace_id = p_workspace_id and project_id = p_project_id
  for share;
  if not found then raise exception 'WORKFLOW_UPLOAD_NOT_FOUND' using errcode = 'P0002'; end if;
  if upload.workflow_version <> 'first-draft-v2'
     or upload.status not in ('processing', 'ready')
     or upload.source_object_reference_id is null
     or upload.verified_size_bytes is null
     or upload.verified_sha256 is null
  then
    raise exception 'WORKFLOW_UPLOAD_NOT_FINALIZED' using errcode = 'P0001';
  end if;
  key := 'upload:first-draft-v2:' || upload.id || ':' || upload.media_revision;
  request_digest := encode(extensions.digest(
    p_workspace_id || ':' || p_project_id || ':' || upload.id || ':'
      || upload.media_revision || ':' || upload.source_object_reference_id,
    'sha256'
  ), 'hex');
  select started.result into result
  from public.motionly_start_workflow_run(
    p_workspace_id, p_project_id, upload.id, upload.media_revision,
    upload.source_object_reference_id, 'first_draft', 'first-draft-v2',
    key, request_digest, p_started_by,
    jsonb_build_object('upload_id', upload.id, 'source_media_revision', upload.media_revision)
  ) as started(result);
  return next result;
end;
$$;

create or replace function public.motionly_reconcile_first_draft_v2(
  p_limit integer default 50
)
returns setof jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  upload record;
  result jsonb;
begin
  if p_limit < 1 or p_limit > 100 then
    raise exception 'WORKFLOW_RECONCILE_BOUNDS_INVALID' using errcode = '22023';
  end if;
  for upload in
    select video.workspace_id, video.project_id, video.id
    from public.uploaded_videos video
    where video.workflow_version = 'first-draft-v2'
      and video.status in ('processing', 'ready')
      and video.source_object_reference_id is not null
      and video.verified_size_bytes is not null
      and video.verified_sha256 is not null
      and not exists (
        select 1 from public.workflow_runs run
        where run.workspace_id = video.workspace_id
          and run.project_id = video.project_id
          and run.uploaded_video_id = video.id
          and run.workflow_type = 'first_draft'
          and run.workflow_version = 'first-draft-v2'
          and run.source_media_revision = video.media_revision
      )
    order by video.updated_at, video.id
    for update skip locked
    limit p_limit
  loop
    select ensured.result into result
    from public.motionly_ensure_first_draft_v2(
      upload.workspace_id, upload.project_id, upload.id, null
    ) as ensured(result);
    return next result;
  end loop;
end;
$$;

-- Race-safe base export upsert. The advisory project lock also serializes the
-- legacy per-project version counter.
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
    workspace_id, project_id, render_job_id, workflow_run_id, version,
    storage_bucket, storage_path, format, width, height, fps,
    duration_ms, file_size_bytes, thumbnail_url, settings_summary
  ) values (
    p_workspace_id, p_project_id, p_render_job_id, render.workflow_run_id, next_version,
    p_storage_bucket, p_storage_path, p_format::public.export_format,
    p_width, p_height, p_fps, p_duration_ms, p_file_size_bytes,
    p_thumbnail_url, coalesce(p_settings_summary, '{}'::jsonb)
  ) returning * into exported;
  return next exported;
end;
$$;

-- Automatically carry known proposal decisions, timeline revisions, renders,
-- and exports into the typed lineage relation. Transcript/visual links are made
-- explicitly by their workflow step completion integration.
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

create trigger task5_decisions_capture_workflow_artifact
after insert on public.ai_edit_proposal_decisions
for each row execute function public.motionly_capture_task5_artifact();
create trigger task5_timeline_capture_workflow_artifact
after insert on public.timeline_revisions
for each row execute function public.motionly_capture_task5_artifact();
create trigger task5_renders_capture_workflow_artifact
after insert on public.render_jobs
for each row execute function public.motionly_capture_task5_artifact();
create trigger task5_exports_capture_workflow_artifact
after insert on public.exports
for each row execute function public.motionly_capture_task5_artifact();

-- Service-role-only mutation/recovery boundary.
revoke all on function public.motionly_link_workflow_artifact(uuid,uuid,uuid,text,uuid,integer)
from public, anon, authenticated;
revoke all on function public.motionly_prepare_workflow_step(uuid,uuid,uuid,text,text,text,integer,text,uuid)
from public, anon, authenticated;
revoke all on function public.motionly_cancel_workflow_run(uuid,uuid,uuid,integer,text,uuid)
from public, anon, authenticated;
revoke all on function public.motionly_retry_workflow_run(uuid,uuid,uuid,integer,text,uuid)
from public, anon, authenticated;
revoke all on function public.motionly_ensure_first_draft_v2(uuid,uuid,uuid,uuid)
from public, anon, authenticated;
revoke all on function public.motionly_reconcile_first_draft_v2(integer)
from public, anon, authenticated;
revoke all on function public.motionly_upsert_base_export(
  uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb
) from public, anon, authenticated;

grant execute on function public.motionly_link_workflow_artifact(uuid,uuid,uuid,text,uuid,integer)
to service_role;
grant execute on function public.motionly_prepare_workflow_step(uuid,uuid,uuid,text,text,text,integer,text,uuid)
to service_role;
grant execute on function public.motionly_cancel_workflow_run(uuid,uuid,uuid,integer,text,uuid)
to service_role;
grant execute on function public.motionly_retry_workflow_run(uuid,uuid,uuid,integer,text,uuid)
to service_role;
grant execute on function public.motionly_ensure_first_draft_v2(uuid,uuid,uuid,uuid)
to service_role;
grant execute on function public.motionly_reconcile_first_draft_v2(integer)
to service_role;
grant execute on function public.motionly_upsert_base_export(
  uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb
) to service_role;
