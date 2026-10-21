begin;
select plan(18);

-- Table exists with RLS on.
select has_table('public', 'workspace_invitations', 'workspace invitation ledger exists');
select ok(
  (select relrowsecurity from pg_class where oid = 'public.workspace_invitations'::regclass),
  'workspace_invitations enforces RLS'
);

-- Invites cascade from their owning workspace.
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.workspace_invitations'::regclass
      and contype = 'f'
      and confrelid = 'public.workspaces'::regclass
  ),
  'invitations cascade from their owning workspace'
);

-- Only a fingerprint (64-hex SHA-256) is stored: a check constraint enforces it
-- and no column could hold the raw token.
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.workspace_invitations'::regclass
      and pg_get_constraintdef(oid) like '%token_digest%0-9a-f%64%'
  ),
  'token_digest stores a SHA-256 fingerprint, never the raw token'
);
select ok(
  not exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'workspace_invitations'
      and column_name in ('token', 'secret', 'raw_token', 'invite_token')
  ),
  'no column stores the raw invite token'
);

-- The invite role can never mint an owner.
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.workspace_invitations'::regclass
      and pg_get_constraintdef(oid) like '%role%admin%member%'
  ),
  'invite role is constrained to admin/member (never owner)'
);

-- Browser roles cannot read invitations directly.
select ok(
  not has_table_privilege('authenticated', 'public.workspace_invitations', 'SELECT')
    and not has_table_privilege('anon', 'public.workspace_invitations', 'SELECT'),
  'browser roles cannot read invitations directly'
);
select ok(
  has_table_privilege('service_role', 'public.workspace_invitations', 'INSERT')
    and has_table_privilege('service_role', 'public.workspace_invitations', 'UPDATE')
    and has_table_privilege('service_role', 'public.workspace_invitations', 'DELETE'),
  'service role retains the API-mediated invitation path'
);

-- The seat-safe RPCs exist and run as SECURITY DEFINER.
select has_function('public', 'motionly_create_workspace_invitation', 'create-invite RPC exists');
select has_function('public', 'motionly_accept_workspace_invitation', 'accept-invite RPC exists');
select ok(
  (select prosecdef from pg_proc where proname = 'motionly_create_workspace_invitation'),
  'create-invite RPC is SECURITY DEFINER'
);
select ok(
  (select prosecdef from pg_proc where proname = 'motionly_accept_workspace_invitation'),
  'accept-invite RPC is SECURITY DEFINER'
);

-- Only service_role may execute the mutation RPCs (not anon/authenticated).
select ok(
  has_function_privilege(
    'service_role',
    'public.motionly_create_workspace_invitation(uuid, text, public.workspace_role,'
      || ' uuid, timestamptz, integer)',
    'EXECUTE'
  )
    and not has_function_privilege(
      'authenticated',
      'public.motionly_create_workspace_invitation(uuid, text, public.workspace_role,'
        || ' uuid, timestamptz, integer)',
      'EXECUTE'
    ),
  'only service_role may execute create-invite'
);

-- ---------------------------------------------------------------------------
-- Behavioural fixtures: a workspace with an owner at a two-seat limit.
-- ---------------------------------------------------------------------------
insert into auth.users (id, email)
values
  ('00000000-0000-4000-8000-000000000a01', 'owner20@example.test'),
  ('00000000-0000-4000-8000-000000000a02', 'invitee20@example.test'),
  ('00000000-0000-4000-8000-000000000a03', 'second20@example.test')
on conflict (id) do nothing;

insert into public.workspaces (id, name, owner_user_id)
values ('00000000-0000-4000-8000-0000000000b1', 'Seat WS', '00000000-0000-4000-8000-000000000a01');
insert into public.workspace_members (workspace_id, user_id, role, status)
values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-000000000a01',
        'owner', 'active');

-- Create an invite within a 2-seat budget (1 active owner + 0 pending < 2): ok.
select lives_ok(
  $$ select public.motionly_create_workspace_invitation(
       '00000000-0000-4000-8000-0000000000b1',
       repeat('a', 64),
       'member',
       '00000000-0000-4000-8000-000000000a01',
       now() + interval '7 days',
       2) $$,
  'owner can create an invite inside the seat budget'
);

-- A second invite now hits the limit (1 active + 1 pending = 2 >= 2): refused.
select throws_like(
  $$ select public.motionly_create_workspace_invitation(
       '00000000-0000-4000-8000-0000000000b1',
       repeat('b', 64),
       'member',
       '00000000-0000-4000-8000-000000000a01',
       now() + interval '7 days',
       2) $$,
  '%SEAT_LIMIT_REACHED%',
  'a second invite over the seat limit is refused'
);

-- Accepting the first invite makes the invitee an active member with the role.
select lives_ok(
  $$ select public.motionly_accept_workspace_invitation(
       repeat('a', 64),
       '00000000-0000-4000-8000-000000000a02',
       2) $$,
  'invitee accepts and becomes a member'
);
select ok(
  exists (
    select 1 from public.workspace_members
    where workspace_id = '00000000-0000-4000-8000-0000000000b1'
      and user_id = '00000000-0000-4000-8000-000000000a02'
      and role = 'member'
      and status = 'active'
  ),
  'accepted invitee is an active member with the invite role'
);

-- The last owner can never leave without transferring.
select throws_like(
  $$ select public.motionly_leave_workspace(
       '00000000-0000-4000-8000-0000000000b1',
       '00000000-0000-4000-8000-000000000a01') $$,
  '%LAST_OWNER_PROTECTED%',
  'the last owner cannot leave without transferring'
);

select * from finish();
rollback;
