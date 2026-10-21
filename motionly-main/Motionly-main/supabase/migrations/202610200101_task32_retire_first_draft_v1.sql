-- Task 32: retire the first-draft-v1 workflow. first-draft-v2 is the only live
-- first-draft workflow. Forward-only: existing first-draft-v1 workflow_runs and
-- their immutable event/outbox history are NOT rewritten and remain fully
-- readable, updatable (revision bumps, terminalization) and replayable. Only the
-- creation of NEW first-draft-v1 runs is refused from this migration onward.

-- New first-draft runs default to the live v2 contract.
alter table public.workflow_runs
  alter column workflow_version set default 'first-draft-v2';

-- A BEFORE INSERT guard (not a CHECK constraint) refuses new v1 runs while
-- leaving every existing row and its subsequent UPDATEs untouched. A CHECK
-- constraint -- even NOT VALID -- would also reject the frequent revision-bump
-- UPDATEs that existing v1 rows still receive, so a trigger scoped to INSERT is
-- the correct, history-preserving guard.
create or replace function public.motionly_refuse_retired_workflow_version()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  if new.workflow_type = 'first_draft' and new.workflow_version = 'first-draft-v1' then
    raise exception 'WORKFLOW_VERSION_RETIRED: first-draft-v1 is retired; start first-draft-v2'
      using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger task32_workflow_runs_refuse_retired_version
before insert on public.workflow_runs
for each row execute function public.motionly_refuse_retired_workflow_version();

-- Trigger functions execute as the trigger owner, so no caller needs EXECUTE.
-- Match the public-schema isolation contract (202610190802): PUBLIC must not
-- hold an ambient EXECUTE grant on any application function in schema public.
revoke execute on function public.motionly_refuse_retired_workflow_version()
  from public;
