-- Task 7: PostgreSQL-canonical durable workflows and transactional outbox.
-- Forward-only: existing domain rows are not rewritten.

create extension if not exists pgcrypto;

create table public.workflow_runs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  uploaded_video_id uuid not null,
  source_media_revision integer not null check (source_media_revision > 0),
  source_object_reference_id uuid not null,
  source_size_bytes bigint check (source_size_bytes is null or source_size_bytes > 0),
  source_sha256 text check (source_sha256 is null or source_sha256 ~ '^[0-9a-f]{64}$'),
  workflow_type text not null check (workflow_type in ('first_draft', 'export', 'adapter_training')),
  workflow_version text not null default 'first-draft-v1',
  idempotency_key text not null check (length(idempotency_key) between 8 and 160),
  request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
  temporal_workflow_id text not null check (length(temporal_workflow_id) between 8 and 240),
  temporal_run_id text,
  state text not null check (state in (
    'uploaded', 'analyzing', 'proposal_ready', 'awaiting_approval',
    'assembling', 'draft_rendering', 'draft_ready', 'refining',
    'exporting', 'export_ready', 'failed', 'cancelled', 'superseded'
  )),
  revision integer not null default 1 check (revision > 0),
  current_step_key text,
  active_plan_id uuid,
  active_plan_revision integer check (active_plan_revision is null or active_plan_revision > 0),
  input jsonb not null default '{}'::jsonb check (jsonb_typeof(input) = 'object'),
  failure_code text,
  failure_message text,
  started_by uuid references auth.users(id) on delete set null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, project_id, id),
  unique (workspace_id, project_id, workflow_type, idempotency_key),
  unique (temporal_workflow_id),
  constraint task7_workflow_runs_project_fk foreign key (workspace_id, project_id)
    references public.projects (workspace_id, id) on delete cascade,
  constraint task7_workflow_runs_upload_fk foreign key (
    workspace_id, project_id, uploaded_video_id
  ) references public.uploaded_videos (workspace_id, project_id, id) on delete restrict,
  constraint task7_workflow_runs_reference_fk foreign key (
    workspace_id, project_id, source_object_reference_id
  ) references public.object_references (workspace_id, project_id, id) on delete restrict,
  constraint task7_workflow_runs_plan_fk foreign key (
    workspace_id, project_id, active_plan_id
  ) references public.edit_plans (workspace_id, project_id, id) on delete restrict,
  constraint task7_workflow_runs_plan_pair check (
    (active_plan_id is null) = (active_plan_revision is null)
  ),
  constraint task7_workflow_runs_failure_shape check (
    (state = 'failed' and failure_code is not null)
    or (state <> 'failed' and failure_code is null and failure_message is null)
  ),
  constraint task7_workflow_runs_completion_shape check (
    (state in ('export_ready', 'cancelled', 'superseded') and completed_at is not null)
    or (state not in ('export_ready', 'cancelled', 'superseded') and completed_at is null)
  )
);

create table public.workflow_steps (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  workflow_run_id uuid not null,
  step_key text not null check (length(step_key) between 1 and 120),
  step_kind text not null check (length(step_kind) between 1 and 80),
  state text not null default 'queued' check (state in (
    'queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled', 'skipped'
  )),
  revision integer not null default 1 check (revision > 0),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  max_attempts integer not null default 3 check (max_attempts between 1 and 20),
  bullmq_job_id text,
  resource_id uuid,
  input_hash text check (input_hash is null or input_hash ~ '^[0-9a-f]{64}$'),
  output_hash text check (output_hash is null or output_hash ~ '^[0-9a-f]{64}$'),
  output jsonb,
  last_error_code text,
  last_error_message text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, project_id, id),
  unique (workspace_id, project_id, workflow_run_id, step_key),
  constraint task7_workflow_steps_run_fk foreign key (
    workspace_id, project_id, workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete cascade,
  constraint task7_workflow_steps_terminal_shape check (
    (state in ('succeeded', 'failed', 'cancelled', 'skipped') and completed_at is not null)
    or (state not in ('succeeded', 'failed', 'cancelled', 'skipped') and completed_at is null)
  ),
  constraint task7_workflow_steps_output_shape check (
    (state = 'succeeded' and output_hash is not null and output is not null)
    or state <> 'succeeded'
  )
);

