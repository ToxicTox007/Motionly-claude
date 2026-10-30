-- Database audit DB-01 / DB-08: the browser-facing API roles (anon,
-- authenticated) reach only what the browser actually uses.
--
-- DB-01  Supabase's default privileges (pg_default_acl, owner postgres, schema
--        public) grant EXECUTE on every new function to anon and authenticated
--        explicitly. `revoke ... from public` does not remove those explicit
--        grants, so SECURITY DEFINER functions that were meant to be
--        service-role only stayed callable through PostgREST without any
--        credentials: motionly_invalidate_on_media_replace(uuid) and
--        motionly_invalidate_stale_derivatives(uuid, uuid) (marked any
--        project's renders/exports stale) and, before 202610280102,
--        motionly_claim_workflow_cancellations(integer). The only SECURITY
--        DEFINER functions the browser calls are is_workspace_member(uuid)
--        (every tenant RLS policy) and update_own_profile(...)
--        (apps/web/app/(app)/account/actions.ts). Every other SECURITY DEFINER
--        function in public loses its anon/authenticated EXECUTE grant.
--        Trigger functions keep firing: PostgreSQL checks EXECUTE on a trigger
--        function only when the trigger is created, not when it fires.
--        service_role, motionly_worker and postgres grants are untouched.
-- DB-08  The inference registry tables and music_mood_synonyms kept the
--        default "ALL to anon, authenticated" grant (including TRUNCATE, which
--        row level security does not cover). No browser code reads them; their
--        policies admit only service_role and motionly_worker.
--        The default privileges themselves are changed so a new table,
--        sequence or function in public is no longer born readable, writable
--        or callable by anon/authenticated: each migration grants the API roles
--        exactly what they need (tests/integration/test_schema_matches_code.py
--        compares those grants with what the code uses).
--
-- Forward-only. Covered by supabase/tests/db_audit_api_role_privileges_test.sql.

begin;

do $$
declare
  fn record;
begin
  for fn in
    select p.oid::regprocedure as signature
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.prosecdef
      and p.oid not in (
        'public.is_workspace_member(uuid)'::regprocedure,
        'public.update_own_profile(text, text, text)'::regprocedure
      )
      and (
        has_function_privilege('anon', p.oid, 'EXECUTE')
        or has_function_privilege('authenticated', p.oid, 'EXECUTE')
      )
  loop
    execute format('revoke execute on function %s from public, anon, authenticated', fn.signature);
  end loop;
end
$$;

-- update_own_profile is for signed-in users only; anon never had a use for it.
revoke execute on function public.update_own_profile(text, text, text) from public, anon;

revoke all on table
  public.ai_adapters,
  public.ai_models,
  public.ai_routes,
  public.ai_secrets,
  public.ai_registry_version,
  public.music_mood_synonyms
from anon, authenticated;

-- New objects created by migrations (owner postgres) start with no API-role
-- access; grants are explicit from now on.
alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke execute on functions from anon, authenticated;

commit;
