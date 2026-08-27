-- Task 7 final audit fix: serialize replay identity validation with first claim.

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
  if p_step_key is null
     or p_step_kind is null
     or p_attempt_key is null
     or p_lease_owner is null
     or p_max_attempts is null
  then
    raise exception 'WORKFLOW_STEP_ARGUMENT_REQUIRED' using errcode = '22023';
  end if;

  -- The unchecked implementation locks this same aggregate. Taking the lock
  -- before identity lookup closes the concurrent first-claim check/use gap;
  -- PostgreSQL retains it through the delegated transactional implementation.
  perform 1
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
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and workflow_run_id = p_workflow_run_id
    and step_key = p_step_key;
  if found and (
    step.step_kind is distinct from p_step_kind
    or step.input_hash is distinct from p_input_hash
    or step.max_attempts is distinct from p_max_attempts
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
