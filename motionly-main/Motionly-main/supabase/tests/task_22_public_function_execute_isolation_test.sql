-- Task 22 (follow-up): PUBLIC no longer has an ambient EXECUTE grant on the
-- application functions in schema `public`, while the roles that actually call
-- them keep their explicit EXECUTE. This is what makes the mock_gateway_role
-- (which only inherits PUBLIC's un-revokable USAGE on `public`) unable to run
-- any application function.
--
-- Contract: migration 202610190802 runs `revoke execute on function ... from
-- public` on every function in schema public; the explicit per-role grants are
-- untouched, so every existing caller keeps the access it already had.

begin;
select plan(5);

-- No function in `public` is EXECUTEable by PUBLIC any more.
select is(
  (select count(*)::int
   from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'public'
     and has_function_privilege('public', p.oid, 'EXECUTE')),
  0,
  'PUBLIC can no longer EXECUTE any function in schema public'
);

-- The existing callers keep EXECUTE on the functions they invoke. These four
-- functions span the exposed set (an RLS helper, a legacy mapper, two cache
-- invalidators) and must remain reachable by the roles that call them.
select ok(
  has_function_privilege('service_role', 'public.is_workspace_member(uuid)', 'EXECUTE')
  and has_function_privilege('authenticated', 'public.is_workspace_member(uuid)', 'EXECUTE')
  and has_function_privilege('anon', 'public.is_workspace_member(uuid)', 'EXECUTE')
  and has_function_privilege('motionly_worker', 'public.is_workspace_member(uuid)', 'EXECUTE'),
  'the application roles keep EXECUTE on is_workspace_member'
);
select ok(
  has_function_privilege('service_role', 'public.motionly_legacy_plan_timeline(jsonb)', 'EXECUTE'),
  'service_role keeps EXECUTE on motionly_legacy_plan_timeline'
);
select ok(
  has_function_privilege(
    'service_role', 'public.motionly_invalidate_stale_derivatives(uuid,uuid)', 'EXECUTE'),
  'service_role keeps EXECUTE on motionly_invalidate_stale_derivatives'
);
-- motionly_worker no longer holds a direct grant (202610230101): the
-- uploaded_videos trigger that calls it is SECURITY DEFINER, so it still runs
-- (task_18_worker_least_privilege_test.sql exercises that path as the worker).
select ok(
  has_function_privilege(
    'service_role', 'public.motionly_invalidate_on_media_replace(uuid)', 'EXECUTE'),
  'service_role keeps EXECUTE on motionly_invalidate_on_media_replace'
);

select * from finish();
rollback;
