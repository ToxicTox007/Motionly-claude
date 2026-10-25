-- Banned users lose workspace access immediately (202610230201).
--
-- An access token issued before a ban stays valid until it expires, so the
-- RLS helper (browser reads, Realtime) and the API's role resolver must both
-- refuse a banned or deleted user themselves.
begin;
select plan(11);

insert into auth.users (id, email, raw_user_meta_data) values
  ('2e000000-0000-4000-8000-0000000000e1', 'ban-owner@test.local', '{}'),
  ('2e000000-0000-4000-8000-0000000000e2', 'ban-member@test.local', '{}');
-- handle_new_user gave each user a workspace; add the second user to the first's.
insert into public.workspace_members (workspace_id, user_id, role, status)
select w.id, '2e000000-0000-4000-8000-0000000000e2', 'admin', 'active'
from public.workspaces w
where w.owner_user_id = '2e000000-0000-4000-8000-0000000000e1';
select set_config(
  'motionly_test.workspace',
  (select id::text from public.workspaces
   where owner_user_id = '2e000000-0000-4000-8000-0000000000e1'),
  true
);

-- The API's resolver, before the ban.
select is(
  (select role::text from public.motionly_workspace_member_role(
    current_setting('motionly_test.workspace')::uuid,
    '2e000000-0000-4000-8000-0000000000e2')),
  'admin',
  'an active member resolves to their role'
);
select is(
  (select count(*)::integer from public.motionly_workspace_member_role(
    current_setting('motionly_test.workspace')::uuid,
    '2e000000-0000-4000-8000-0000000000aa')),
  0,
  'a non-member resolves to no row'
);

-- Browser access with the member's token, before the ban.
create function pg_temp.reads_as(user_id uuid) returns integer language plpgsql as $$
declare
  seen integer;
begin
  -- Both forms auth.uid() has read across Supabase versions.
  perform set_config('request.jwt.claim.sub', user_id::text, true);
  perform set_config('request.jwt.claims',
    json_build_object('sub', user_id, 'role', 'authenticated')::text, true);
  set local role authenticated;
  select count(*) into seen from public.workspaces
  where id = current_setting('motionly_test.workspace')::uuid;
  reset role;
  return seen;
end;
$$;
select is(pg_temp.reads_as('2e000000-0000-4000-8000-0000000000e2'), 1,
  'before the ban the member can read the workspace through RLS');

-- Ban (what the admin ban route asks GoTrue to do).
update auth.users set banned_until = now() + interval '100 years'
where id = '2e000000-0000-4000-8000-0000000000e2';

select is(
  (select count(*)::integer from public.motionly_workspace_member_role(
    current_setting('motionly_test.workspace')::uuid,
    '2e000000-0000-4000-8000-0000000000e2')),
  0,
  'a banned member resolves to no row'
);
select is(pg_temp.reads_as('2e000000-0000-4000-8000-0000000000e2'), 0,
  'a banned member can no longer read the workspace with an unexpired token');
select is(pg_temp.reads_as('2e000000-0000-4000-8000-0000000000e1'), 1,
  'other members are unaffected');

-- An expired ban no longer applies.
update auth.users set banned_until = now() - interval '1 minute'
where id = '2e000000-0000-4000-8000-0000000000e2';
select is(
  (select role::text from public.motionly_workspace_member_role(
    current_setting('motionly_test.workspace')::uuid,
    '2e000000-0000-4000-8000-0000000000e2')),
  'admin',
  'after the ban ends (unban) the member resolves again'
);

-- Soft-deleted users are refused the same way.
update auth.users set deleted_at = now()
where id = '2e000000-0000-4000-8000-0000000000e2';
select is(pg_temp.reads_as('2e000000-0000-4000-8000-0000000000e2'), 0,
  'a soft-deleted user can no longer read the workspace');

-- Inactive memberships stay refused.
update auth.users set deleted_at = null
where id = '2e000000-0000-4000-8000-0000000000e2';
update public.workspace_members set status = 'disabled'
where user_id = '2e000000-0000-4000-8000-0000000000e2'
  and workspace_id = current_setting('motionly_test.workspace')::uuid;
select is(
  (select count(*)::integer from public.motionly_workspace_member_role(
    current_setting('motionly_test.workspace')::uuid,
    '2e000000-0000-4000-8000-0000000000e2')),
  0,
  'a disabled membership resolves to no row'
);

-- Only the API may ask.
select ok(
  has_function_privilege('service_role', 'public.motionly_workspace_member_role(uuid,uuid)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'public.motionly_workspace_member_role(uuid,uuid)', 'EXECUTE')
  and not has_function_privilege('anon', 'public.motionly_workspace_member_role(uuid,uuid)', 'EXECUTE')
  and not has_function_privilege('motionly_worker', 'public.motionly_workspace_member_role(uuid,uuid)', 'EXECUTE'),
  'only service_role may resolve workspace roles'
);
select ok(
  not has_function_privilege('authenticated', 'public.motionly_user_access_suspended(uuid)', 'EXECUTE')
  and not has_function_privilege('anon', 'public.motionly_user_access_suspended(uuid)', 'EXECUTE')
  and not has_function_privilege('motionly_worker', 'public.motionly_user_access_suspended(uuid)', 'EXECUTE'),
  'browser roles and the worker cannot probe whether a user is banned'
);

select * from finish();
rollback;