create table public.workflow_step_attempts (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  workflow_run_id uuid not null,
  workflow_step_id uuid not null,
  attempt_number integer not null check (attempt_number > 0),
  attempt_key text not null check (length(attempt_key) between 8 and 200),
  state text not null check (state in (
    'active', 'succeeded', 'failed', 'cancelled', 'abandoned'
  )),
  claim_token uuid not null default gen_random_uuid(),
  lease_owner text not null check (length(lease_owner) between 1 and 160),
  lease_expires_at timestamptz not null,
  heartbeat_at timestamptz not null default now(),
  result_hash text check (result_hash is null or result_hash ~ '^[0-9a-f]{64}$'),
  error_code text,
  error_message text,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  unique (workspace_id, project_id, id),
  unique (workspace_id, project_id, workflow_step_id, attempt_number),
  unique (workspace_id, project_id, attempt_key),
  constraint task7_workflow_attempts_run_fk foreign key (
    workspace_id, project_id, workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete cascade,
  constraint task7_workflow_attempts_step_fk foreign key (
    workspace_id, project_id, workflow_step_id
  ) references public.workflow_steps (workspace_id, project_id, id) on delete cascade,
  constraint task7_workflow_attempts_terminal_shape check (
    (state = 'active' and ended_at is null)
    or (state <> 'active' and ended_at is not null)
  )
);

create unique index task7_workflow_attempts_one_active_idx
  on public.workflow_step_attempts (workspace_id, project_id, workflow_step_id)
  where state = 'active';

create table public.workflow_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  workflow_run_id uuid not null,
  workflow_step_id uuid,
  sequence_number integer not null check (sequence_number > 0),
  event_type text not null check (length(event_type) between 1 and 120),
  event_key text not null check (length(event_key) between 8 and 240),
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  created_at timestamptz not null default now(),
  unique (workspace_id, project_id, id),
  unique (workspace_id, project_id, workflow_run_id, sequence_number),
  unique (workspace_id, project_id, workflow_run_id, event_key),
  constraint task7_workflow_events_run_fk foreign key (
    workspace_id, project_id, workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete cascade,
  constraint task7_workflow_events_step_fk foreign key (
    workspace_id, project_id, workflow_step_id
  ) references public.workflow_steps (workspace_id, project_id, id) on delete restrict
);

create table public.transactional_outbox (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  workflow_run_id uuid not null,
  workflow_step_id uuid,
  workflow_event_id uuid not null,
  destination text not null check (destination in ('temporal', 'bullmq')),
  event_type text not null check (length(event_type) between 1 and 120),
  payload_version text not null default '1',
  deduplication_key text not null check (length(deduplication_key) between 8 and 240),
  payload_hash text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  state text not null default 'pending' check (state in (
    'pending', 'leased', 'dispatched', 'failed', 'dead_letter'
  )),
  available_at timestamptz not null default now(),
  lease_owner text,
  lease_token uuid,
  lease_expires_at timestamptz,
  claim_generation integer not null default 0 check (claim_generation >= 0),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  max_attempts integer not null default 20 check (max_attempts between 1 and 100),
  transport_message_id text,
  last_error_code text,
  last_error_message text,
  dispatched_at timestamptz,
  dead_lettered_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, project_id, id),
  unique (destination, deduplication_key),
  constraint task7_outbox_run_fk foreign key (
    workspace_id, project_id, workflow_run_id
  ) references public.workflow_runs (workspace_id, project_id, id) on delete cascade,
  constraint task7_outbox_step_fk foreign key (
    workspace_id, project_id, workflow_step_id
  ) references public.workflow_steps (workspace_id, project_id, id) on delete restrict,
  constraint task7_outbox_event_fk foreign key (
    workspace_id, project_id, workflow_event_id
  ) references public.workflow_events (workspace_id, project_id, id) on delete restrict,
  constraint task7_outbox_lease_shape check (
    (state = 'leased' and lease_owner is not null and lease_token is not null
      and lease_expires_at is not null)
    or (state <> 'leased' and lease_owner is null and lease_token is null
      and lease_expires_at is null)
  ),
  constraint task7_outbox_dispatch_shape check (
    (state = 'dispatched' and dispatched_at is not null and transport_message_id is not null)
    or (state <> 'dispatched' and dispatched_at is null)
  ),
  constraint task7_outbox_dead_shape check (
    (state = 'dead_letter' and dead_lettered_at is not null)
    or (state <> 'dead_letter' and dead_lettered_at is null)
  )
);

