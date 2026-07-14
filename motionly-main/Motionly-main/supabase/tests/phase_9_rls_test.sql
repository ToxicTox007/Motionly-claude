begin;
select plan(14);

insert into auth.users (id, email, raw_user_meta_data)
values
  ('10000000-0000-4000-8000-000000000001', 'owner@rls.test', '{}'::jsonb),
  ('10000000-0000-4000-8000-000000000002', 'member@rls.test', '{}'::jsonb),
  ('10000000-0000-4000-8000-000000000003', 'outsider@rls.test', '{}'::jsonb),
  ('10000000-0000-4000-8000-000000000004', 'disabled@rls.test', '{}'::jsonb);

insert into public.workspaces (id, name, owner_user_id)
values ('30000000-0000-4000-8000-000000000001', 'Shared RLS workspace', '10000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status)
values ('30000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'owner', 'active');
insert into public.workspace_members (workspace_id, user_id, role, status)
values ('30000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000002', 'member', 'active');
insert into public.workspace_members (workspace_id, user_id, role, status)
values ('30000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000004', 'member', 'disabled');

insert into public.projects (workspace_id, created_by, title)
values ('30000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'Owner project');

set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select is((select count(*)::integer from public.workspaces), 2, 'owner sees owned workspaces only');
select is((select count(*)::integer from public.projects where title = 'Owner project'), 1, 'owner sees own project');
select is((select count(*)::integer from public.profiles), 1, 'owner sees own profile only');

select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000002', true);
select is((select count(*)::integer from public.workspaces), 2, 'member sees own and joined workspaces');
select is((select count(*)::integer from public.projects where title = 'Owner project'), 1, 'member sees joined workspace project');
select throws_ok(
  $$insert into public.projects (workspace_id, created_by, title)
    values ('30000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000002', 'Member project')$$,
  '42501', null,
  'active member must create projects through the API'
);

select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000003', true);
select is((select count(*)::integer from public.projects), 0, 'non-member sees no foreign projects');
select throws_ok(
  $$insert into public.projects (workspace_id, created_by, title)
    values ('30000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000003', 'Forbidden')$$,
  '42501', null, 'non-member cannot create in foreign workspace'
);

select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000004', true);
select is((select count(*)::integer from public.projects), 0, 'disabled member sees no projects');
select is((select count(*)::integer from public.workspace_members), 1, 'disabled member sees only own active workspace membership');

select is(
  (
    select bool_and(not has_function_privilege('authenticated', procedure.oid, 'EXECUTE'))
    from pg_proc as procedure
    join pg_namespace as namespace on namespace.oid = procedure.pronamespace
    where namespace.nspname = 'public'
      and procedure.proname in ('motionly_create_active_edit_plan', 'motionly_activate_edit_plan')
  ),
  true,
  'authenticated clients cannot execute security-definer plan mutation RPCs'
);
select is(
  (
    select bool_and(has_function_privilege('service_role', procedure.oid, 'EXECUTE'))
    from pg_proc as procedure
    join pg_namespace as namespace on namespace.oid = procedure.pronamespace
    where namespace.nspname = 'public'
      and procedure.proname in ('motionly_create_active_edit_plan', 'motionly_activate_edit_plan')
  ),
  true,
  'service role retains plan mutation RPC access'
);

set local role service_role;
select is(
  (select count(*)::integer from public.projects where title in ('Owner project', 'Member project')),
  1,
  'service role sees the project created through the trusted setup path'
);
insert into public.worker_heartbeats (worker_id, worker_role, queues, version, metadata)
values ('worker-1', 'provider', array['planning'], 'test', '{}'::jsonb)
on conflict (worker_id) do update
set last_seen_at = now(), metadata = excluded.metadata;
select is(
  (select count(*)::integer from public.worker_heartbeats where worker_id = 'worker-1'),
  1,
  'service role reads operational tables'
);

select * from finish();
rollback;
