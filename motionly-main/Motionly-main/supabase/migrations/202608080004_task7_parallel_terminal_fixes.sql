-- Task 7 second independent-audit fixes.

create or replace function public.motionly_close_terminal_workflow_siblings()
returns trigger
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
  cancelled_attempts integer;
  event_row public.workflow_events;
  payload jsonb;
  seq integer;
begin
  if new.state not in ('export_ready', 'failed', 'cancelled', 'superseded')
     or new.state is not distinct from old.state
  then
    return new;
  end if;

  update public.workflow_step_attempts
  set state = 'cancelled',
      error_code = 'workflow_terminal',
      error_message = 'Attempt closed because the workflow became terminal.',
      ended_at = clock_timestamp()
  where workspace_id = new.workspace_id
    and project_id = new.project_id
    and workflow_run_id = new.id
    and state = 'active';
  get diagnostics cancelled_attempts = row_count;

  update public.workflow_steps
  set state = 'cancelled',
      revision = revision + 1,
      completed_at = clock_timestamp(),
      last_error_code = 'workflow_terminal',
      last_error_message = 'Step closed because the workflow became terminal.'
  where workspace_id = new.workspace_id
    and project_id = new.project_id
    and workflow_run_id = new.id
    and state in ('queued', 'running', 'waiting');

  if cancelled_attempts > 0 then
    select coalesce(max(sequence_number), 0) + 1 into seq
    from public.workflow_events
    where workflow_run_id = new.id;
    payload := jsonb_build_object(
      'workflow_run_id', new.id,
      'state', new.state,
      'cancelled_attempt_count', cancelled_attempts
    );
    insert into public.workflow_events (
      workspace_id, project_id, workflow_run_id, sequence_number,
      event_type, event_key, payload
    ) values (
      new.workspace_id, new.project_id, new.id, seq,
      'workflow.siblings.cancelled',
      'terminal-siblings:' || new.id || ':' || new.revision,
      payload
    ) returning * into event_row;
    insert into public.transactional_outbox (
      workspace_id, project_id, workflow_run_id, workflow_event_id,
      destination, event_type, deduplication_key, payload_hash, payload
    ) values (
      new.workspace_id, new.project_id, new.id, event_row.id,
      'temporal', 'workflow.siblings.cancelled',
      'terminal-siblings:' || new.id || ':' || new.revision,
      encode(extensions.digest(payload::text, 'sha256'), 'hex'), payload
    ) on conflict (destination, deduplication_key) do nothing;
  end if;
  return new;
end;
$$;

create trigger task7_workflow_runs_close_terminal_siblings
after update on public.workflow_runs
for each row execute function public.motionly_close_terminal_workflow_siblings();

-- Keep the audited implementation private and expose a validating wrapper under
-- the stable RPC name. This preserves PostgREST compatibility without copying
-- the large transactional implementation.
alter function public.motionly_begin_workflow_step(
  uuid,uuid,uuid,text,text,text,text,integer,text,integer
) rename to motionly_begin_workflow_step_unchecked;

revoke all on function public.motionly_begin_workflow_step_unchecked(
  uuid,uuid,uuid,text,text,text,text,integer,text,integer
) from public, anon, authenticated, service_role;

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
  step public.workflow_steps;
begin
  select * into step
  from public.workflow_steps
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and workflow_run_id = p_workflow_run_id
    and step_key = p_step_key;
  if found and (
    step.step_kind <> p_step_kind
    or step.input_hash is distinct from p_input_hash
    or step.max_attempts <> p_max_attempts
  ) then
    raise exception 'WORKFLOW_STEP_IDEMPOTENCY_CONFLICT' using errcode = 'P0001';
  end if;
  return query
  select *
  from public.motionly_begin_workflow_step_unchecked(
    p_workspace_id,
    p_project_id,
    p_workflow_run_id,
    p_step_key,
    p_step_kind,
    p_attempt_key,
    p_lease_owner,
    p_lease_seconds,
    p_input_hash,
    p_max_attempts
  );
end;
$$;

revoke all on function public.motionly_begin_workflow_step(
  uuid,uuid,uuid,text,text,text,text,integer,text,integer
) from public, anon, authenticated;
grant execute on function public.motionly_begin_workflow_step(
  uuid,uuid,uuid,text,text,text,text,integer,text,integer
) to service_role;