create index task7_workflow_runs_project_created_idx
  on public.workflow_runs (workspace_id, project_id, created_at desc);
create index task7_workflow_runs_active_idx
  on public.workflow_runs (workspace_id, project_id, state, updated_at)
  where state not in ('export_ready', 'failed', 'cancelled', 'superseded');
create index task7_workflow_steps_run_state_idx
  on public.workflow_steps (workflow_run_id, state, created_at);
create index task7_workflow_events_run_sequence_idx
  on public.workflow_events (workflow_run_id, sequence_number);
create index task7_outbox_claim_idx
  on public.transactional_outbox (state, available_at, created_at);
create index task7_outbox_expired_lease_idx
  on public.transactional_outbox (state, lease_expires_at)
  where state = 'leased';

create trigger task7_workflow_runs_touch_updated_at
before update on public.workflow_runs
for each row execute function public.touch_updated_at();
create trigger task7_workflow_steps_touch_updated_at
before update on public.workflow_steps
for each row execute function public.touch_updated_at();
create trigger task7_outbox_touch_updated_at
before update on public.transactional_outbox
for each row execute function public.touch_updated_at();

create or replace function public.motionly_protect_workflow_attempt()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if tg_op = 'DELETE' then
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

create trigger task7_workflow_attempts_protect
before update or delete on public.workflow_step_attempts
for each row execute function public.motionly_protect_workflow_attempt();

create trigger task7_workflow_events_append_only
before update or delete on public.workflow_events
for each row execute function public.motionly_reject_immutable_mutation();

create or replace function public.motionly_protect_outbox_identity()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if new.workspace_id <> old.workspace_id
     or new.project_id <> old.project_id
     or new.workflow_run_id <> old.workflow_run_id
     or new.workflow_event_id <> old.workflow_event_id
     or new.destination <> old.destination
     or new.event_type <> old.event_type
     or new.payload_version <> old.payload_version
     or new.deduplication_key <> old.deduplication_key
     or new.payload_hash <> old.payload_hash
     or new.payload <> old.payload
  then
    raise exception 'IMMUTABLE_IDENTITY: outbox message identity cannot change'
      using errcode = '55000';
  end if;
  return new;
end;
$$;

create trigger task7_outbox_protect_identity
before update on public.transactional_outbox
for each row execute function public.motionly_protect_outbox_identity();

-- Source identity changes added by Task 6 must advance media_revision too.
create or replace function public.motionly_increment_media_revision()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if (new.storage_path is distinct from old.storage_path)
     or (new.byte_size is distinct from old.byte_size)
     or (new.duration_ms is distinct from old.duration_ms)
     or (new.source_object_reference_id is distinct from old.source_object_reference_id)
     or (new.verified_size_bytes is distinct from old.verified_size_bytes)
     or (new.verified_sha256 is distinct from old.verified_sha256)
  then
    new.media_revision := old.media_revision + 1;
  end if;
  return new;
end;
$$;

create or replace function public.motionly_start_workflow_run(
  p_workspace_id uuid,
  p_project_id uuid,
  p_uploaded_video_id uuid,
  p_source_media_revision integer,
  p_source_object_reference_id uuid,
  p_workflow_type text,
  p_workflow_version text,
  p_idempotency_key text,
  p_request_hash text,
  p_started_by uuid,
  p_input jsonb default '{}'::jsonb
)
returns setof jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  existing public.workflow_runs;
  upload public.uploaded_videos;
  event_row public.workflow_events;
  temporal_id text;
