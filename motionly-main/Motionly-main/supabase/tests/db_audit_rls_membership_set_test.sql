-- Database audit DB-22 (migration 202610280204): tenant RLS policies use the
-- once-per-query membership set and keep the same isolation.

begin;
select plan(8);

select is(
  (select count(*)::int from pg_policies
   where schemaname = 'public' and (qual ~ 'is_workspace_member' or with_check ~ 'is_workspace_member')),
  0, 'no tenant policy calls is_workspace_member per row'
);
select is(
  (select count(*)::int from pg_policies
   where schemaname = 'public' and qual ~ 'motionly_member_workspace_ids'),
  45, 'all 45 tenant read policies use the membership set'
);
select ok(
  has_function_privilege('authenticated', 'public.motionly_member_workspace_ids()', 'EXECUTE')
  and not has_function_privilege('anon', 'public.motionly_member_workspace_ids()', 'EXECUTE'),
  'only signed-in users can evaluate the membership set'
);

insert into auth.users (id, email, raw_user_meta_data) values
  ('0c000000-0000-4000-8000-000000000001', 'rls-a@test.local', '{}'),
  ('0c000000-0000-4000-8000-000000000002', 'rls-b@test.local', '{}');
insert into public.workspaces (id, name, owner_user_id) values
  ('0c000000-0000-4000-8000-0000000000a1', 'RLS A', '0c000000-0000-4000-8000-000000000001'),
  ('0c000000-0000-4000-8000-0000000000a2', 'RLS B', '0c000000-0000-4000-8000-000000000002');
insert into public.workspace_members (workspace_id, user_id, role, status) values
  ('0c000000-0000-4000-8000-0000000000a1', '0c000000-0000-4000-8000-000000000001', 'owner', 'active'),
  ('0c000000-0000-4000-8000-0000000000a2', '0c000000-0000-4000-8000-000000000002', 'owner', 'active'),
  ('0c000000-0000-4000-8000-0000000000a2', '0c000000-0000-4000-8000-000000000001', 'member', 'invited')
on conflict do nothing;
insert into public.projects (id, workspace_id, created_by, title) values
  ('0c000000-0000-4000-8000-0000000000b1', '0c000000-0000-4000-8000-0000000000a1', '0c000000-0000-4000-8000-000000000001', 'A'),
  ('0c000000-0000-4000-8000-0000000000b2', '0c000000-0000-4000-8000-0000000000a2', '0c000000-0000-4000-8000-000000000002', 'B');

set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"0c000000-0000-4000-8000-000000000001","role":"authenticated"}', true);
select is(
  (select array_agg(id order by id) from public.projects
   where id in ('0c000000-0000-4000-8000-0000000000b1', '0c000000-0000-4000-8000-0000000000b2')),
  array['0c000000-0000-4000-8000-0000000000b1'::uuid],
  'a member reads its own workspace''s projects and not another workspace''s'
);
select is(
  (select count(*)::int from public.workspaces where id = '0c000000-0000-4000-8000-0000000000a2'),
  0, 'an invited (not active) membership grants nothing'
);
reset role;

update auth.users set banned_until = now() + interval '1 day'
where id = '0c000000-0000-4000-8000-000000000001';
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"0c000000-0000-4000-8000-000000000001","role":"authenticated"}', true);
select is(
  (select count(*)::int from public.projects where id = '0c000000-0000-4000-8000-0000000000b1'),
  0, 'a banned user reads nothing'
);
reset role;

set local role authenticated;
select set_config('request.jwt.claims', '{"role":"authenticated"}', true);
select is(
  (select count(*)::int from public.projects), 0,
  'a request without a user reads nothing'
);
reset role;

-- The membership set is evaluated once per query (a subplan), not per row.
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"0c000000-0000-4000-8000-000000000002","role":"authenticated"}', true);
create temporary table db_audit_plan (line text);
grant insert, select on db_audit_plan to authenticated;
do $$
declare
  plan_line text;
begin
  for plan_line in execute 'explain select * from public.projects where workspace_id = ''0c000000-0000-4000-8000-0000000000a2''' loop
    insert into db_audit_plan values (plan_line);
  end loop;
end
$$;
select ok(
  exists (select 1 from db_audit_plan where line ~ 'SubPlan|InitPlan')
  and not exists (select 1 from db_audit_plan where line ~ 'is_workspace_member'),
  'the projects read plan evaluates membership as a subplan'
);
reset role;

select * from finish();
rollback;
