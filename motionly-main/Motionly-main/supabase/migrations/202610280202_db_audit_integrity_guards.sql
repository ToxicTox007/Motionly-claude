-- Database audit integrity fixes.
--
-- DB-05  Deleting an account deleted every workspace the account owned,
--        including team workspaces and the projects other members created
--        there (auth.users -> motionly_delete_account_workspaces ->
--        workspaces cascade). Now, per owned workspace:
--          * another ACTIVE owner exists  -> ownership passes to them;
--          * other active members, no other owner -> the delete is refused
--            (ACCOUNT_OWNS_SHARED_WORKSPACE): transfer ownership first, with
--            motionly_transfer_workspace_ownership;
--          * nobody else is active -> the workspace and its history are
--            deleted, exactly as before (a personal workspace).
-- DB-11  The render terminal-state guard let a 'complete' render move to any
--        status (only failed/canceled were protected), and it also refused
--        the product's own retry path (failed/canceled -> queued, used by the
--        render retry endpoints and the worker retry in services/render_jobs.py).
--        A finished render now never changes status; a failed or canceled
--        render may only be requeued.
-- DB-13  Every workflow-produced export is linked by an append-only
--        workflow_run_artifacts row whose NO ACTION foreign key blocked the
--        export's deletion, so retention could never delete those exports
--        (after deleting their file). The link now follows its export: it is
--        removed with the export, and only then (the history stays append-only
--        while the export exists).
-- DB-23  profiles.email was copied at sign-up and never followed an email
--        change in auth.users; billing_adjustments.amount_paise was the only
--        paise amount stored as integer (orders/refunds/subscriptions use
--        bigint).
--
-- Forward-only. Covered by supabase/tests/db_audit_integrity_guards_test.sql.

begin;

-- ---------------------------------------------------------------------------
-- DB-05: account deletion never removes a workspace other people still use.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_delete_account_workspaces()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  owned record;
  successor uuid;
begin
  set constraints all deferred;
  for owned in
    select w.id
    from public.workspaces w
    where w.owner_user_id = old.id
    order by w.id
    for update
  loop
    select m.user_id into successor
    from public.workspace_members m
    where m.workspace_id = owned.id
      and m.user_id <> old.id
      and m.status = 'active'
      and m.role = 'owner'
    order by m.created_at, m.user_id
    limit 1;

    if successor is not null then
      update public.workspaces
      set owner_user_id = successor,
          updated_at = now()
      where id = owned.id;
      continue;
    end if;

    if exists (
      select 1
      from public.workspace_members m
      where m.workspace_id = owned.id
        and m.user_id <> old.id
        and m.status = 'active'
    ) then
      raise exception 'ACCOUNT_OWNS_SHARED_WORKSPACE: transfer ownership of workspace % before deleting this account', owned.id
        using errcode = 'P0001';
    end if;

    delete from public.workspaces where id = owned.id;
  end loop;
  return old;
end;
$$;
revoke all on function public.motionly_delete_account_workspaces() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- DB-11: complete is final; failed/canceled may only be requeued.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_guard_render_terminal_state()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if new.status is distinct from old.status then
    if old.status::text in ('complete', 'succeeded') then
      raise exception 'RENDER_TERMINAL_STATE' using errcode = 'P0001';
    end if;
    if old.status::text in ('failed', 'canceled') and new.status::text <> 'queued' then
      raise exception 'RENDER_TERMINAL_STATE' using errcode = 'P0001';
    end if;
  end if;
  return new;
end;
$$;
revoke all on function public.motionly_guard_render_terminal_state()
  from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- DB-13: an export's workflow artifact link goes with the export.
-- ---------------------------------------------------------------------------
alter table public.workflow_run_artifacts
  drop constraint task5_workflow_artifact_export_fk,
  add constraint task5_workflow_artifact_export_fk
    foreign key (workspace_id, project_id, export_id)
    references public.exports (workspace_id, project_id, id)
    on delete cascade
    deferrable;

create or replace function public.motionly_protect_workflow_run_artifact()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if tg_op = 'DELETE' then
    -- The project is being deleted (same rule as every history table).
    if not exists (select 1 from public.projects where id = old.project_id) then
      return old;
    end if;
    -- The export this link points to has been deleted (retention).
    if old.artifact_type = 'draft_export'
       and not exists (select 1 from public.exports where id = old.export_id)
    then
      return old;
    end if;
  end if;
  raise exception 'IMMUTABLE_RECORD: % rows are append-only', tg_table_name
    using errcode = '55000';
end;
$$;
revoke all on function public.motionly_protect_workflow_run_artifact() from public, anon, authenticated;

drop trigger if exists task5_workflow_artifacts_append_only on public.workflow_run_artifacts;
create trigger task5_workflow_artifacts_append_only
before delete or update on public.workflow_run_artifacts
for each row execute function public.motionly_protect_workflow_run_artifact();

-- ---------------------------------------------------------------------------
-- DB-23: profile email follows the account email; paise amounts are bigint.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_sync_profile_email()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if new.email is not null and new.email is distinct from old.email then
    update public.profiles
    set email = new.email
    where id = new.id
      and email is distinct from new.email;
  end if;
  return new;
end;
$$;
revoke all on function public.motionly_sync_profile_email() from public, anon, authenticated;

drop trigger if exists motionly_sync_profile_email on auth.users;
create trigger motionly_sync_profile_email
after update of email on auth.users
for each row execute function public.motionly_sync_profile_email();

update public.profiles p
set email = u.email
from auth.users u
where u.id = p.id
  and u.email is not null
  and p.email is distinct from u.email;

alter table public.billing_adjustments
  alter column amount_paise type bigint;

commit;