begin
  perform pg_advisory_xact_lock(hashtextextended(
    p_workspace_id::text || ':' || p_project_id::text || ':'
      || p_workflow_type || ':' || p_idempotency_key,
    0
  ));

  select * into existing
  from public.workflow_runs
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and workflow_type = p_workflow_type
    and idempotency_key = p_idempotency_key;
  if found then
    if existing.request_hash <> p_request_hash then
      raise exception 'WORKFLOW_IDEMPOTENCY_CONFLICT' using errcode = 'P0001';
    end if;
    return next to_jsonb(existing) || jsonb_build_object('deduplicated', true);
    return;
  end if;

  select * into upload
  from public.uploaded_videos
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and id = p_uploaded_video_id
  for share;
  if not found then
    raise exception 'WORKFLOW_UPLOAD_NOT_FOUND' using errcode = 'P0002';
  end if;
  if upload.media_revision <> p_source_media_revision
     or upload.source_object_reference_id is distinct from p_source_object_reference_id
  then
    raise exception 'WORKFLOW_SOURCE_STALE' using errcode = 'P0001';
  end if;

  temporal_id := concat(
    'motionly:', p_workflow_type, ':', p_workspace_id, ':', p_project_id, ':',
    p_uploaded_video_id, ':', p_source_media_revision, ':',
    left(encode(extensions.digest(p_idempotency_key, 'sha256'), 'hex'), 16)
  );
  insert into public.workflow_runs (
    workspace_id, project_id, uploaded_video_id, source_media_revision,
    source_object_reference_id, source_size_bytes, source_sha256,
    workflow_type, workflow_version, idempotency_key, request_hash,
    temporal_workflow_id, state, input, started_by
  ) values (
    p_workspace_id, p_project_id, p_uploaded_video_id, p_source_media_revision,
    p_source_object_reference_id, upload.verified_size_bytes, upload.verified_sha256,
    p_workflow_type, p_workflow_version, p_idempotency_key, p_request_hash,
    temporal_id, 'uploaded', coalesce(p_input, '{}'::jsonb), p_started_by
  ) returning * into existing;

  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, sequence_number,
    event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, existing.id, 1,
    'workflow.requested', 'workflow-requested:' || existing.id,
    jsonb_build_object(
      'workflow_run_id', existing.id,
      'workflow_type', existing.workflow_type,
      'uploaded_video_id', existing.uploaded_video_id,
      'source_media_revision', existing.source_media_revision
    )
  ) returning * into event_row;

  insert into public.transactional_outbox (
    workspace_id, project_id, workflow_run_id, workflow_event_id,
    destination, event_type, deduplication_key, payload_hash, payload
  ) values (
    p_workspace_id, p_project_id, existing.id, event_row.id,
    'temporal', 'workflow.start', 'workflow-start:' || existing.id,
    encode(extensions.digest(event_row.payload::text, 'sha256'), 'hex'), event_row.payload
  );

  return next to_jsonb(existing) || jsonb_build_object('deduplicated', false);
end;
$$;

create or replace function public.motionly_claim_workflow_outbox(
  p_lease_owner text,
  p_limit integer default 25,
  p_lease_seconds integer default 60
)
returns setof public.transactional_outbox
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if length(p_lease_owner) < 1 or length(p_lease_owner) > 160 then
    raise exception 'OUTBOX_LEASE_OWNER_INVALID' using errcode = '22023';
  end if;
  if p_limit < 1 or p_limit > 100 or p_lease_seconds < 5 or p_lease_seconds > 900 then
    raise exception 'OUTBOX_CLAIM_BOUNDS_INVALID' using errcode = '22023';
  end if;
  return query
  with candidates as (
    select id
    from public.transactional_outbox
    where (
      (state in ('pending', 'failed') and available_at <= clock_timestamp())
      or (state = 'leased' and lease_expires_at <= clock_timestamp())
    )
      and attempt_count < max_attempts
    order by available_at, created_at
    for update skip locked
    limit p_limit
  )
  update public.transactional_outbox outbox
  set state = 'leased',
      lease_owner = p_lease_owner,
      lease_token = gen_random_uuid(),
      lease_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds),
      claim_generation = outbox.claim_generation + 1,
      attempt_count = outbox.attempt_count + 1,
      last_error_code = null,
      last_error_message = null
  from candidates
  where outbox.id = candidates.id
  returning outbox.*;
