-- Task 15: let a failed workflow step be re-enqueued under a new BullMQ job id.
--
-- motionly_prepare_workflow_step is idempotent for the *same* transport: it
-- inserts the step on first call and, on replay with identical arguments,
-- returns the existing row. The original version treated ANY difference in
-- bullmq_job_id (or input_hash) as WORKFLOW_STEP_TRANSPORT_CONFLICT. That is
-- correct for a concurrent conflicting transport, but it also rejected the
-- legitimate retry path: when the previous attempt FAILED, the workflow's next
-- attempt has a brand-new job id, and the function refused to prepare it, so
-- first-draft steps could never be retried (the enqueue activity then looped
-- reporting a spurious "temporarily unavailable" error).
--
-- Forward-only fix: when the existing step is in a non-terminal, retryable
-- state (failed with attempts remaining, or queued/waiting) we accept a new
-- bullmq_job_id / input_hash and re-arm the step for the next attempt instead
-- of raising a conflict. step_kind / max_attempts / resource_id are identity
-- and must still match; a running step with a different job id is still a real
-- conflict.

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
  is_retryable boolean;
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

  -- Identity fields can never change for a given step_key.
  if step.step_kind is distinct from p_step_kind
     or step.max_attempts is distinct from p_max_attempts
     or step.resource_id is distinct from p_resource_id
  then
    raise exception 'WORKFLOW_STEP_TRANSPORT_CONFLICT' using errcode = 'P0001';
  end if;

  -- Same transport (idempotent replay): return as-is.
  if step.bullmq_job_id is not distinct from p_bullmq_job_id
     and step.input_hash is not distinct from p_input_hash
  then
    return next step;
    return;
  end if;

  -- A new transport is only acceptable when the previous attempt FAILED and
  -- the step still has attempts remaining. A queued/running step receiving a
  -- different job id is a genuine concurrent-transport conflict and still fails
  -- closed.
  is_retryable := step.state = 'failed' and step.attempt_count < step.max_attempts;
  if not is_retryable then
    raise exception 'WORKFLOW_STEP_TRANSPORT_CONFLICT' using errcode = 'P0001';
  end if;

  update public.workflow_steps
  set bullmq_job_id = p_bullmq_job_id,
      input_hash = p_input_hash,
      state = 'queued',
      last_error_code = null,
      last_error_message = null,
      completed_at = null,
      revision = step.revision + 1,
      updated_at = now()
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and workflow_run_id = p_workflow_run_id
    and step_key = p_step_key
  returning * into step;

  return next step;
end;
$$;
