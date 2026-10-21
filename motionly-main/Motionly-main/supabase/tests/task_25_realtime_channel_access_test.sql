-- pgTAP for migration 202610191101 (Realtime private-channel authorization).
--
-- Supabase Realtime authorizes a private topic by evaluating the RLS policies on
-- realtime.messages as the connecting role with the user's JWT claims loaded.
-- The policies delegate to realtime.motionly_can_access_topic(topic), which maps
-- `workspace:<id>` / `project:<id>` to a workspace and reuses
-- public.is_workspace_member(uuid) (active membership of auth.uid()). We exercise
-- that decision function directly under each role/identity the policies run as:
--   * an ACTIVE member of the topic's workspace may join/broadcast (true),
--   * a member of a DIFFERENT workspace may not (false),
--   * the anon role (signed-out visitor, no auth.uid()) may not (false).
-- The two policies and their grants are asserted structurally so the join
-- (SELECT) and broadcast/presence (INSERT) paths both exist on realtime.messages.

begin;
select plan(13);

-- Realtime creates its own schema at startup; this contract requires it present.
select has_schema('realtime', 'realtime schema exists (created by the Realtime service)');
select has_table('realtime', 'messages', 'realtime.messages exists');

select has_function(
  'realtime',
  'motionly_can_access_topic',
  array['text'],
  'topic authorization helper exists'
);

-- The helper is SECURITY DEFINER so it can read membership as the owner while
-- still deciding on auth.uid() of the connecting user.
select ok(
  (select prosecdef from pg_proc where oid = 'realtime.motionly_can_access_topic(text)'::regprocedure),
  'motionly_can_access_topic is SECURITY DEFINER'
);

-- Both authorization policies exist on realtime.messages.
select ok(
  exists (
    select 1 from pg_policies
    where schemaname = 'realtime' and tablename = 'messages'
      and policyname = 'motionly_members_join_private_topics' and cmd = 'SELECT'
  ),
  'SELECT (join/receive) policy motionly_members_join_private_topics exists'
);
select ok(
  exists (
    select 1 from pg_policies
    where schemaname = 'realtime' and tablename = 'messages'
      and policyname = 'motionly_members_broadcast_private_topics' and cmd = 'INSERT'
  ),
  'INSERT (broadcast/presence) policy motionly_members_broadcast_private_topics exists'
);

-- The connecting browser roles can execute the decision helper.
select ok(
  has_function_privilege('authenticated', 'realtime.motionly_can_access_topic(text)', 'execute')
    and has_function_privilege('anon', 'realtime.motionly_can_access_topic(text)', 'execute'),
  'authenticated and anon may execute the authorization helper'
);

-- Fixtures: two workspaces, a member in each, and a project in workspace A.
-- Inserted as the default (superuser) role, the way the other pgTAP suites seed
-- auth.users and tenant tables.
insert into auth.users (id, email, raw_user_meta_data)
values
  ('a5000000-0000-4000-8000-000000000001', 'task25-a@example.test', '{}'::jsonb),
  ('a5000000-0000-4000-8000-000000000002', 'task25-b@example.test', '{}'::jsonb);

insert into public.workspaces (id, name, owner_user_id)
values
  ('a5000000-0000-4000-8000-0000000000a1', 'Task 25 Workspace A', 'a5000000-0000-4000-8000-000000000001'),
  ('a5000000-0000-4000-8000-0000000000b1', 'Task 25 Workspace B', 'a5000000-0000-4000-8000-000000000002');

insert into public.workspace_members (workspace_id, user_id, role, status)
values
  ('a5000000-0000-4000-8000-0000000000a1', 'a5000000-0000-4000-8000-000000000001', 'owner', 'active'),
  ('a5000000-0000-4000-8000-0000000000b1', 'a5000000-0000-4000-8000-000000000002', 'owner', 'active');

insert into public.projects (id, workspace_id, created_by, title)
values
  ('a5000000-0000-4000-8000-0000000000c1', 'a5000000-0000-4000-8000-0000000000a1',
   'a5000000-0000-4000-8000-000000000001', 'Task 25 project A');

-- --- Own-workspace member (user A) ---------------------------------------
set local role authenticated;
select set_config('request.jwt.claim.sub', 'a5000000-0000-4000-8000-000000000001', true);

select ok(
  realtime.motionly_can_access_topic('workspace:a5000000-0000-4000-8000-0000000000a1'),
  'active member of workspace A may use its workspace topic'
);
select ok(
  realtime.motionly_can_access_topic('project:a5000000-0000-4000-8000-0000000000c1'),
  'active member of workspace A may use a project topic scoped to workspace A'
);
-- Unknown / unscoped topics fall through to the default-deny branch even for a
-- valid, authenticated member: Motionly uses no public topics.
select ok(
  not realtime.motionly_can_access_topic('lobby:a5000000-0000-4000-8000-0000000000a1'),
  'an unknown topic kind is denied by default'
);

-- --- Other-workspace member (user B) -------------------------------------
reset role;
set local role authenticated;
select set_config('request.jwt.claim.sub', 'a5000000-0000-4000-8000-000000000002', true);

select ok(
  not realtime.motionly_can_access_topic('workspace:a5000000-0000-4000-8000-0000000000a1'),
  'a member of another workspace is denied workspace A''s topic'
);
select ok(
  not realtime.motionly_can_access_topic('project:a5000000-0000-4000-8000-0000000000c1'),
  'a member of another workspace is denied workspace A''s project topic'
);

-- --- Signed-out visitor (anon role, no auth.uid()) -----------------------
reset role;
set local role anon;
select set_config('request.jwt.claim.sub', '', true);

select ok(
  not realtime.motionly_can_access_topic('workspace:a5000000-0000-4000-8000-0000000000a1'),
  'the anon role (signed-out visitor) is denied every private topic'
);

reset role;
select * from finish();
rollback;