end;
$$;

create or replace function public.motionly_ack_workflow_outbox(
  p_outbox_id uuid,
  p_lease_token uuid,
  p_transport_message_id text,
  p_temporal_run_id text default null
)
returns setof public.transactional_outbox
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  message public.transactional_outbox;
begin
  select * into message from public.transactional_outbox where id = p_outbox_id for update;
  if not found then
    raise exception 'OUTBOX_NOT_FOUND' using errcode = 'P0002';
  end if;
  if message.state = 'dispatched' then
    if message.transport_message_id is distinct from p_transport_message_id then
      raise exception 'OUTBOX_ACK_CONFLICT' using errcode = 'P0001';
    end if;
    return next message;
    return;
  end if;
  if message.state <> 'leased' or message.lease_token is distinct from p_lease_token then
    raise exception 'OUTBOX_STALE_CLAIM' using errcode = 'P0001';
  end if;
  update public.transactional_outbox
  set state = 'dispatched', transport_message_id = p_transport_message_id,
      dispatched_at = clock_timestamp(), lease_owner = null, lease_token = null,
      lease_expires_at = null
  where id = p_outbox_id
  returning * into message;
  if p_temporal_run_id is not null and message.destination = 'temporal' then
    update public.workflow_runs
    set temporal_run_id = coalesce(temporal_run_id, p_temporal_run_id)
    where id = message.workflow_run_id
      and workspace_id = message.workspace_id
      and project_id = message.project_id;
  end if;
  return next message;
end;
$$;

create or replace function public.motionly_fail_workflow_outbox(
  p_outbox_id uuid,
  p_lease_token uuid,
  p_error_code text,
  p_error_message text,
  p_retry_at timestamptz
)
returns setof public.transactional_outbox
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  message public.transactional_outbox;
  terminal boolean;
begin
  select * into message from public.transactional_outbox where id = p_outbox_id for update;
  if not found then
    raise exception 'OUTBOX_NOT_FOUND' using errcode = 'P0002';
  end if;
  if message.state <> 'leased' or message.lease_token is distinct from p_lease_token then
    raise exception 'OUTBOX_STALE_CLAIM' using errcode = 'P0001';
  end if;
  terminal := message.attempt_count >= message.max_attempts;
  update public.transactional_outbox
  set state = case when terminal then 'dead_letter' else 'failed' end,
      available_at = case when terminal then available_at else greatest(p_retry_at, now()) end,
      last_error_code = left(coalesce(p_error_code, 'dispatch_failed'), 120),
      last_error_message = left(coalesce(p_error_message, 'Dispatch failed.'), 500),
      dead_lettered_at = case when terminal then clock_timestamp() else null end,
      lease_owner = null, lease_token = null, lease_expires_at = null
  where id = p_outbox_id
  returning * into message;
  return next message;
end;
$$;

create or replace function public.motionly_begin_workflow_step(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_step_key text,
  p_step_kind text,
  p_attempt_key text,
  p_lease_owner text,
  p_lease_seconds integer,
  p_input_hash text default null,
  p_max_attempts integer default 3
)
returns setof jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
  step public.workflow_steps;
  attempt public.workflow_step_attempts;
  existing public.workflow_step_attempts;
