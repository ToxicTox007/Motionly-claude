-- Task 5 proposal generation and durable human gate. Forward-only: v1 histories,
-- migrations 008/009, and direct/non-workflow proposal APIs remain unchanged.

create extension if not exists pgcrypto;

-- A proposal may be linked to a v2 run only after canonical source,
-- transcription, and visual analysis have all succeeded.
create or replace function public.motionly_validate_workflow_proposal_link()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
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
     or run.state <> 'proposal_ready'
  then
    raise exception 'WORKFLOW_PROPOSAL_RUN_INVALID' using errcode = 'P0001';
  end if;

  select uv.* into upload
  from public.projects project
  join public.uploaded_videos uv
    on uv.workspace_id = project.workspace_id
   and uv.project_id = project.id
   and uv.id = project.active_video_id
  where project.workspace_id = new.workspace_id
    and project.id = new.project_id
  for share of uv;
  if not found
     or upload.id <> run.uploaded_video_id
     or upload.id <> new.upload_id
     or upload.status <> 'ready'
     or upload.media_revision <> run.source_media_revision
     or upload.media_revision <> new.media_revision
     or upload.source_object_reference_id is distinct from run.source_object_reference_id
     or upload.verified_sha256 is null
     or new.created_by is null
  then
    raise exception 'WORKFLOW_PROPOSAL_SOURCE_STALE' using errcode = 'P0001';
  end if;

  if not exists (
    select 1
    from public.transcripts transcript
    where transcript.id = new.transcript_id
      and transcript.workspace_id = new.workspace_id
      and transcript.project_id = new.project_id
      and transcript.uploaded_video_id = run.uploaded_video_id
      and transcript.status = 'ready'
      and transcript.quality_status in ('ok', 'low_confidence')
      and exists (
        select 1 from public.transcript_words word
        where word.transcript_id = transcript.id
          and word.workspace_id = new.workspace_id
          and word.project_id = new.project_id
      )
  ) then
    raise exception 'WORKFLOW_PROPOSAL_TRANSCRIPT_INVALID' using errcode = 'P0001';
  end if;

  if new.visual_manifest_id is null
     or new.visual_manifest_version is null
     or not exists (
       select 1
       from public.video_manifests visual
       where visual.id = new.visual_manifest_id
         and visual.workspace_id = new.workspace_id
         and visual.project_id = new.project_id
         and visual.uploaded_video_id = run.uploaded_video_id
         and visual.version = new.visual_manifest_version
         and visual.status = 'ready'
         and visual.manifest is not null
     )
  then
    raise exception 'WORKFLOW_PROPOSAL_VISUAL_INVALID' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger task5_workflow_proposals_validate_link
before insert or update of workflow_run_id, upload_id, media_revision, transcript_id,
  visual_manifest_id, visual_manifest_version, created_by
on public.ai_edit_proposals
for each row execute function public.motionly_validate_workflow_proposal_link();

revoke all on function public.motionly_validate_workflow_proposal_link()
from public, anon, authenticated, service_role;

alter table public.ai_edit_proposals
  add constraint task5_workflow_proposal_creator_required check (
    workflow_run_id is null or created_by is not null
  );

-- Preserve the audited Task 7 completion body and add a narrow precondition for
-- the new workflow-owned planning step.
alter function public.motionly_complete_workflow_step(
  uuid,uuid,uuid,text,uuid,text,jsonb,text,text
) rename to motionly_complete_workflow_step_task7_audited;

