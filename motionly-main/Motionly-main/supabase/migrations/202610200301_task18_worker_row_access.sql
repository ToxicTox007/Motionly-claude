-- Task 18 follow-up: let the restricted worker role see the rows it processes.
--
-- motionly_worker (202610190501) has table privileges in public, but it does not
-- bypass row-level security, and every RLS policy on the job tables names only
-- `authenticated`. The worker therefore saw no rows at all: preprocessing reported
-- upload_not_found for every upload on the CPU stack. Workers process jobs for
-- every workspace (as they did with the service-role key), so each RLS-protected
-- table in public gets a policy for the worker, except the credential and
-- identity tables no worker reads or writes. The role still has no auth schema,
-- is not a member of service_role, and storage stays scoped to its four buckets
-- by motionly_worker_bucket_scope. Granting BYPASSRLS instead would also lift
-- that storage scope.
--
-- New RLS tables need their own worker policy; the coverage assertion in
-- supabase/tests/task_18_worker_row_access_test.sql fails until they have one.

do $$
declare
  target record;
begin
  for target in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind in ('r', 'p')
      and c.relrowsecurity
      and c.relname not in (
        'admin_audit_events', 'password_reset_requests',
        'workspace_api_keys', 'workspace_invitations'
      )
  loop
    execute format('drop policy if exists motionly_worker_jobs on public.%I', target.relname);
    execute format(
      'create policy motionly_worker_jobs on public.%I for all to motionly_worker '
      'using (true) with check (true)',
      target.relname
    );
  end loop;
end
$$;