begin
  select * into run from public.workflow_runs
  where id = p_workflow_run_id and workspace_id = p_workspace_id and project_id = p_project_id
  for update;
  if not found then raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002'; end if;
  if run.state in ('export_ready', 'failed', 'cancelled', 'superseded') then
    raise exception 'WORKFLOW_TERMINAL' using errcode = 'P0001';
  end if;

  insert into public.workflow_steps (
    workspace_id, project_id, workflow_run_id, step_key, step_kind, input_hash, max_attempts
  ) values (
    p_workspace_id, p_project_id, p_workflow_run_id, p_step_key, p_step_kind,
    p_input_hash, p_max_attempts
  ) on conflict (workspace_id, project_id, workflow_run_id, step_key) do nothing;

  select * into step from public.workflow_steps
  where workflow_run_id = p_workflow_run_id and workspace_id = p_workspace_id
    and project_id = p_project_id and step_key = p_step_key
  for update;
  if step.step_kind <> p_step_kind or step.input_hash is distinct from p_input_hash then
    raise exception 'WORKFLOW_STEP_IDEMPOTENCY_CONFLICT' using errcode = 'P0001';
  end if;
  if step.state = 'succeeded' then
    return next jsonb_build_object('step', to_jsonb(step), 'deduplicated', true);
    return;
  end if;

  select * into existing from public.workflow_step_attempts
  where workflow_step_id = step.id and workspace_id = p_workspace_id
    and project_id = p_project_id and state = 'active'
  for update;
  if found then
    if existing.attempt_key = p_attempt_key then
      return next jsonb_build_object(
        'step', to_jsonb(step), 'attempt', to_jsonb(existing), 'deduplicated', true
      );
      return;
    end if;
    if existing.lease_expires_at > clock_timestamp() then
      raise exception 'WORKFLOW_STEP_ALREADY_CLAIMED' using errcode = 'P0001';
    end if;
    update public.workflow_step_attempts
    set state = 'abandoned', ended_at = clock_timestamp(), error_code = 'lease_expired'
    where id = existing.id;
  end if;
  if step.attempt_count >= step.max_attempts then
    raise exception 'WORKFLOW_STEP_ATTEMPTS_EXHAUSTED' using errcode = 'P0001';
  end if;

  update public.workflow_steps
  set state = 'running', attempt_count = attempt_count + 1, revision = revision + 1,
      started_at = coalesce(started_at, clock_timestamp()), completed_at = null
  where id = step.id returning * into step;
  insert into public.workflow_step_attempts (
    workspace_id, project_id, workflow_run_id, workflow_step_id, attempt_number,
    attempt_key, state, lease_owner, lease_expires_at
  ) values (
    p_workspace_id, p_project_id, p_workflow_run_id, step.id, step.attempt_count,
    p_attempt_key, 'active', p_lease_owner,
    clock_timestamp() + make_interval(secs => p_lease_seconds)
  ) returning * into attempt;
  update public.workflow_runs
  set current_step_key = p_step_key, revision = revision + 1
  where id = run.id;
  return next jsonb_build_object(
    'step', to_jsonb(step), 'attempt', to_jsonb(attempt), 'deduplicated', false
  );
end;
$$;

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
  attempt public.workflow_step_attempts;
  event_row public.workflow_events;
  seq integer;
begin
  select * into run from public.workflow_runs
  where id = p_workflow_run_id and workspace_id = p_workspace_id and project_id = p_project_id
  for update;
  if not found then raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002'; end if;
  select * into step from public.workflow_steps
  where workflow_run_id = p_workflow_run_id and workspace_id = p_workspace_id
    and project_id = p_project_id and step_key = p_step_key
  for update;
  if not found then raise exception 'WORKFLOW_STEP_NOT_FOUND' using errcode = 'P0002'; end if;
  if step.state = 'succeeded' then
    if step.output_hash is distinct from p_output_hash then
      raise exception 'WORKFLOW_STEP_RESULT_CONFLICT' using errcode = 'P0001';
    end if;
    return next jsonb_build_object(
      'run', to_jsonb(run), 'step', to_jsonb(step), 'deduplicated', true
    );
    return;
  end if;
  select * into attempt from public.workflow_step_attempts
  where workflow_step_id = step.id and claim_token = p_claim_token and state = 'active'
  for update;
  if not found or attempt.lease_expires_at <= clock_timestamp() then
    raise exception 'WORKFLOW_STEP_STALE_CLAIM' using errcode = 'P0001';
  end if;

  update public.workflow_step_attempts
  set state = 'succeeded', result_hash = p_output_hash, ended_at = clock_timestamp()
  where id = attempt.id;
  update public.workflow_steps
  set state = 'succeeded', output_hash = p_output_hash, output = p_output,
      revision = revision + 1, completed_at = clock_timestamp(),
      last_error_code = null, last_error_message = null
  where id = step.id returning * into step;
  if p_next_run_state is not null then
    update public.workflow_runs
    set state = p_next_run_state, revision = revision + 1,
        current_step_key = p_step_key,
        completed_at = case
          when p_next_run_state in ('export_ready', 'cancelled', 'superseded')
          then clock_timestamp()
          else null
        end
    where id = run.id returning * into run;
  else
    update public.workflow_runs
    set revision = revision + 1, current_step_key = p_step_key
    where id = run.id returning * into run;
  end if;
  select coalesce(max(sequence_number), 0) + 1 into seq
  from public.workflow_events where workflow_run_id = run.id;
  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, workflow_step_id,
    sequence_number, event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, run.id, step.id, seq,
    p_next_event_type, 'step-completed:' || step.id || ':' || p_output_hash,
    jsonb_build_object(
      'workflow_run_id', run.id, 'step_id', step.id, 'step_key', step.step_key,
      'output_hash', p_output_hash, 'state', run.state
    )
  ) returning * into event_row;
  insert into public.transactional_outbox (
    workspace_id, project_id, workflow_run_id, workflow_step_id, workflow_event_id,
    destination, event_type, deduplication_key, payload_hash, payload
  ) values (
    p_workspace_id, p_project_id, run.id, step.id, event_row.id,
    'temporal', p_next_event_type, 'step-completed:' || step.id || ':' || p_output_hash,
    encode(extensions.digest(event_row.payload::text, 'sha256'), 'hex'), event_row.payload
  ) on conflict (destination, deduplication_key) do nothing;
  return next jsonb_build_object(
    'run', to_jsonb(run), 'step', to_jsonb(step), 'deduplicated', false
  );
