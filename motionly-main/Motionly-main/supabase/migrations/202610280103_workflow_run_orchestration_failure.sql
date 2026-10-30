-- Worker audit WJ-06: when the Temporal side of a workflow failed (an
-- activity exhausted its retries, a step outcome never arrived, the plan
-- revision conflicted, or the execution closed), nothing wrote the canonical
-- run as failed. The run stayed non-terminal in the database forever and the
-- reconciler only "reported" it to a log nobody wrote.
--
-- motionly_fail_workflow_run() is the orchestration-side terminal transition:
-- idempotent (a run that is already terminal is returned unchanged), tenant
-- scoped, and service-role only. The existing terminal-transition trigger
-- cancels any still-active step attempts of the run.

begin;

create or replace function public.motionly_fail_workflow_run(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_error_code text,
  p_error_message text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
  run public.workflow_runs;
  seq integer;
  v_code text;
begin
  v_code := public.motionly_normalize_workflow_error_code(
    coalesce(nullif(btrim(p_error_code), ''), 'orchestration_failed')
  );
  select * into run
  from public.workflow_runs
  where id = p_workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002';
  end if;
  if run.state in ('export_ready', 'failed', 'cancelled', 'superseded') then
    return jsonb_build_object('run', to_jsonb(run), 'changed', false);
  end if;

  update public.workflow_runs
  set state = 'failed',
      failure_code = left(v_code, 120),
      failure_message = left(
        coalesce(nullif(btrim(p_error_message), ''), 'The workflow stopped before it finished.'),
        500
      ),
      revision = revision + 1
  where id = run.id
  returning * into run;

  select coalesce(max(sequence_number), 0) + 1 into seq
  from public.workflow_events
  where workflow_run_id = run.id;
  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, sequence_number,
    event_type, event_key, payload
  ) values (
    run.workspace_id, run.project_id, run.id, seq,
    'workflow.run.failed',
    'run-failed:' || run.id::text,
    jsonb_build_object(
      'workflow_run_id', run.id,
      'error_code', left(v_code, 120),
      'source', 'orchestration'
    )
  )
  on conflict (workspace_id, project_id, workflow_run_id, event_key) do nothing;

  return jsonb_build_object('run', to_jsonb(run), 'changed', true);
end;
$$;

revoke all on function public.motionly_fail_workflow_run(uuid, uuid, uuid, text, text)
  from public, anon, authenticated;
grant execute on function public.motionly_fail_workflow_run(uuid, uuid, uuid, text, text)
  to service_role;

commit;
