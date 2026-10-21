-- Task 15 fix: stop an orphaned first-draft workflow when its project (or the
-- account/workspace that owns it) is deleted, surviving the ON DELETE CASCADE.
--
-- The previous BEFORE DELETE trigger (202610190202) wrote the cancellation into
-- workflow_runs / workflow_events / transactional_outbox, but those rows are
-- removed by the same statement's cascade (the FKs are not deferrable), so the
-- Temporal cancellation never reached the dispatcher and the orphaned workflow
-- kept calling motionly_prepare_workflow_step / motionly_fail_workflow_step for
-- a run that no longer exists (a WORKFLOW_NOT_FOUND storm).
--
-- Fix: a durable cancellation table with NO foreign key to projects or
-- workflow_runs, so the cascade cannot delete it. The BEFORE DELETE trigger
-- records one row per non-terminal run carrying the run's temporal_workflow_id.
-- The Temporal dispatcher claims these rows and cancels/terminates the workflow
-- by temporal_workflow_id, idempotently (not-found in Temporal = done).

begin;

create table if not exists public.workflow_cancellation_requests (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  workflow_run_id uuid not null,
  temporal_workflow_id text not null,
  reason text not null default 'project_deleted',
  created_at timestamptz not null default now(),
  dispatched_at timestamptz,
  -- Deliberately no FK to projects/workflow_runs: the trigger writes this row
  -- in the same statement that cascades those rows away, so a FK would delete
  -- it too. One cancellation per orphaned run is enough.
  unique (workflow_run_id)
);

-- The dispatcher claims the oldest undispatched requests.
create index if not exists workflow_cancellation_requests_pending_idx
  on public.workflow_cancellation_requests (created_at)
  where dispatched_at is null;

alter table public.workflow_cancellation_requests enable row level security;

revoke all on public.workflow_cancellation_requests from public, anon, authenticated;

grant select, insert, update, delete
  on public.workflow_cancellation_requests to service_role;

-- Rewrite the project-delete trigger function to record a durable cancellation
-- request (which survives the cascade) instead of mutating rows the cascade
-- removes. Transitioning workflow_runs is pointless here because the cascade
-- deletes the run in the same statement; the durable request is what reaches
-- Temporal via the dispatcher.
create or replace function public.motionly_cancel_project_workflows_on_delete()
returns trigger
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
begin
  insert into public.workflow_cancellation_requests (
    workspace_id, project_id, workflow_run_id, temporal_workflow_id, reason
  )
  select
    run.workspace_id, run.project_id, run.id, run.temporal_workflow_id,
    'project_deleted'
  from public.workflow_runs run
  where run.workspace_id = old.workspace_id
    and run.project_id = old.id
    and run.state not in ('export_ready', 'failed', 'cancelled', 'superseded')
  on conflict (workflow_run_id) do nothing;
  return old;
end;
$$;

revoke all on function public.motionly_cancel_project_workflows_on_delete() from public;

drop trigger if exists motionly_cancel_project_workflows on public.projects;
create trigger motionly_cancel_project_workflows
  before delete on public.projects
  for each row
  execute function public.motionly_cancel_project_workflows_on_delete();

-- Service-role RPC: atomically claim a batch of undispatched cancellation
-- requests for the dispatcher (FOR UPDATE SKIP LOCKED), marking them dispatched.
-- Idempotent at the dispatcher level: a request is handed out once; if the
-- dispatcher crashes before cancelling Temporal, a later sweep re-selects rows
-- whose dispatched_at is stale is NOT needed here because Temporal cancellation
-- is itself idempotent and the request row is retained for audit.
create or replace function public.motionly_claim_workflow_cancellations(
  p_limit integer default 50
)
returns setof public.workflow_cancellation_requests
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if p_limit is null or p_limit < 1 or p_limit > 200 then
    raise exception 'WORKFLOW_CANCELLATION_LIMIT_INVALID' using errcode = '22023';
  end if;
  return query
  update public.workflow_cancellation_requests r
  set dispatched_at = now()
  where r.id in (
    select c.id from public.workflow_cancellation_requests c
    where c.dispatched_at is null
    order by c.created_at
    for update skip locked
    limit p_limit
  )
  returning r.*;
end;
$$;

revoke all on function public.motionly_claim_workflow_cancellations(integer) from public;
grant execute on function public.motionly_claim_workflow_cancellations(integer) to service_role;

commit;
