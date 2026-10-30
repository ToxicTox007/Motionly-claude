-- Database audit DB-22: tenant RLS policies evaluate membership once per
-- query instead of once per row.
--
-- The 45 tenant SELECT policies were `using (is_workspace_member(workspace_id))`.
-- is_workspace_member is a SECURITY DEFINER function (so it cannot be inlined)
-- that takes the row's workspace id, so PostgreSQL called it for every row the
-- query touched. Measured on a 5,000-row read: 13.6 ms without RLS, 205-249 ms
-- with it. The policies now compare the row's workspace id with the caller's
-- active memberships, computed once per query (a hashed subplan): 41 ms on the
-- same read.
--
-- Same rule as before: an ACTIVE membership of a user whose access is not
-- suspended (banned or deleted). No caller (auth.uid() is null) -> no rows.
-- is_workspace_member(uuid) is unchanged and still used by Realtime topic
-- authorization and by the API.
--
-- Forward-only. Covered by supabase/tests/db_audit_rls_membership_set_test.sql
-- and by the existing tenancy suites (phase_0_tenancy_rls_test.sql,
-- phase_9_rls_test.sql, user_access_revocation_test.sql,
-- task_3_tenant_boundary_test.sql).

begin;

create or replace function public.motionly_member_workspace_ids()
returns setof uuid
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select wm.workspace_id
  from public.workspace_members wm
  where wm.user_id = auth.uid()
    and wm.status = 'active'
    and not public.motionly_user_access_suspended(auth.uid());
$$;

revoke all on function public.motionly_member_workspace_ids() from public, anon, authenticated;
grant execute on function public.motionly_member_workspace_ids() to authenticated;

do $$
declare
  pol record;
  column_name text;
begin
  for pol in
    select p.schemaname, p.tablename, p.policyname, p.qual
    from pg_policies p
    where p.schemaname = 'public'
      and p.cmd = 'SELECT'
      and p.with_check is null
      and p.qual in ('is_workspace_member(workspace_id)', 'is_workspace_member(id)')
  loop
    column_name := case pol.qual when 'is_workspace_member(id)' then 'id' else 'workspace_id' end;
    execute format(
      'alter policy %I on %I.%I using (%I in (select public.motionly_member_workspace_ids()))',
      pol.policyname, pol.schemaname, pol.tablename, column_name
    );
  end loop;
end
$$;

commit;
