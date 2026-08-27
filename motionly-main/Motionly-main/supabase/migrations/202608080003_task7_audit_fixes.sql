-- Task 7 independent-audit forward fixes.

-- Attempt idempotency belongs to one logical step, not an entire project.
do $$
declare
  constraint_name text;
begin
  select conname into constraint_name
  from pg_constraint
  where conrelid = 'public.workflow_step_attempts'::regclass
    and contype = 'u'
    and pg_get_constraintdef(oid) =
      'UNIQUE (workspace_id, project_id, attempt_key)';
  if constraint_name is not null then
    execute format(
      'alter table public.workflow_step_attempts drop constraint %I',
      constraint_name
    );
  end if;
end;
$$;

alter table public.workflow_steps
  add constraint task7_workflow_steps_run_identity_unique
    unique (workspace_id, project_id, workflow_run_id, id);

alter table public.workflow_step_attempts
  add constraint task7_workflow_attempts_step_key_unique
    unique (workspace_id, project_id, workflow_step_id, attempt_key),
  add constraint task7_workflow_attempts_same_run_step_fk
    foreign key (workspace_id, project_id, workflow_run_id, workflow_step_id)
    references public.workflow_steps (
      workspace_id, project_id, workflow_run_id, id
    ) on delete cascade;

alter table public.workflow_events
  add constraint task7_workflow_events_run_identity_unique
    unique (workspace_id, project_id, workflow_run_id, id),
  add constraint task7_workflow_events_same_run_step_fk
    foreign key (workspace_id, project_id, workflow_run_id, workflow_step_id)
    references public.workflow_steps (
      workspace_id, project_id, workflow_run_id, id
    ) on delete restrict;

alter table public.transactional_outbox
  add constraint task7_outbox_same_run_step_fk
    foreign key (workspace_id, project_id, workflow_run_id, workflow_step_id)
    references public.workflow_steps (
      workspace_id, project_id, workflow_run_id, id
    ) on delete restrict,
  add constraint task7_outbox_same_run_event_fk
    foreign key (workspace_id, project_id, workflow_run_id, workflow_event_id)
    references public.workflow_events (
      workspace_id, project_id, workflow_run_id, id
    ) on delete restrict;

create or replace function public.motionly_protect_workflow_run()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  transition_allowed boolean;
begin
  if new.id is distinct from old.id
     or new.workspace_id is distinct from old.workspace_id
     or new.project_id is distinct from old.project_id
     or new.uploaded_video_id is distinct from old.uploaded_video_id
     or new.source_media_revision is distinct from old.source_media_revision
     or new.source_object_reference_id is distinct from old.source_object_reference_id
     or new.source_size_bytes is distinct from old.source_size_bytes
     or new.source_sha256 is distinct from old.source_sha256
     or new.workflow_type is distinct from old.workflow_type
     or new.workflow_version is distinct from old.workflow_version
     or new.idempotency_key is distinct from old.idempotency_key
     or new.request_hash is distinct from old.request_hash
     or new.temporal_workflow_id is distinct from old.temporal_workflow_id
     or new.input is distinct from old.input
     or new.started_by is distinct from old.started_by
     or new.started_at is distinct from old.started_at
     or new.created_at is distinct from old.created_at
  then
    raise exception 'IMMUTABLE_IDENTITY: workflow run identity and source lineage cannot change'
      using errcode = '55000';
  end if;

  if old.temporal_run_id is not null
     and new.temporal_run_id is distinct from old.temporal_run_id
  then
    raise exception 'IMMUTABLE_IDENTITY: Temporal run identity cannot change'
      using errcode = '55000';
  end if;

  if old.state in ('export_ready', 'failed', 'cancelled', 'superseded')
     and (
       new.state is distinct from old.state
       or new.revision is distinct from old.revision
       or new.current_step_key is distinct from old.current_step_key
       or new.active_plan_id is distinct from old.active_plan_id
       or new.active_plan_revision is distinct from old.active_plan_revision
       or new.failure_code is distinct from old.failure_code
       or new.failure_message is distinct from old.failure_message
       or new.completed_at is distinct from old.completed_at
     )
  then
    raise exception 'WORKFLOW_TERMINAL: terminal workflow lifecycle cannot change'
      using errcode = '55000';
  end if;

  if new.state is distinct from old.state then
    transition_allowed := case old.state
      when 'uploaded' then new.state in ('analyzing', 'failed', 'cancelled', 'superseded')
      when 'analyzing' then new.state in (
        'proposal_ready', 'failed', 'cancelled', 'superseded'
      )
      when 'proposal_ready' then new.state in (
        'awaiting_approval', 'assembling', 'failed', 'cancelled', 'superseded'
      )
      when 'awaiting_approval' then new.state in (
        'assembling', 'failed', 'cancelled', 'superseded'
      )
      when 'assembling' then new.state in (
        'draft_rendering', 'failed', 'cancelled', 'superseded'
      )
      when 'draft_rendering' then new.state in (
        'draft_ready', 'failed', 'cancelled', 'superseded'
      )
      when 'draft_ready' then new.state in (
        'refining', 'exporting', 'failed', 'cancelled', 'superseded'
      )
      when 'refining' then new.state in (
        'draft_rendering', 'draft_ready', 'exporting',
        'failed', 'cancelled', 'superseded'
      )
      when 'exporting' then new.state in (
        'export_ready', 'failed', 'cancelled', 'superseded'
      )
      else false
    end;
    if not transition_allowed then
      raise exception 'WORKFLOW_TRANSITION_INVALID: % -> %', old.state, new.state
        using errcode = 'P0001';
    end if;
  end if;
  return new;
