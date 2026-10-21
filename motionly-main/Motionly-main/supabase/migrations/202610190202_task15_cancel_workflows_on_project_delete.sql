-- Task 15: cancel a project's running workflows when the project (or the
-- account that owns it) is deleted.
--
-- Deleting a project cascades its workflow_runs away, but an in-flight Temporal
-- workflow keeps calling motionly_prepare_workflow_step / motionly_fail_workflow_step
-- for a run that no longer exists, producing a storm of WORKFLOW_NOT_FOUND
-- errors across postgres, PostgREST and the worker. We stop the workflow at the
-- source: a BEFORE DELETE trigger on projects transitions each non-terminal
-- workflow run to 'cancelled' and emits the canonical workflow.cancelled event
-- plus a Temporal cancellation outbox record, exactly like
-- motionly_cancel_workflow_run, before the cascade removes the rows. Account
-- deletion deletes the workspace, which cascades to projects and fires this
-- same trigger per project.

create or replace function public.motionly_cancel_project_workflows_on_delete()
returns trigger
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
  run public.workflow_runs;
  event_row public.workflow_events;
  seq integer;
  payload jsonb;
  dedup text;
begin
  for run in
    select * from public.workflow_runs
    where workspace_id = old.workspace_id
      and project_id = old.id
      and state not in ('export_ready', 'failed', 'cancelled', 'superseded')
    for update
  loop
    update public.workflow_runs
    set state = 'cancelled', revision = revision + 1,
        completed_at = clock_timestamp(), current_step_key = null
    where id = run.id returning * into run;

    select coalesce(max(sequence_number), 0) + 1 into seq
    from public.workflow_events where workflow_run_id = run.id;
    payload := jsonb_build_object(
      'workflow_run_id', run.id, 'state', 'cancelled', 'reason', 'project_deleted'
    );
    dedup := 'workflow-cancelled:' || run.id || ':project-deleted';

    insert into public.workflow_events (
      workspace_id, project_id, workflow_run_id, sequence_number,
      event_type, event_key, payload
    ) values (
      run.workspace_id, run.project_id, run.id, seq,
      'workflow.cancelled', dedup, payload
    )
    on conflict do nothing
    returning * into event_row;

    if event_row.id is not null then
      insert into public.transactional_outbox (
        workspace_id, project_id, workflow_run_id, workflow_event_id,
        destination, event_type, deduplication_key, payload_hash, payload
      ) values (
        run.workspace_id, run.project_id, run.id, event_row.id,
        'temporal', 'workflow.cancelled', dedup,
        encode(extensions.digest(payload::text, 'sha256'), 'hex'), payload
      )
      on conflict do nothing;
    end if;
  end loop;
  return old;
end;
$$;

revoke all on function public.motionly_cancel_project_workflows_on_delete() from public;

drop trigger if exists motionly_cancel_project_workflows on public.projects;
create trigger motionly_cancel_project_workflows
  before delete on public.projects
  for each row
  execute function public.motionly_cancel_project_workflows_on_delete();