revoke all on function public.motionly_complete_workflow_step_task7_audited(
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
  step public.workflow_steps;
  proposal public.ai_edit_proposals;
begin
  select * into step
  from public.workflow_steps
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and workflow_run_id = p_workflow_run_id
    and step_key = p_step_key;

  if found and step.step_kind = 'generate_proposal' then
    select proposal_row.* into proposal
    from public.ai_edit_proposals proposal_row
    join public.workflow_run_artifacts artifact
      on artifact.workspace_id = proposal_row.workspace_id
     and artifact.project_id = proposal_row.project_id
     and artifact.workflow_run_id = p_workflow_run_id
     and artifact.artifact_type = 'proposal'
     and artifact.proposal_id = proposal_row.id
    where proposal_row.workspace_id = p_workspace_id
      and proposal_row.project_id = p_project_id
      and proposal_row.id = step.resource_id
    for share of proposal_row;
    if not found
       or proposal.state <> 'awaiting_approval'
       or p_next_run_state is distinct from 'awaiting_approval'
       or jsonb_typeof(p_output) <> 'object'
       or p_output->>'proposal_id' is distinct from proposal.id::text
       or p_output->>'status' is distinct from 'awaiting_approval'
       or p_output->>'result_hash' is distinct from proposal.result_hash
       or coalesce((p_output->>'failed')::boolean, false)
       or p_output - array[
         'proposal_id', 'status', 'result_hash', 'failed',
         'error_code', 'error_message'
       ] <> '{}'::jsonb
    then
      raise exception 'WORKFLOW_PROPOSAL_NOT_APPROVAL_READY' using errcode = 'P0001';
    end if;
  end if;

  return query
  select * from public.motionly_complete_workflow_step_task7_audited(
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

-- Keep every Task 11 check and mutation in the audited body. The stable wrapper
-- projects only workflow identifiers/revisions and appends one durable gate fact
-- in the same PostgreSQL transaction.
alter function public.motionly_decide_ai_edit_proposal(
  uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid
) rename to motionly_decide_ai_edit_proposal_task11_audited;

revoke all on function public.motionly_decide_ai_edit_proposal_task11_audited(
  uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid
) from public, anon, authenticated, service_role;

create or replace function public.motionly_decide_ai_edit_proposal(
  p_workspace_id uuid,
  p_project_id uuid,
  p_proposal_id uuid,
  p_decision text,
  p_expected_proposal_revision integer,
  p_expected_base_active_plan_id uuid,
  p_expected_base_active_plan_revision integer,
  p_idempotency_key text,
  p_accepted_action_ids text[],
  p_decided_by uuid
)
returns table (
  decision_id uuid,
  accepted_action_ids text[],
  result_hash text,
  edit_plan_id uuid,
  edit_plan_revision integer,
  idempotent_replay boolean
)
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
  decided record;
  proposal public.ai_edit_proposals;
  run public.workflow_runs;
  timeline public.timeline_revisions;
  event_row public.workflow_events;
  existing_event public.workflow_events;
  gate_payload jsonb;
  payload_digest text;
  gate_event_key text;
  seq integer;
begin
  select * into decided
  from public.motionly_decide_ai_edit_proposal_task11_audited(
    p_workspace_id,
    p_project_id,
    p_proposal_id,
    p_decision,
    p_expected_proposal_revision,
    p_expected_base_active_plan_id,
    p_expected_base_active_plan_revision,
    p_idempotency_key,
    p_accepted_action_ids,
    p_decided_by
  );

  select * into proposal
  from public.ai_edit_proposals
  where id = p_proposal_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id;
  if proposal.workflow_run_id is null then
    return query select
      decided.decision_id::uuid,
      decided.accepted_action_ids::text[],
      decided.result_hash::text,
      decided.edit_plan_id::uuid,
      decided.edit_plan_revision::integer,
      decided.idempotent_replay::boolean;
    return;
  end if;

  select * into run
  from public.workflow_runs
  where id = proposal.workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found or run.workflow_version <> 'first-draft-v2' then
    raise exception 'WORKFLOW_PROPOSAL_RUN_INVALID' using errcode = 'P0001';
  end if;

  perform * from public.motionly_link_workflow_artifact(
    p_workspace_id,
    p_project_id,
    run.id,
    'decision',
    decided.decision_id,
    null
  );

  if p_decision = 'reject' then
    if decided.edit_plan_id is not null then
      raise exception 'WORKFLOW_REJECTION_PLAN_INVALID' using errcode = 'P0001';
    end if;
    if run.state = 'awaiting_approval' then
      update public.workflow_runs
      set state = 'cancelled',
          revision = revision + 1,
          current_step_key = 'approval-gate-v2',
          active_plan_id = null,
          active_plan_revision = null,
          completed_at = clock_timestamp()
      where id = run.id
      returning * into run;
    elsif run.state <> 'cancelled'
          or run.active_plan_id is not null
          or run.active_plan_revision is not null then
      raise exception 'WORKFLOW_APPROVAL_STATE_CONFLICT' using errcode = 'P0001';
    end if;
    gate_payload := jsonb_build_object(
      'workflow_run_id', run.id,
      'proposal_id', proposal.id,
      'decision_id', decided.decision_id,
      'decision', 'reject',
      'proposal_revision', proposal.revision,
      'workflow_revision', run.revision
    );
  else
    if decided.edit_plan_id is null or decided.edit_plan_revision is null then
      raise exception 'WORKFLOW_APPROVAL_PLAN_MISSING' using errcode = 'P0001';
    end if;
    select timeline_row.* into timeline
    from public.timeline_revisions timeline_row
    where timeline_row.workspace_id = p_workspace_id
      and timeline_row.project_id = p_project_id
      and timeline_row.proposal_id = proposal.id
      and timeline_row.decision_id = decided.decision_id
      and timeline_row.edit_plan_id = decided.edit_plan_id
      and timeline_row.revision = decided.edit_plan_revision;
    if not found then
      raise exception 'WORKFLOW_APPROVAL_TIMELINE_MISSING' using errcode = 'P0001';
    end if;
    perform * from public.motionly_link_workflow_artifact(
      p_workspace_id,
      p_project_id,
      run.id,
      'timeline_revision',
      timeline.id,
      timeline.revision
    );
    if run.state = 'awaiting_approval' then
      update public.workflow_runs
      set state = 'assembling',
          revision = revision + 1,
          current_step_key = 'approval-gate-v2',
          active_plan_id = decided.edit_plan_id,
          active_plan_revision = decided.edit_plan_revision,
          completed_at = null
      where id = run.id
      returning * into run;
    elsif run.state <> 'assembling'
          or run.active_plan_id is distinct from decided.edit_plan_id
          or run.active_plan_revision is distinct from decided.edit_plan_revision then
      raise exception 'WORKFLOW_APPROVAL_STATE_CONFLICT' using errcode = 'P0001';
    end if;
    gate_payload := jsonb_build_object(
      'workflow_run_id', run.id,
      'proposal_id', proposal.id,
      'decision_id', decided.decision_id,
      'decision', p_decision,
      'proposal_revision', proposal.revision,
      'workflow_revision', run.revision,
      'edit_plan_id', decided.edit_plan_id,
      'edit_plan_revision', decided.edit_plan_revision
    );
  end if;

  gate_event_key := 'approval-gate:' || run.id || ':' || decided.decision_id;
  select * into existing_event
  from public.workflow_events
  where workflow_run_id = run.id and event_key = gate_event_key;
  if found then
    if existing_event.payload is distinct from gate_payload
       or existing_event.event_type <> 'workflow.approval.decided' then
      raise exception 'WORKFLOW_APPROVAL_EVENT_CONFLICT' using errcode = 'P0001';
    end if;
    event_row := existing_event;
  else
    select coalesce(max(sequence_number), 0) + 1 into seq
    from public.workflow_events
    where workflow_run_id = run.id;
    insert into public.workflow_events (
      workspace_id, project_id, workflow_run_id, sequence_number,
      event_type, event_key, payload
    ) values (
      p_workspace_id, p_project_id, run.id, seq,
      'workflow.approval.decided', gate_event_key, gate_payload
    ) returning * into event_row;
  end if;

  payload_digest := encode(extensions.digest(gate_payload::text, 'sha256'), 'hex');
  insert into public.transactional_outbox (
    workspace_id, project_id, workflow_run_id, workflow_event_id,
    destination, event_type, deduplication_key, payload_hash, payload
  ) values (
    p_workspace_id, p_project_id, run.id, event_row.id,
    'temporal', 'workflow.approval.decided', gate_event_key, payload_digest, gate_payload
  ) on conflict (destination, deduplication_key) do nothing;
  if not exists (
    select 1 from public.transactional_outbox outbox
    where outbox.destination = 'temporal'
      and outbox.deduplication_key = gate_event_key
      and outbox.workflow_run_id = run.id
      and outbox.workflow_event_id = event_row.id
      and outbox.event_type = 'workflow.approval.decided'
      and outbox.payload_hash = payload_digest
      and outbox.payload = gate_payload
  ) then
    raise exception 'WORKFLOW_APPROVAL_OUTBOX_CONFLICT' using errcode = 'P0001';
  end if;

  return query select
    decided.decision_id::uuid,
    decided.accepted_action_ids::text[],
    decided.result_hash::text,
    decided.edit_plan_id::uuid,
    decided.edit_plan_revision::integer,
    decided.idempotent_replay::boolean;
end;
$$;

revoke all on function public.motionly_decide_ai_edit_proposal(
  uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid
) from public, anon, authenticated;
grant execute on function public.motionly_decide_ai_edit_proposal(
  uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid
) to service_role;

-- Recovery-created runs must have an auditable actor. The project creator is
-- canonical when the original request actor is unavailable.
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
  project_creator uuid;
  initiating_user uuid;
  result jsonb;
  key text;
  request_digest text;
begin
  select project.created_by into project_creator
  from public.projects project
  where project.id = p_project_id and project.workspace_id = p_workspace_id;
  if not found or project_creator is null then
    raise exception 'WORKFLOW_PROJECT_NOT_FOUND' using errcode = 'P0002';
  end if;
  initiating_user := coalesce(p_started_by, project_creator);

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
    key, request_digest, initiating_user,
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
    select video.workspace_id, video.project_id, video.id, project.created_by
    from public.uploaded_videos video
    join public.projects project
      on project.workspace_id = video.workspace_id
     and project.id = video.project_id
    where video.workflow_version = 'first-draft-v2'
      and video.status in ('processing', 'ready')
      and video.source_object_reference_id is not null
      and video.verified_size_bytes is not null
      and video.verified_sha256 is not null
      and project.created_by is not null
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
    for update of video skip locked
    limit p_limit
  loop
    select ensured.result into result
    from public.motionly_ensure_first_draft_v2(
      upload.workspace_id, upload.project_id, upload.id, upload.created_by
    ) as ensured(result);
    return next result;
  end loop;
end;
$$;

revoke all on function public.motionly_validate_workflow_proposal_link()
from public, anon, authenticated;
revoke all on function public.motionly_ensure_first_draft_v2(uuid,uuid,uuid,uuid)
from public, anon, authenticated;
revoke all on function public.motionly_reconcile_first_draft_v2(integer)
from public, anon, authenticated;
grant execute on function public.motionly_ensure_first_draft_v2(uuid,uuid,uuid,uuid)
to service_role;
grant execute on function public.motionly_reconcile_first_draft_v2(integer)
to service_role;