end;
$$;

create trigger task7_workflow_runs_protect
before update on public.workflow_runs
for each row execute function public.motionly_protect_workflow_run();

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

  update public.transactional_outbox
  set state = 'dead_letter',
      dead_lettered_at = clock_timestamp(),
      last_error_code = 'lease_expired_attempts_exhausted',
      last_error_message = 'Final dispatcher lease expired before acknowledgment.',
      lease_owner = null,
      lease_token = null,
      lease_expires_at = null
  where state = 'leased'
    and lease_expires_at <= clock_timestamp()
    and attempt_count >= max_attempts;

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
  run public.workflow_runs;
begin
  select * into message
  from public.transactional_outbox
  where id = p_outbox_id
  for update;
  if not found then
    raise exception 'OUTBOX_NOT_FOUND' using errcode = 'P0002';
  end if;

  if message.event_type = 'workflow.start' then
    if p_temporal_run_id is null or length(p_temporal_run_id) = 0 then
      raise exception 'OUTBOX_TEMPORAL_RUN_ID_REQUIRED' using errcode = '22023';
    end if;
    select * into run
    from public.workflow_runs
    where id = message.workflow_run_id
      and workspace_id = message.workspace_id
      and project_id = message.project_id
    for update;
    if run.temporal_run_id is not null
       and run.temporal_run_id <> p_temporal_run_id
    then
      raise exception 'OUTBOX_TEMPORAL_RUN_ID_CONFLICT' using errcode = 'P0001';
    end if;
  elsif p_temporal_run_id is not null then
    raise exception 'OUTBOX_TEMPORAL_RUN_ID_UNEXPECTED' using errcode = '22023';
  end if;

  if message.state = 'dispatched' then
    if message.transport_message_id is distinct from p_transport_message_id then
      raise exception 'OUTBOX_ACK_CONFLICT' using errcode = 'P0001';
    end if;
    if message.event_type = 'workflow.start' and run.temporal_run_id is null then
      update public.workflow_runs
      set temporal_run_id = p_temporal_run_id
      where id = run.id;
    end if;
    return next message;
    return;
  end if;

  if message.state <> 'leased'
     or message.lease_token is distinct from p_lease_token
     or message.lease_expires_at <= clock_timestamp()
  then
    raise exception 'OUTBOX_STALE_CLAIM' using errcode = 'P0001';
  end if;

  update public.transactional_outbox
  set state = 'dispatched',
      transport_message_id = p_transport_message_id,
      dispatched_at = clock_timestamp(),
      lease_owner = null,
      lease_token = null,
      lease_expires_at = null
  where id = p_outbox_id
  returning * into message;

  if message.event_type = 'workflow.start' and run.temporal_run_id is null then
    update public.workflow_runs
    set temporal_run_id = p_temporal_run_id
    where id = run.id;
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
  select * into message
  from public.transactional_outbox
  where id = p_outbox_id
  for update;
  if not found then
    raise exception 'OUTBOX_NOT_FOUND' using errcode = 'P0002';
  end if;
  if message.state <> 'leased'
     or message.lease_token is distinct from p_lease_token
     or message.lease_expires_at <= clock_timestamp()
  then
    raise exception 'OUTBOX_STALE_CLAIM' using errcode = 'P0001';
  end if;
  terminal := message.attempt_count >= message.max_attempts;
  update public.transactional_outbox
  set state = case when terminal then 'dead_letter' else 'failed' end,
      available_at = case when terminal then available_at else greatest(p_retry_at, now()) end,
      last_error_code = left(coalesce(p_error_code, 'dispatch_failed'), 120),
      last_error_message = left(coalesce(p_error_message, 'Dispatch failed.'), 500),
      dead_lettered_at = case when terminal then clock_timestamp() else null end,
      lease_owner = null,
      lease_token = null,
      lease_expires_at = null
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
set search_path = public, extensions, pg_temp
as $$
declare
  run public.workflow_runs;
  step public.workflow_steps;
  attempt public.workflow_step_attempts;
  existing public.workflow_step_attempts;
  event_row public.workflow_events;
  seq integer;
  payload jsonb;
