-- Restrict atomic plan mutation functions to the trusted API service role.
-- PostgreSQL grants function execution to PUBLIC by default, which would bypass RLS
-- because these routines intentionally run as SECURITY DEFINER.

do $$
declare
  target_function regprocedure;
begin
  for target_function in
    select procedure.oid::regprocedure
    from pg_proc as procedure
    join pg_namespace as namespace on namespace.oid = procedure.pronamespace
    where namespace.nspname = 'public'
      and procedure.proname in (
        'motionly_create_active_edit_plan',
        'motionly_activate_edit_plan'
      )
  loop
    execute format(
      'revoke all privileges on function %s from public, anon, authenticated',
      target_function
    );
    execute format(
      'grant execute on function %s to service_role',
      target_function
    );
  end loop;
end;
$$;