end;
$$;

create or replace function public.motionly_fail_workflow_step(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_step_key text,
  p_claim_token uuid,
  p_error_code text,
  p_error_message text,
  p_retryable boolean default true
)
returns setof jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
  step public.workflow_steps;
  attempt public.workflow_step_attempts;
  terminal boolean;
  event_row public.workflow_events;
  seq integer;
begin
  select * into run from public.workflow_runs
  where id = p_workflow_run_id and workspace_id = p_workspace_id and project_id = p_project_id
  for update;
  if not found then raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002'; end if;
  select * into step from public.workflow_steps
  where workflow_run_id = p_workflow_run_id and workspace_id = p_workspace_id
    and project_id = p_project_id and step_key = p_step_key
  for update;
  if not found then raise exception 'WORKFLOW_STEP_NOT_FOUND' using errcode = 'P0002'; end if;
  select * into attempt from public.workflow_step_attempts
  where workflow_step_id = step.id and claim_token = p_claim_token and state = 'active'
  for update;
  if not found then raise exception 'WORKFLOW_STEP_STALE_CLAIM' using errcode = 'P0001'; end if;
  terminal := (not p_retryable) or step.attempt_count >= step.max_attempts;
  update public.workflow_step_attempts
  set state = 'failed', error_code = left(p_error_code, 120),
      error_message = left(p_error_message, 500), ended_at = clock_timestamp()
  where id = attempt.id;
  update public.workflow_steps
  set state = case when terminal then 'failed' else 'queued' end,
      revision = revision + 1,
      completed_at = case when terminal then clock_timestamp() else null end,
      last_error_code = left(p_error_code, 120),
      last_error_message = left(p_error_message, 500)
  where id = step.id returning * into step;
  if terminal then
    update public.workflow_runs
    set state = 'failed', failure_code = left(p_error_code, 120),
        failure_message = left(p_error_message, 500), revision = revision + 1
    where id = run.id returning * into run;
  end if;
  select coalesce(max(sequence_number), 0) + 1 into seq
  from public.workflow_events where workflow_run_id = run.id;
  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, workflow_step_id,
    sequence_number, event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, run.id, step.id, seq,
    'workflow.step.failed',
    'step-failed:' || attempt.id,
    jsonb_build_object(
      'workflow_run_id', run.id, 'step_id', step.id, 'step_key', step.step_key,
      'attempt_number', attempt.attempt_number, 'error_code', p_error_code,
      'terminal', terminal
    )
  ) returning * into event_row;
  insert into public.transactional_outbox (
    workspace_id, project_id, workflow_run_id, workflow_step_id, workflow_event_id,
    destination, event_type, deduplication_key, payload_hash, payload
  ) values (
    p_workspace_id, p_project_id, run.id, step.id, event_row.id,
    'temporal', 'workflow.step.failed', 'step-failed:' || attempt.id,
    encode(extensions.digest(event_row.payload::text, 'sha256'), 'hex'), event_row.payload
  );
  return next jsonb_build_object(
    'run', to_jsonb(run), 'step', to_jsonb(step), 'terminal', terminal
  );