begin
  if length(p_lease_owner) < 1 or length(p_lease_owner) > 160
     or p_lease_seconds < 5 or p_lease_seconds > 3600
     or p_max_attempts < 1 or p_max_attempts > 20
  then
    raise exception 'WORKFLOW_STEP_LEASE_BOUNDS_INVALID' using errcode = '22023';
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

  select * into step
  from public.workflow_steps
  where workflow_run_id = p_workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
    and step_key = p_step_key
  for update;

  if found then
    select * into existing
    from public.workflow_step_attempts
    where workflow_step_id = step.id
      and workspace_id = p_workspace_id
      and project_id = p_project_id
      and attempt_key = p_attempt_key;
    if found then
      return next jsonb_build_object(
        'step', to_jsonb(step),
        'attempt', to_jsonb(existing),
        'deduplicated', true,
        'exhausted', step.state = 'failed'
      );
      return;
    end if;
  end if;

  if run.state in ('export_ready', 'failed', 'cancelled', 'superseded') then
    raise exception 'WORKFLOW_TERMINAL' using errcode = 'P0001';
  end if;

  if step.id is null then
    insert into public.workflow_steps (
      workspace_id, project_id, workflow_run_id, step_key,
      step_kind, input_hash, max_attempts
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_step_key,
      p_step_kind, p_input_hash, p_max_attempts
    ) returning * into step;
  elsif step.step_kind <> p_step_kind
        or step.input_hash is distinct from p_input_hash
        or step.max_attempts <> p_max_attempts
  then
    raise exception 'WORKFLOW_STEP_IDEMPOTENCY_CONFLICT' using errcode = 'P0001';
  end if;

  if step.state = 'succeeded' then
    return next jsonb_build_object(
      'step', to_jsonb(step), 'deduplicated', true, 'exhausted', false
    );
    return;
  end if;

  select * into existing
  from public.workflow_step_attempts
  where workflow_step_id = step.id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
    and state = 'active'
  for update;
  if found then
    if existing.lease_expires_at > clock_timestamp() then
      raise exception 'WORKFLOW_STEP_ALREADY_CLAIMED' using errcode = 'P0001';
    end if;
    update public.workflow_step_attempts
    set state = 'abandoned',
        ended_at = clock_timestamp(),
        error_code = 'lease_expired',
        error_message = 'Worker lease expired before terminal result.'
    where id = existing.id
    returning * into existing;
  end if;

  if step.attempt_count >= step.max_attempts then
    update public.workflow_steps
    set state = 'failed',
        revision = revision + 1,
        completed_at = clock_timestamp(),
        last_error_code = 'attempts_exhausted',
        last_error_message = 'All worker attempts expired before terminal result.'
    where id = step.id
    returning * into step;
    update public.workflow_runs
    set state = 'failed',
        failure_code = 'step_attempts_exhausted',
        failure_message = 'A workflow step exhausted all attempts.',
        revision = revision + 1,
        current_step_key = step.step_key
    where id = run.id
    returning * into run;
    select coalesce(max(sequence_number), 0) + 1 into seq
    from public.workflow_events
    where workflow_run_id = run.id;
    payload := jsonb_build_object(
      'workflow_run_id', run.id,
      'step_id', step.id,
      'step_key', step.step_key,
      'attempt_number', existing.attempt_number,
      'error_code', 'attempts_exhausted',
      'terminal', true
    );
    insert into public.workflow_events (
      workspace_id, project_id, workflow_run_id, workflow_step_id,
      sequence_number, event_type, event_key, payload
    ) values (
      p_workspace_id, p_project_id, run.id, step.id,
      seq, 'workflow.step.exhausted',
      'step-exhausted:' || step.id, payload
    ) returning * into event_row;
    insert into public.transactional_outbox (
      workspace_id, project_id, workflow_run_id, workflow_step_id,
      workflow_event_id, destination, event_type, deduplication_key,
      payload_hash, payload
    ) values (
      p_workspace_id, p_project_id, run.id, step.id,
      event_row.id, 'temporal', 'workflow.step.exhausted',
      'step-exhausted:' || step.id,
      encode(extensions.digest(payload::text, 'sha256'), 'hex'), payload
    ) on conflict (destination, deduplication_key) do nothing;
    return next jsonb_build_object(
      'step', to_jsonb(step),
      'attempt', to_jsonb(existing),
      'deduplicated', false,
      'exhausted', true
    );
    return;
  end if;

  update public.workflow_steps
  set state = 'running',
      attempt_count = attempt_count + 1,
      revision = revision + 1,
      started_at = coalesce(started_at, clock_timestamp()),
      completed_at = null
  where id = step.id
  returning * into step;
  insert into public.workflow_step_attempts (
    workspace_id, project_id, workflow_run_id, workflow_step_id,
    attempt_number, attempt_key, state, lease_owner, lease_expires_at
  ) values (
    p_workspace_id, p_project_id, p_workflow_run_id, step.id,
    step.attempt_count, p_attempt_key, 'active', p_lease_owner,
    clock_timestamp() + make_interval(secs => p_lease_seconds)
  ) returning * into attempt;
  update public.workflow_runs
  set state = case
        when state = 'uploaded' and p_step_kind = 'analyze_source' then 'analyzing'
        else state
      end,
      current_step_key = p_step_key,
      revision = revision + 1
  where id = run.id;
  return next jsonb_build_object(
    'step', to_jsonb(step),
    'attempt', to_jsonb(attempt),
    'deduplicated', false,
    'exhausted', false
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
set search_path = public, extensions, pg_temp
as $$
declare
  run public.workflow_runs;
  step public.workflow_steps;
  attempt public.workflow_step_attempts;
  event_row public.workflow_events;
  seq integer;
begin
  select * into run
  from public.workflow_runs
  where id = p_workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002';
  end if;
  select * into step
  from public.workflow_steps
  where workflow_run_id = p_workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
    and step_key = p_step_key
  for update;
  if not found then
    raise exception 'WORKFLOW_STEP_NOT_FOUND' using errcode = 'P0002';
  end if;
  if step.state = 'succeeded' then
    if step.output_hash is distinct from p_output_hash then
      raise exception 'WORKFLOW_STEP_RESULT_CONFLICT' using errcode = 'P0001';
    end if;
    return next jsonb_build_object(
      'run', to_jsonb(run), 'step', to_jsonb(step), 'deduplicated', true
    );
    return;
  end if;
  if run.state in ('export_ready', 'failed', 'cancelled', 'superseded') then
    raise exception 'WORKFLOW_TERMINAL' using errcode = 'P0001';
  end if;
  select * into attempt
  from public.workflow_step_attempts
  where workflow_step_id = step.id
    and claim_token = p_claim_token
    and state = 'active'
  for update;
  if not found or attempt.lease_expires_at <= clock_timestamp() then
    raise exception 'WORKFLOW_STEP_STALE_CLAIM' using errcode = 'P0001';
  end if;

  update public.workflow_step_attempts
  set state = 'succeeded',
      result_hash = p_output_hash,
      ended_at = clock_timestamp()
  where id = attempt.id;
  update public.workflow_steps
  set state = 'succeeded',
      output_hash = p_output_hash,
      output = p_output,
      revision = revision + 1,
      completed_at = clock_timestamp(),
      last_error_code = null,
      last_error_message = null
  where id = step.id
  returning * into step;
  if p_next_run_state is not null then
    update public.workflow_runs
    set state = p_next_run_state,
        revision = revision + 1,
        current_step_key = p_step_key,
        completed_at = case
          when p_next_run_state in ('export_ready', 'cancelled', 'superseded')
          then clock_timestamp()
          else null
        end
    where id = run.id
    returning * into run;
  else
    update public.workflow_runs
    set revision = revision + 1,
        current_step_key = p_step_key
    where id = run.id
    returning * into run;
  end if;
  select coalesce(max(sequence_number), 0) + 1 into seq
  from public.workflow_events
  where workflow_run_id = run.id;
  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, workflow_step_id,
    sequence_number, event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, run.id, step.id, seq,
    p_next_event_type,
    'step-completed:' || step.id || ':' || p_output_hash,
    jsonb_build_object(
      'workflow_run_id', run.id,
      'step_id', step.id,
      'step_key', step.step_key,
      'output_hash', p_output_hash,
      'state', run.state
    )
  ) returning * into event_row;
  insert into public.transactional_outbox (
    workspace_id, project_id, workflow_run_id, workflow_step_id,
    workflow_event_id, destination, event_type, deduplication_key,
    payload_hash, payload
  ) values (
    p_workspace_id, p_project_id, run.id, step.id,
    event_row.id, 'temporal', p_next_event_type,
    'step-completed:' || step.id || ':' || p_output_hash,
    encode(extensions.digest(event_row.payload::text, 'sha256'), 'hex'),
    event_row.payload
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
set search_path = public, extensions, pg_temp
as $$
declare
  run public.workflow_runs;
  step public.workflow_steps;
  attempt public.workflow_step_attempts;
  terminal boolean;
  event_row public.workflow_events;
  seq integer;
begin
  select * into run
  from public.workflow_runs
  where id = p_workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002';
  end if;
  select * into step
  from public.workflow_steps
  where workflow_run_id = p_workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
    and step_key = p_step_key
  for update;
  if not found then
    raise exception 'WORKFLOW_STEP_NOT_FOUND' using errcode = 'P0002';
  end if;

  select * into attempt
  from public.workflow_step_attempts
  where workflow_step_id = step.id
    and claim_token = p_claim_token
  for update;
  if found and attempt.state = 'failed' then
    if attempt.error_code is distinct from left(p_error_code, 120)
       or attempt.error_message is distinct from left(p_error_message, 500)
    then
      raise exception 'WORKFLOW_STEP_FAILURE_CONFLICT' using errcode = 'P0001';
    end if;
    return next jsonb_build_object(
      'run', to_jsonb(run),
      'step', to_jsonb(step),
      'terminal', step.state = 'failed',
      'deduplicated', true
    );
    return;
  end if;
  if run.state in ('export_ready', 'failed', 'cancelled', 'superseded') then
    raise exception 'WORKFLOW_TERMINAL' using errcode = 'P0001';
  end if;
  if not found
     or attempt.state <> 'active'
     or attempt.lease_expires_at <= clock_timestamp()
  then
    raise exception 'WORKFLOW_STEP_STALE_CLAIM' using errcode = 'P0001';
  end if;

  terminal := (not p_retryable) or step.attempt_count >= step.max_attempts;
  update public.workflow_step_attempts
  set state = 'failed',
      error_code = left(p_error_code, 120),
      error_message = left(p_error_message, 500),
      ended_at = clock_timestamp()
  where id = attempt.id;
  update public.workflow_steps
  set state = case when terminal then 'failed' else 'queued' end,
      revision = revision + 1,
      completed_at = case when terminal then clock_timestamp() else null end,
      last_error_code = left(p_error_code, 120),
      last_error_message = left(p_error_message, 500)
  where id = step.id
  returning * into step;
  if terminal then
    update public.workflow_runs
    set state = 'failed',
        failure_code = left(p_error_code, 120),
        failure_message = left(p_error_message, 500),
        revision = revision + 1,
        current_step_key = step.step_key
    where id = run.id
    returning * into run;
  end if;
  select coalesce(max(sequence_number), 0) + 1 into seq
  from public.workflow_events
  where workflow_run_id = run.id;
  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, workflow_step_id,
    sequence_number, event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, run.id, step.id, seq,
    'workflow.step.failed',
    'step-failed:' || attempt.id,
    jsonb_build_object(
      'workflow_run_id', run.id,
      'step_id', step.id,
      'step_key', step.step_key,
      'attempt_number', attempt.attempt_number,
      'error_code', p_error_code,
      'terminal', terminal
    )
  ) returning * into event_row;
  insert into public.transactional_outbox (
    workspace_id, project_id, workflow_run_id, workflow_step_id,
    workflow_event_id, destination, event_type, deduplication_key,
    payload_hash, payload
  ) values (
    p_workspace_id, p_project_id, run.id, step.id,
    event_row.id, 'temporal', 'workflow.step.failed',
    'step-failed:' || attempt.id,
    encode(extensions.digest(event_row.payload::text, 'sha256'), 'hex'),
    event_row.payload
  );
  return next jsonb_build_object(
    'run', to_jsonb(run),
    'step', to_jsonb(step),
    'terminal', terminal,
    'deduplicated', false
  );
end;
$$;
