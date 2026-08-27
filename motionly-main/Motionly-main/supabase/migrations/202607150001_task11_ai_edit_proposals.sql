-- Task 11: durable, tenant-scoped AI edit proposals and approval-gated application.
-- Forward-only: existing edit plans and timeline history are not rewritten.

create extension if not exists pgcrypto;

-- Composite tenant keys used by every new foreign key.
create unique index if not exists task11_timeline_workspace_project_id_key
  on public.timeline_revisions (workspace_id, project_id, id);

create table public.ai_edit_proposals (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  request_id text not null check (length(request_id) between 16 and 128),
  request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
  state text not null check (state in (
    'queued', 'generating', 'awaiting_approval', 'approved',
    'partially_approved', 'rejected', 'failed'
  )),
  revision integer not null default 1 check (revision > 0),
  context_version text not null default 'ai-edit-context-v1',
  context_digest text not null check (context_digest ~ '^[0-9a-f]{64}$'),
  context_snapshot jsonb not null check (jsonb_typeof(context_snapshot) = 'object'),
  upload_id uuid not null,
  media_revision integer not null check (media_revision > 0),
  transcript_id uuid not null,
  visual_manifest_id uuid,
  visual_manifest_version integer check (visual_manifest_version is null or visual_manifest_version > 0),
  base_active_plan_id uuid,
  base_active_plan_revision integer check (
    base_active_plan_revision is null or base_active_plan_revision > 0
  ),
  base_timeline_hash text check (
    base_timeline_hash is null or base_timeline_hash ~ '^[0-9a-f]{64}$'
  ),
  prompt_version text not null,
  tool_registry_version text not null default 'ai-edit-tools-v1',
  orchestrator_version text not null default 'ai-edit-orchestrator-v1',
  provider text,
  model text,
  model_revision text,
  route_id text,
  gateway_request_id text,
  planner_source text,
  overall_rationale text check (overall_rationale is null or length(overall_rationale) <= 500),
  confidence numeric check (confidence is null or confidence between 0 and 1),
  degradation_flags jsonb not null default '[]'::jsonb check (jsonb_typeof(degradation_flags) = 'array'),
  risk_flags jsonb not null default '[]'::jsonb check (jsonb_typeof(risk_flags) = 'array'),
  validation_history_codes jsonb not null default '[]'::jsonb check (jsonb_typeof(validation_history_codes) = 'array'),
  retry_history_codes jsonb not null default '[]'::jsonb check (jsonb_typeof(retry_history_codes) = 'array'),
  fallback_history_codes jsonb not null default '[]'::jsonb check (jsonb_typeof(fallback_history_codes) = 'array'),
  estimated_cost_usd numeric check (estimated_cost_usd is null or estimated_cost_usd >= 0),
  actual_cost_usd numeric check (actual_cost_usd is null or actual_cost_usd >= 0),
  usage jsonb not null default '{}'::jsonb check (jsonb_typeof(usage) = 'object'),
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  result_hash text check (result_hash is null or result_hash ~ '^[0-9a-f]{64}$'),
  failure_code text,
  failure_message text,
  generated_plan jsonb,
  generated_summary jsonb,
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint task11_proposal_base_null_pair check (
    (base_active_plan_id is null) = (base_active_plan_revision is null)
  ),
  constraint task11_proposal_terminal_shape check (
    (state in ('approved', 'partially_approved', 'rejected') and result_hash is not null)
    or state not in ('approved', 'partially_approved', 'rejected')
  ),
  constraint task11_proposal_ready_shape check (
    state <> 'awaiting_approval'
    or (generated_plan is not null and generated_summary is not null and result_hash is not null)
  ),
  constraint task11_proposal_project_fk foreign key (workspace_id, project_id)
    references public.projects (workspace_id, id) on delete cascade,
  constraint task11_proposal_upload_fk foreign key (workspace_id, project_id, upload_id)
    references public.uploaded_videos (workspace_id, project_id, id) on delete restrict,
  constraint task11_proposal_transcript_fk foreign key (workspace_id, project_id, transcript_id)
    references public.transcripts (workspace_id, project_id, id) on delete restrict,
  constraint task11_proposal_base_plan_fk foreign key (
    workspace_id, project_id, base_active_plan_id
  ) references public.edit_plans (workspace_id, project_id, id) on delete restrict,
  unique (workspace_id, project_id, request_id),
  unique (workspace_id, project_id, id)
);