end;
$$;

alter table public.workflow_runs enable row level security;
alter table public.workflow_steps enable row level security;
alter table public.workflow_step_attempts enable row level security;
alter table public.workflow_events enable row level security;
alter table public.transactional_outbox enable row level security;

create policy task7_members_read_workflow_runs on public.workflow_runs
for select using (public.is_workspace_member(workspace_id));
create policy task7_members_read_workflow_steps on public.workflow_steps
for select using (public.is_workspace_member(workspace_id));
create policy task7_members_read_workflow_attempts on public.workflow_step_attempts
for select using (public.is_workspace_member(workspace_id));
create policy task7_members_read_workflow_events on public.workflow_events
for select using (public.is_workspace_member(workspace_id));
create policy task7_members_read_workflow_outbox on public.transactional_outbox
for select using (public.is_workspace_member(workspace_id));

revoke all on public.workflow_runs from public, anon, authenticated;
revoke all on public.workflow_steps from public, anon, authenticated;
revoke all on public.workflow_step_attempts from public, anon, authenticated;
revoke all on public.workflow_events from public, anon, authenticated;
revoke all on public.transactional_outbox from public, anon, authenticated;
grant select on public.workflow_runs to authenticated;
grant select on public.workflow_steps to authenticated;
grant select on public.workflow_step_attempts to authenticated;
grant select on public.workflow_events to authenticated;
grant select on public.transactional_outbox to authenticated;
grant all on public.workflow_runs to service_role;
grant all on public.workflow_steps to service_role;
grant all on public.workflow_step_attempts to service_role;
grant all on public.workflow_events to service_role;
grant all on public.transactional_outbox to service_role;

revoke all on function public.motionly_start_workflow_run(
  uuid,uuid,uuid,integer,uuid,text,text,text,text,uuid,jsonb
) from public, anon, authenticated;
revoke all on function public.motionly_claim_workflow_outbox(text,integer,integer)
  from public, anon, authenticated;
revoke all on function public.motionly_ack_workflow_outbox(uuid,uuid,text,text)
  from public, anon, authenticated;
revoke all on function public.motionly_fail_workflow_outbox(uuid,uuid,text,text,timestamptz)
  from public, anon, authenticated;
revoke all on function public.motionly_begin_workflow_step(
  uuid,uuid,uuid,text,text,text,text,integer,text,integer
) from public, anon, authenticated;
revoke all on function public.motionly_complete_workflow_step(
  uuid,uuid,uuid,text,uuid,text,jsonb,text,text
) from public, anon, authenticated;
revoke all on function public.motionly_fail_workflow_step(
  uuid,uuid,uuid,text,uuid,text,text,boolean
) from public, anon, authenticated;

grant execute on function public.motionly_start_workflow_run(
  uuid,uuid,uuid,integer,uuid,text,text,text,text,uuid,jsonb
) to service_role;
grant execute on function public.motionly_claim_workflow_outbox(text,integer,integer)
  to service_role;
grant execute on function public.motionly_ack_workflow_outbox(uuid,uuid,text,text)
  to service_role;
grant execute on function public.motionly_fail_workflow_outbox(uuid,uuid,text,text,timestamptz)
  to service_role;
grant execute on function public.motionly_begin_workflow_step(
  uuid,uuid,uuid,text,text,text,text,integer,text,integer
) to service_role;
grant execute on function public.motionly_complete_workflow_step(
  uuid,uuid,uuid,text,uuid,text,jsonb,text,text
) to service_role;
grant execute on function public.motionly_fail_workflow_step(
  uuid,uuid,uuid,text,uuid,text,text,boolean
) to service_role;