create index task11_proposals_project_created_idx
  on public.ai_edit_proposals (workspace_id, project_id, created_at desc);
create index task11_proposals_pending_idx
  on public.ai_edit_proposals (workspace_id, project_id, updated_at)
  where state in ('queued', 'generating', 'awaiting_approval');

create table public.ai_edit_proposal_actions (
  id uuid primary key default gen_random_uuid(),
  proposal_id uuid not null,
  workspace_id uuid not null,
  project_id uuid not null,
  action_id text not null check (action_id ~ '^action_[0-9]{2}_[0-9a-f]{12}$'),
  ordinal integer not null check (ordinal between 0 and 20),
  tool_name text not null check (tool_name in (
    'complete_first_plan', 'set_cuts', 'set_caption_highlights',
    'set_broll_requests', 'set_look_audio_effects'
  )),
  tool_version text not null check (tool_version = '1'),
  args jsonb not null check (jsonb_typeof(args) = 'object'),
  rationale text not null check (length(rationale) between 1 and 240),
  confidence numeric not null check (confidence between 0 and 1),
  dependency_ids text[] not null default '{}',
  risk_flags jsonb not null default '[]'::jsonb check (jsonb_typeof(risk_flags) = 'array'),
  dry_run_hash text not null check (dry_run_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  constraint task11_action_proposal_fk foreign key (workspace_id, project_id, proposal_id)
    references public.ai_edit_proposals (workspace_id, project_id, id) on delete restrict,
  unique (workspace_id, project_id, proposal_id, action_id),
  unique (workspace_id, project_id, proposal_id, ordinal),
  unique (workspace_id, project_id, id)
);

create table public.ai_edit_proposal_decisions (
  id uuid primary key default gen_random_uuid(),
  proposal_id uuid not null,
  workspace_id uuid not null,
  project_id uuid not null,
  decision text not null check (decision in ('approve', 'partial_approve', 'reject')),
  expected_proposal_revision integer not null check (expected_proposal_revision > 0),
  expected_base_active_plan_id uuid,
  expected_base_active_plan_revision integer,
  idempotency_key text not null check (length(idempotency_key) between 8 and 128),
  request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
  accepted_action_ids text[] not null default '{}',
  result_hash text not null check (result_hash ~ '^[0-9a-f]{64}$'),
  edit_plan_id uuid,
  decided_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  constraint task11_decision_base_null_pair check (
    (expected_base_active_plan_id is null) = (expected_base_active_plan_revision is null)
  ),
  constraint task11_decision_selection_shape check (
    (decision = 'reject' and cardinality(accepted_action_ids) = 0)
    or decision <> 'reject'
  ),
  constraint task11_decision_proposal_fk foreign key (workspace_id, project_id, proposal_id)
    references public.ai_edit_proposals (workspace_id, project_id, id) on delete restrict,
  constraint task11_decision_plan_fk foreign key (workspace_id, project_id, edit_plan_id)
    references public.edit_plans (workspace_id, project_id, id) on delete restrict,
  unique (workspace_id, project_id, proposal_id),
  unique (workspace_id, project_id, proposal_id, idempotency_key),
  unique (workspace_id, project_id, id)
);

-- Harden existing audit history without touching historical rows.
create unique index timeline_revisions_plan_revision_unique_idx
  on public.timeline_revisions (edit_plan_id, revision);

alter table public.timeline_revisions
  add column proposal_id uuid,
  add column decision_id uuid,
  add column result_hash text,
  add constraint task11_timeline_result_hash_check check (
    result_hash is null or result_hash ~ '^[0-9a-f]{64}$'
  ),
  add constraint task11_timeline_approval_links_check check (
    (change_type in ('approval', 'partial_approval')
      and proposal_id is not null and decision_id is not null and result_hash is not null)
    or change_type not in ('approval', 'partial_approval')
  ),
  add constraint task11_timeline_proposal_fk foreign key (
    workspace_id, project_id, proposal_id
  ) references public.ai_edit_proposals (workspace_id, project_id, id) on delete restrict,
  add constraint task11_timeline_decision_fk foreign key (
    workspace_id, project_id, decision_id
  ) references public.ai_edit_proposal_decisions (workspace_id, project_id, id) on delete restrict;

alter table public.timeline_revisions
  drop constraint if exists timeline_revisions_change_type_check;
alter table public.timeline_revisions
  add constraint timeline_revisions_change_type_check check (change_type in (
    'cuts', 'timeline', 'manual_elements', 'caption_highlights',
    'compile', 'ai_generate', 'restore', 'approval', 'partial_approval'
  ));

create or replace function public.motionly_reject_immutable_mutation()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  raise exception 'IMMUTABLE_RECORD: % rows are append-only', tg_table_name
    using errcode = '55000';
end;
$$;

drop trigger if exists task11_timeline_revisions_append_only on public.timeline_revisions;
create trigger task11_timeline_revisions_append_only
before update or delete on public.timeline_revisions
for each row execute function public.motionly_reject_immutable_mutation();

drop trigger if exists task11_proposal_actions_immutable on public.ai_edit_proposal_actions;
create trigger task11_proposal_actions_immutable
before update or delete on public.ai_edit_proposal_actions
for each row execute function public.motionly_reject_immutable_mutation();

drop trigger if exists task11_proposal_decisions_immutable on public.ai_edit_proposal_decisions;
create trigger task11_proposal_decisions_immutable
before update or delete on public.ai_edit_proposal_decisions
for each row execute function public.motionly_reject_immutable_mutation();

-- Idempotently create the queued aggregate before queue publication.
create or replace function public.motionly_create_ai_edit_proposal(
  p_workspace_id uuid,
  p_project_id uuid,
  p_request_id text,
  p_context_digest text,
  p_context_snapshot jsonb,
  p_upload_id uuid,
  p_media_revision integer,
  p_transcript_id uuid,
  p_visual_manifest_id uuid,
  p_visual_manifest_version integer,
  p_base_active_plan_id uuid,
  p_base_active_plan_revision integer,
  p_base_timeline_hash text,
  p_prompt_version text,
  p_created_by uuid
)
returns setof jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  existing public.ai_edit_proposals;
begin
  perform pg_advisory_xact_lock(hashtextextended(
    p_workspace_id::text || ':' || p_project_id::text || ':' || p_request_id, 0
  ));

  select * into existing
  from public.ai_edit_proposals
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and request_id = p_request_id;
  if found then
    if existing.context_snapshot->'controls' is distinct from p_context_snapshot->'controls' then
      raise exception 'PROPOSAL_IDEMPOTENCY_CONFLICT' using errcode = 'P0001';
    end if;
    return next to_jsonb(existing) || jsonb_build_object('deduplicated', true);
    return;
  end if;

  insert into public.ai_edit_proposals (
    workspace_id, project_id, request_id, request_hash, state,
    context_digest, context_snapshot, upload_id, media_revision, transcript_id,
    visual_manifest_id, visual_manifest_version, base_active_plan_id,
    base_active_plan_revision, base_timeline_hash, prompt_version,
    degradation_flags, created_by
  ) values (
    p_workspace_id, p_project_id, p_request_id, p_context_digest, 'queued',
    p_context_digest, p_context_snapshot, p_upload_id, p_media_revision, p_transcript_id,
    p_visual_manifest_id, p_visual_manifest_version, p_base_active_plan_id,
    p_base_active_plan_revision, p_base_timeline_hash, p_prompt_version,
    coalesce(p_context_snapshot->'modality_degradation', '[]'::jsonb), p_created_by
  ) returning * into existing;
  return next to_jsonb(existing) || jsonb_build_object('deduplicated', false);
end;
$$;

-- Worker-only state transition. Action validation and proposal completion are one transaction.
create or replace function public.motionly_transition_ai_edit_proposal(
  p_workspace_id uuid,
  p_project_id uuid,
  p_proposal_id uuid,
  p_target_state text,
  p_completion jsonb default '{}'::jsonb
)
returns setof public.ai_edit_proposals
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  proposal public.ai_edit_proposals;
  action jsonb;
  action_ids text[] := '{}';
  dependency text;
  generated_hash text;
begin
  select * into proposal
  from public.ai_edit_proposals
  where id = p_proposal_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    raise exception 'PROPOSAL_NOT_FOUND' using errcode = 'P0002';
  end if;

  if p_target_state = 'generating' then
    if proposal.state in ('awaiting_approval', 'approved', 'partially_approved', 'rejected') then
      return next proposal;
      return;
    end if;
    if proposal.state not in ('queued', 'generating', 'failed') then
      raise exception 'PROPOSAL_STATE_CONFLICT' using errcode = 'P0001';
    end if;
    if proposal.state <> 'generating' then
      update public.ai_edit_proposals
      set state = 'generating', revision = revision + 1, updated_at = now()
      where id = proposal.id
      returning * into proposal;
    end if;
    return next proposal;
    return;
  end if;

  if p_target_state not in ('awaiting_approval', 'failed') or proposal.state <> 'generating' then
    raise exception 'PROPOSAL_STATE_CONFLICT' using errcode = 'P0001';
  end if;

  if p_target_state = 'awaiting_approval' then
    if p_completion->'generated_plan' is null
       or jsonb_typeof(p_completion->'actions') <> 'array'
       or jsonb_array_length(p_completion->'actions') = 0 then
      raise exception 'PROPOSAL_COMPLETION_INVALID' using errcode = '22023';
    end if;

    for action in select value from jsonb_array_elements(p_completion->'actions')
    loop
      if action->>'tool_name' not in (
        'complete_first_plan', 'set_cuts', 'set_caption_highlights',
        'set_broll_requests', 'set_look_audio_effects'
      ) or action->>'tool_version' <> '1'
         or jsonb_typeof(action->'args') <> 'object' then
        raise exception 'PROPOSAL_TOOL_INVALID' using errcode = '22023';
      end if;
      if action->>'id' = any(action_ids) then
        raise exception 'PROPOSAL_ACTION_DUPLICATE' using errcode = '22023';
      end if;
      action_ids := array_append(action_ids, action->>'id');
    end loop;

    for action in select value from jsonb_array_elements(p_completion->'actions')
    loop
      for dependency in select jsonb_array_elements_text(coalesce(action->'dependency_ids', '[]'::jsonb))
      loop
        if dependency <> all(action_ids) then
          raise exception 'PROPOSAL_DEPENDENCY_INVALID' using errcode = '22023';
        end if;
      end loop;
      insert into public.ai_edit_proposal_actions (
        proposal_id, workspace_id, project_id, action_id, ordinal,
        tool_name, tool_version, args, rationale, confidence,
        dependency_ids, risk_flags, dry_run_hash
      ) values (
        proposal.id, proposal.workspace_id, proposal.project_id,
        action->>'id', (action->>'ordinal')::integer, action->>'tool_name',
        action->>'tool_version', action->'args', action->>'rationale',
        (action->>'confidence')::numeric,
        array(select jsonb_array_elements_text(coalesce(action->'dependency_ids', '[]'::jsonb))),
        coalesce(action->'risk_flags', '[]'::jsonb), action->>'dry_run_hash'
      );
    end loop;
    generated_hash := encode(digest(
      proposal.workspace_id::text || ':' || proposal.context_digest || ':' ||
      (p_completion->'generated_plan')::text || ':' || array_to_string(action_ids, ','),
      'sha256'
    ), 'hex');
  end if;

  update public.ai_edit_proposals
  set state = p_target_state,
      revision = revision + 1,
      generated_plan = p_completion->'generated_plan',
      generated_summary = p_completion->'generated_summary',
      provider = p_completion#>>'{provenance,provider}',
      model = p_completion#>>'{provenance,model}',
      model_revision = p_completion#>>'{provenance,model_revision}',
      route_id = p_completion#>>'{provenance,route_id}',
      gateway_request_id = p_completion#>>'{provenance,gateway_request_id}',
      planner_source = p_completion#>>'{provenance,planner_source}',
      overall_rationale = p_completion->>'overall_rationale',
      confidence = nullif(p_completion->>'confidence', '')::numeric,
      risk_flags = coalesce(p_completion->'risk_flags', '[]'::jsonb),
      validation_history_codes = coalesce(p_completion->'validation_history_codes', '[]'::jsonb),
      retry_history_codes = coalesce(p_completion->'retry_history_codes', '[]'::jsonb),
      fallback_history_codes = coalesce(p_completion->'fallback_history_codes', '[]'::jsonb),
      estimated_cost_usd = nullif(p_completion#>>'{cost,estimated_cost_usd}', '')::numeric,
      actual_cost_usd = nullif(p_completion#>>'{cost,actual_cost_usd}', '')::numeric,
      usage = coalesce(p_completion#>'{cost,usage}', '{}'::jsonb),
      latency_ms = nullif(p_completion#>>'{cost,latency_ms}', '')::integer,
      result_hash = generated_hash,
      failure_code = case when p_target_state = 'failed' then p_completion->>'failure_code' end,
      updated_at = now()
  where id = proposal.id
  returning * into proposal;
  return next proposal;
end;
$$;

-- One locked transaction: revalidate context, select actions, project plan,
-- create/activate one version, append one full timeline snapshot, and terminate proposal.
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
set search_path = public, pg_temp
as $$
declare
  proposal public.ai_edit_proposals;
  existing_decision public.ai_edit_proposal_decisions;
  active_plan public.edit_plans;
  upload public.uploaded_videos;
  action public.ai_edit_proposal_actions;
  selected_ids text[] := coalesce(p_accepted_action_ids, '{}');
  all_ids text[];
  unique_count integer;
  request_digest text;
  projected_plan jsonb;
  projected_summary jsonb;
  next_version integer;
  new_plan public.edit_plans;
  new_decision public.ai_edit_proposal_decisions;
begin
  if p_decision not in ('approve', 'partial_approve', 'reject') then
    raise exception 'PROPOSAL_SELECTION_INVALID' using errcode = '22023';
  end if;
  if (p_expected_base_active_plan_id is null) <>
     (p_expected_base_active_plan_revision is null) then
    raise exception 'PROPOSAL_BASE_STALE' using errcode = 'P0001';
  end if;

  request_digest := encode(digest(jsonb_build_object(
    'decision', p_decision,
    'proposal_revision', p_expected_proposal_revision,
    'base_id', p_expected_base_active_plan_id,
    'base_revision', p_expected_base_active_plan_revision,
    'accepted', selected_ids
  )::text, 'sha256'), 'hex');

  perform pg_advisory_xact_lock(hashtextextended(
    p_workspace_id::text || ':' || p_project_id::text || ':' || p_proposal_id::text, 0
  ));

  select * into existing_decision
  from public.ai_edit_proposal_decisions
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and proposal_id = p_proposal_id
    and idempotency_key = p_idempotency_key;
  if found then
    if existing_decision.request_hash <> request_digest then
      raise exception 'PROPOSAL_IDEMPOTENCY_CONFLICT' using errcode = 'P0001';
    end if;
    return query select existing_decision.id, existing_decision.accepted_action_ids,
      existing_decision.result_hash, existing_decision.edit_plan_id,
      coalesce((select revision from public.edit_plans where id = existing_decision.edit_plan_id), 0),
      true;
    return;
  end if;

  perform 1 from public.projects
  where workspace_id = p_workspace_id and id = p_project_id
  for update;
  if not found then
    raise exception 'PROPOSAL_NOT_FOUND' using errcode = 'P0002';
  end if;

  select * into proposal
  from public.ai_edit_proposals
  where id = p_proposal_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    raise exception 'PROPOSAL_NOT_FOUND' using errcode = 'P0002';
  end if;
  if proposal.state <> 'awaiting_approval' then
    raise exception 'PROPOSAL_STATE_CONFLICT' using errcode = 'P0001';
  end if;
  if proposal.revision <> p_expected_proposal_revision then
    raise exception 'PROPOSAL_REVISION_STALE' using errcode = 'P0001';
  end if;
  if proposal.base_active_plan_id is distinct from p_expected_base_active_plan_id
     or proposal.base_active_plan_revision is distinct from p_expected_base_active_plan_revision then
    raise exception 'PROPOSAL_BASE_STALE' using errcode = 'P0001';
  end if;

  select ep.* into active_plan
  from public.edit_plans ep
  where ep.workspace_id = p_workspace_id
    and ep.project_id = p_project_id
    and ep.is_active
  for update;
  if p_expected_base_active_plan_id is null then
    if found then
      raise exception 'PROPOSAL_BASE_STALE' using errcode = 'P0001';
    end if;
  elsif not found or active_plan.id <> p_expected_base_active_plan_id
        or active_plan.revision <> p_expected_base_active_plan_revision then
    raise exception 'PROPOSAL_BASE_STALE' using errcode = 'P0001';
  end if;

  select uv.* into upload
  from public.projects p
  join public.uploaded_videos uv
    on uv.workspace_id = p.workspace_id
   and uv.project_id = p.id
   and uv.id = p.active_video_id
  where p.workspace_id = p_workspace_id and p.id = p_project_id
  for update of uv;
  if not found or upload.id <> proposal.upload_id
     or upload.media_revision <> proposal.media_revision then
    raise exception 'PROPOSAL_MEDIA_STALE' using errcode = 'P0001';
  end if;

  select array_agg(a.action_id order by a.ordinal) into all_ids
  from public.ai_edit_proposal_actions a
  where a.workspace_id = p_workspace_id
    and a.project_id = p_project_id
    and a.proposal_id = p_proposal_id;
  all_ids := coalesce(all_ids, '{}');
  if cardinality(all_ids) = 0 and p_decision <> 'reject' then
    raise exception 'PROPOSAL_SELECTION_INVALID' using errcode = '22023';
  end if;
  select count(distinct value) into unique_count from unnest(selected_ids) value;
  if unique_count <> cardinality(selected_ids) then
    raise exception 'PROPOSAL_SELECTION_INVALID' using errcode = '22023';
  end if;

  if p_decision = 'approve' then
    if cardinality(selected_ids) <> 0 then
      raise exception 'PROPOSAL_SELECTION_INVALID' using errcode = '22023';
    end if;
    selected_ids := all_ids;
  elsif p_decision = 'reject' then
    if cardinality(selected_ids) <> 0 then
      raise exception 'PROPOSAL_SELECTION_INVALID' using errcode = '22023';
    end if;
  elsif cardinality(selected_ids) = 0
        or cardinality(selected_ids) >= cardinality(all_ids) then
    raise exception 'PROPOSAL_SELECTION_INVALID' using errcode = '22023';
  end if;

  if exists (select 1 from unnest(selected_ids) id where id <> all(all_ids)) then
    raise exception 'PROPOSAL_SELECTION_INVALID' using errcode = '22023';
  end if;
  if exists (
    select 1
    from public.ai_edit_proposal_actions a, unnest(a.dependency_ids) dependency
    where a.workspace_id = p_workspace_id
      and a.project_id = p_project_id
      and a.proposal_id = p_proposal_id
      and a.action_id = any(selected_ids)
      and dependency <> all(selected_ids)
  ) then
    raise exception 'PROPOSAL_DEPENDENCY_INVALID' using errcode = '22023';
  end if;

  if p_decision = 'reject' then
    result_hash := encode(digest(
      p_workspace_id::text || ':' || p_proposal_id::text || ':reject:' || proposal.context_digest,
      'sha256'
    ), 'hex');
    insert into public.ai_edit_proposal_decisions (
      proposal_id, workspace_id, project_id, decision, expected_proposal_revision,
      expected_base_active_plan_id, expected_base_active_plan_revision,
      idempotency_key, request_hash, accepted_action_ids, result_hash, decided_by
    ) values (
      proposal.id, p_workspace_id, p_project_id, p_decision, p_expected_proposal_revision,
      p_expected_base_active_plan_id, p_expected_base_active_plan_revision,
      p_idempotency_key, request_digest, '{}', result_hash, p_decided_by
    ) returning * into new_decision;
    update public.ai_edit_proposals
    set state = 'rejected', revision = revision + 1,
        result_hash = new_decision.result_hash, updated_at = now()
    where id = proposal.id;
    return query select new_decision.id, new_decision.accepted_action_ids,
      new_decision.result_hash, null::uuid, null::integer, false;
    return;
  end if;

  projected_plan := case when active_plan.id is null then null else active_plan.plan end;
  for action in
    select a.* from public.ai_edit_proposal_actions a
    where a.workspace_id = p_workspace_id
      and a.project_id = p_project_id
      and a.proposal_id = p_proposal_id
      and a.action_id = any(selected_ids)
    order by a.ordinal
  loop
    case action.tool_name
      when 'complete_first_plan' then
        projected_plan := action.args->'plan';
      when 'set_cuts' then
        if projected_plan is null then raise exception 'PROPOSAL_BASE_STALE'; end if;
        projected_plan := jsonb_set(projected_plan, '{cuts}', action.args->'cuts', true);
        projected_plan := jsonb_set(projected_plan, '{target_duration_s}', action.args->'target_duration_s', true);
        projected_plan := projected_plan - 'timeline';
      when 'set_caption_highlights' then
        projected_plan := jsonb_set(projected_plan, '{caption_highlights}', action.args->'caption_highlights', true);
      when 'set_broll_requests' then
        projected_plan := jsonb_set(projected_plan, '{broll_requests}', action.args->'broll_requests', true);
      when 'set_look_audio_effects' then
        projected_plan := jsonb_set(projected_plan, '{style}', to_jsonb(action.args->>'style'), true);
        projected_plan := jsonb_set(projected_plan, '{music_mood}', to_jsonb(action.args->>'music_mood'), true);
        projected_plan := jsonb_set(projected_plan, '{effects}', action.args->'effects', true);
        projected_plan := jsonb_set(projected_plan, '{color_grade}', to_jsonb(action.args->>'color_grade'), true);
        projected_plan := jsonb_set(projected_plan, '{zoom_moments}', action.args->'zoom_moments', true);
        projected_plan := jsonb_set(projected_plan, '{risk_flags}', action.args->'risk_flags', true);
      else
        raise exception 'PROPOSAL_TOOL_INVALID' using errcode = '22023';
    end case;
  end loop;
  if projected_plan is null then
    raise exception 'PROPOSAL_SELECTION_INVALID' using errcode = '22023';
  end if;

  result_hash := encode(digest(
    p_workspace_id::text || ':' || proposal.context_digest || ':' ||
    projected_plan::text || ':' || array_to_string(selected_ids, ','), 'sha256'
  ), 'hex');
  select coalesce(max(version), 0) + 1 into next_version
  from public.edit_plans
  where workspace_id = p_workspace_id and project_id = p_project_id;

  update public.edit_plans set is_active = false, updated_at = now()
  where workspace_id = p_workspace_id and project_id = p_project_id and is_active;

  projected_summary := jsonb_build_object(
    'duration_s', (projected_plan->>'target_duration_s')::integer,
    'cut_count', jsonb_array_length(coalesce(projected_plan->'cuts', '[]'::jsonb)),
    'broll_count', jsonb_array_length(coalesce(projected_plan->'broll_requests', '[]'::jsonb)),
    'zoom_count', jsonb_array_length(coalesce(projected_plan->'zoom_moments', '[]'::jsonb)),
    'music_mood', projected_plan->>'music_mood',
    'effects', coalesce(projected_plan->'effects', '[]'::jsonb),
    'color_grade', projected_plan->>'color_grade',
    'risk_flags', coalesce(projected_plan->'risk_flags', '[]'::jsonb)
  );

  insert into public.edit_plans (
    workspace_id, project_id, transcript_id, version, schema_version, status,
    is_active, style, target_duration_s, broll_density, prompt, feedback,
    provider, model, planner_source, model_name, prompt_version, retry_count,
    fallback_reason, cache_key, schema_hash, latency_ms, cost_estimate_usd,
    validation_errors, summary, plan
  ) values (
    p_workspace_id, p_project_id, proposal.transcript_id, next_version,
    coalesce((projected_plan->>'schema_version')::integer, 2), 'ready', true,
    (projected_plan->>'style')::public.edit_plan_style,
    (projected_plan->>'target_duration_s')::integer,
    coalesce((proposal.context_snapshot#>>'{controls,broll_density}')::public.broll_density, 'medium'),
    coalesce(proposal.context_snapshot#>>'{controls,prompt}', ''),
    proposal.context_snapshot#>>'{controls,feedback}',
    coalesce(proposal.provider, 'unknown'), coalesce(proposal.model, 'unknown'),
    coalesce(proposal.planner_source, 'unknown'),
    coalesce(proposal.model_revision, proposal.model, 'unknown'), proposal.prompt_version,
    jsonb_array_length(proposal.retry_history_codes),
    proposal.fallback_history_codes->>-1,
    'approved-proposal:' || proposal.id::text || ':' || result_hash,
    encode(digest(projected_plan::text, 'sha256'), 'hex'), proposal.latency_ms,
    coalesce(proposal.actual_cost_usd, proposal.estimated_cost_usd),
    proposal.validation_history_codes, projected_summary, projected_plan
  ) returning * into new_plan;

  insert into public.ai_edit_proposal_decisions (
    proposal_id, workspace_id, project_id, decision, expected_proposal_revision,
    expected_base_active_plan_id, expected_base_active_plan_revision,
    idempotency_key, request_hash, accepted_action_ids, result_hash,
    edit_plan_id, decided_by
  ) values (
    proposal.id, p_workspace_id, p_project_id, p_decision, p_expected_proposal_revision,
    p_expected_base_active_plan_id, p_expected_base_active_plan_revision,
    p_idempotency_key, request_digest, selected_ids, result_hash, new_plan.id, p_decided_by
  ) returning * into new_decision;

  insert into public.timeline_revisions (
    workspace_id, project_id, edit_plan_id, revision, plan_snapshot,
    changed_by, change_type, proposal_id, decision_id, result_hash
  ) values (
    p_workspace_id, p_project_id, new_plan.id, new_plan.revision, projected_plan,
    p_decided_by,
    case when p_decision = 'partial_approve' then 'partial_approval' else 'approval' end,
    proposal.id, new_decision.id, result_hash
  );

  update public.ai_edit_proposals
  set state = case when p_decision = 'partial_approve'
                   then 'partially_approved' else 'approved' end,
      revision = revision + 1, result_hash = new_decision.result_hash, updated_at = now()
  where id = proposal.id;

  return query select new_decision.id, new_decision.accepted_action_ids,
    new_decision.result_hash, new_plan.id, new_plan.revision, false;
end;
$$;

-- Tenant member reads; browser writes are denied both by grants and RLS.
alter table public.ai_edit_proposals enable row level security;
alter table public.ai_edit_proposal_actions enable row level security;
alter table public.ai_edit_proposal_decisions enable row level security;

create policy "members read ai edit proposals" on public.ai_edit_proposals
  for select to authenticated using (public.is_workspace_member(workspace_id));
create policy "members read ai edit proposal actions" on public.ai_edit_proposal_actions
  for select to authenticated using (public.is_workspace_member(workspace_id));
create policy "members read ai edit proposal decisions" on public.ai_edit_proposal_decisions
  for select to authenticated using (public.is_workspace_member(workspace_id));

create policy "deny browser proposal insert" on public.ai_edit_proposals
  for insert to anon, authenticated with check (false);
create policy "deny browser proposal update" on public.ai_edit_proposals
  for update to anon, authenticated using (false) with check (false);
create policy "deny browser proposal delete" on public.ai_edit_proposals
  for delete to anon, authenticated using (false);
create policy "deny browser action insert" on public.ai_edit_proposal_actions
  for insert to anon, authenticated with check (false);
create policy "deny browser action update" on public.ai_edit_proposal_actions
  for update to anon, authenticated using (false) with check (false);
create policy "deny browser action delete" on public.ai_edit_proposal_actions
  for delete to anon, authenticated using (false);
create policy "deny browser decision insert" on public.ai_edit_proposal_decisions
  for insert to anon, authenticated with check (false);
create policy "deny browser decision update" on public.ai_edit_proposal_decisions
  for update to anon, authenticated using (false) with check (false);
create policy "deny browser decision delete" on public.ai_edit_proposal_decisions
  for delete to anon, authenticated using (false);

revoke all on public.ai_edit_proposals from public, anon, authenticated;
revoke all on public.ai_edit_proposal_actions from public, anon, authenticated;
revoke all on public.ai_edit_proposal_decisions from public, anon, authenticated;
grant select on public.ai_edit_proposals to authenticated;
grant select on public.ai_edit_proposal_actions to authenticated;
grant select on public.ai_edit_proposal_decisions to authenticated;
grant all on public.ai_edit_proposals to service_role;
grant all on public.ai_edit_proposal_actions to service_role;
grant all on public.ai_edit_proposal_decisions to service_role;

-- SECURITY DEFINER routines are callable only by the trusted service role.
do $$
declare
  target_function regprocedure;
begin
  for target_function in
    select p.oid::regprocedure
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname in (
        'motionly_create_ai_edit_proposal',
        'motionly_transition_ai_edit_proposal',
        'motionly_decide_ai_edit_proposal',
        'motionly_update_plan_with_revision',
        'motionly_reject_immutable_mutation'
      )
  loop
    execute format('revoke all privileges on function %s from public, anon, authenticated', target_function);
    execute format('grant execute on function %s to service_role', target_function);
  end loop;
end;
$$;
