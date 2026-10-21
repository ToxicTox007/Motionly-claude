-- Task 20: invite links & seat limits (server side).
--
-- Owners and admins mint invite links that grant a workspace role to whoever
-- redeems them, bounded by the workspace plan's seat limit. Only a SHA-256
-- fingerprint of each invite token is ever stored (like Task 16
-- export_share_links / Task 24 workspace_api_keys); the raw token lives in the
-- invite URL fragment and is never persisted or logged. Browser clients never
-- touch this table directly: RLS is on and only service_role (the API) may
-- read or write.
--
-- All seat accounting and membership mutation runs through SECURITY DEFINER
-- RPCs that take a per-workspace transaction advisory lock so two concurrent
-- requests can never over-allocate the last seat. The seat limit is passed in
-- by the API from the single plan source (Task 23 plan_limits.plan_seat_limit);
-- the database never second-guesses which plan a workspace is on.
--
-- Forward-only. Reserved timestamp 2026101907xx (Task 20).

begin;

create table if not exists public.workspace_invitations (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  -- Only a SHA-256 fingerprint of the raw token is stored, never the token.
  token_digest text not null unique check (token_digest ~ '^[0-9a-f]{64}$'),
  -- Role the invite grants on acceptance. Owners are never minted via invite;
  -- ownership transfers go through motionly_transfer_workspace_ownership.
  role public.workspace_role not null check (role in ('admin', 'member')),
  created_by uuid not null references auth.users(id) on delete restrict,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  accepted_by uuid references auth.users(id) on delete set null,
  accepted_at timestamptz,
  created_at timestamptz not null default now(),
  constraint workspace_invitations_expiry check (expires_at > created_at),
  -- An invite is either live (never accepted) or carries both accept columns.
  constraint workspace_invitations_accept_pairing check (
    (accepted_by is null and accepted_at is null)
    or (accepted_by is not null and accepted_at is not null)
  )
);

create index if not exists workspace_invitations_workspace_created_idx
  on public.workspace_invitations (workspace_id, created_at desc);

-- Acceptance looks up live (unaccepted, unrevoked, unexpired) invites by their
-- token fingerprint; this partial index keeps that hot path fast and
-- enumeration-safe.
create index if not exists workspace_invitations_live_token_idx
  on public.workspace_invitations (token_digest)
  where accepted_at is null and revoked_at is null;

-- Counting the pending invites against a workspace's seat budget is a hot path
-- inside the seat-check RPC.
create index if not exists workspace_invitations_pending_idx
  on public.workspace_invitations (workspace_id)
  where accepted_at is null and revoked_at is null;

alter table public.workspace_invitations enable row level security;

-- Browser/tenant roles never read invitations directly; the API mediates all
-- access via SECURITY DEFINER RPCs.
revoke all on public.workspace_invitations from public, anon, authenticated;

grant select, insert, update, delete on public.workspace_invitations to service_role;

-- ---------------------------------------------------------------------------
-- Per-workspace lock helper. Every seat-sensitive RPC takes this transaction
-- advisory lock first, so concurrent requests on the same workspace serialize
-- and can never both observe "one seat free" and both allocate it.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_lock_workspace_seats(p_workspace_id uuid)
returns void
language plpgsql
as $$
begin
  perform pg_advisory_xact_lock(hashtextextended(p_workspace_id::text || ':seats', 0));
end;
$$;

-- Active (seat-consuming) member count for a workspace. Only 'active' members
-- occupy a seat; 'invited'/'disabled' do not.
create or replace function public.motionly_active_seat_count(p_workspace_id uuid)
returns integer
language sql
stable
as $$
  select count(*)::integer
  from public.workspace_members
  where workspace_id = p_workspace_id
    and status = 'active';
$$;

-- Pending (not yet accepted, not revoked, not expired) invitations that each
-- reserve a prospective seat.
create or replace function public.motionly_pending_invite_count(p_workspace_id uuid)
returns integer
language sql
stable
as $$
  select count(*)::integer
  from public.workspace_invitations
  where workspace_id = p_workspace_id
    and accepted_at is null
    and revoked_at is null
    and expires_at > now();
$$;

-- ---------------------------------------------------------------------------
-- create invite: seat budget = active members + pending invites must stay
-- strictly below p_seat_limit so the new invite still fits.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_create_workspace_invitation(
  p_workspace_id uuid,
  p_token_digest text,
  p_role public.workspace_role,
  p_created_by uuid,
  p_expires_at timestamptz,
  p_seat_limit integer
)
returns public.workspace_invitations
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  invite public.workspace_invitations;
  used integer;
begin
  if p_role not in ('admin', 'member') then
    raise exception 'INVITE_ROLE_INVALID' using errcode = 'P0001';
  end if;

  perform public.motionly_lock_workspace_seats(p_workspace_id);

  used := public.motionly_active_seat_count(p_workspace_id)
        + public.motionly_pending_invite_count(p_workspace_id);
  if used >= p_seat_limit then
    raise exception 'SEAT_LIMIT_REACHED' using errcode = 'P0001';
  end if;

  insert into public.workspace_invitations
    (workspace_id, token_digest, role, created_by, expires_at)
  values
    (p_workspace_id, p_token_digest, p_role, p_created_by, p_expires_at)
  returning * into invite;

  return invite;
end;
$$;

-- ---------------------------------------------------------------------------
-- accept invite by fingerprint. Expired / revoked / already-used links refuse;
-- the seat is re-checked under the lock; the caller becomes an active member
-- with the invite's role. Idempotent for the same accepting user.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_accept_workspace_invitation(
  p_token_digest text,
  p_user_id uuid,
  p_seat_limit integer
)
returns public.workspace_members
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  invite public.workspace_invitations;
  member public.workspace_members;
  used integer;
begin
  select * into invite
  from public.workspace_invitations
  where token_digest = p_token_digest;
  if not found then
    raise exception 'INVITE_NOT_FOUND' using errcode = 'P0002';
  end if;

  perform public.motionly_lock_workspace_seats(invite.workspace_id);

  -- Re-read under the lock in case a concurrent accept/revoke changed it.
  select * into invite
  from public.workspace_invitations
  where id = invite.id
  for update;

  -- Idempotent: the same user re-accepting their own invite returns their row.
  if invite.accepted_at is not null then
    if invite.accepted_by = p_user_id then
      select * into member
      from public.workspace_members
      where workspace_id = invite.workspace_id and user_id = p_user_id;
      if found then
        return member;
      end if;
    end if;
    raise exception 'INVITE_ALREADY_USED' using errcode = 'P0001';
  end if;

  if invite.revoked_at is not null then
    raise exception 'INVITE_REVOKED' using errcode = 'P0001';
  end if;

  if invite.expires_at <= now() then
    raise exception 'INVITE_EXPIRED' using errcode = 'P0001';
  end if;

  -- Already a member? Accept the invite (role upgrade) without consuming a new
  -- seat; otherwise re-check the seat budget before adding a seat.
  select * into member
  from public.workspace_members
  where workspace_id = invite.workspace_id and user_id = p_user_id;

  if found then
    update public.workspace_members
      set role = invite.role, status = 'active'
      where workspace_id = invite.workspace_id and user_id = p_user_id
      returning * into member;
  else
    used := public.motionly_active_seat_count(invite.workspace_id);
    if used >= p_seat_limit then
      raise exception 'SEAT_LIMIT_REACHED' using errcode = 'P0001';
    end if;
    insert into public.workspace_members (workspace_id, user_id, role, status)
    values (invite.workspace_id, p_user_id, invite.role, 'active')
    returning * into member;
  end if;

  update public.workspace_invitations
    set accepted_by = p_user_id, accepted_at = now()
    where id = invite.id;

  return member;
end;
$$;

-- ---------------------------------------------------------------------------
-- revoke invite: idempotent; a live invite is marked revoked. Scoped to the
-- workspace so one tenant can never touch another's invite.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_revoke_workspace_invitation(
  p_workspace_id uuid,
  p_invitation_id uuid
)
returns public.workspace_invitations
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  invite public.workspace_invitations;
begin
  select * into invite
  from public.workspace_invitations
  where id = p_invitation_id and workspace_id = p_workspace_id
  for update;
  if not found then
    raise exception 'INVITE_NOT_FOUND' using errcode = 'P0002';
  end if;

  if invite.accepted_at is not null then
    raise exception 'INVITE_ALREADY_USED' using errcode = 'P0001';
  end if;

  if invite.revoked_at is null then
    update public.workspace_invitations
      set revoked_at = now()
      where id = invite.id
      returning * into invite;
  end if;

  return invite;
end;
$$;

-- ---------------------------------------------------------------------------
-- change a member's role. The last owner can never be demoted (that would
-- orphan the workspace). Promoting to owner is not allowed here; use transfer.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_change_member_role(
  p_workspace_id uuid,
  p_user_id uuid,
  p_role public.workspace_role
)
returns public.workspace_members
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  member public.workspace_members;
  owner_count integer;
begin
  if p_role not in ('admin', 'member') then
    raise exception 'ROLE_CHANGE_INVALID' using errcode = 'P0001';
  end if;

  perform public.motionly_lock_workspace_seats(p_workspace_id);

  select * into member
  from public.workspace_members
  where workspace_id = p_workspace_id and user_id = p_user_id
  for update;
  if not found then
    raise exception 'MEMBER_NOT_FOUND' using errcode = 'P0002';
  end if;

  if member.role = 'owner' then
    select count(*) into owner_count
    from public.workspace_members
    where workspace_id = p_workspace_id and role = 'owner';
    if owner_count <= 1 then
      raise exception 'LAST_OWNER_PROTECTED' using errcode = 'P0001';
    end if;
  end if;

  update public.workspace_members
    set role = p_role
    where workspace_id = p_workspace_id and user_id = p_user_id
    returning * into member;

  return member;
end;
$$;

-- ---------------------------------------------------------------------------
-- remove a member. The last owner can never be removed.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_remove_workspace_member(
  p_workspace_id uuid,
  p_user_id uuid
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  member public.workspace_members;
  owner_count integer;
begin
  perform public.motionly_lock_workspace_seats(p_workspace_id);

  select * into member
  from public.workspace_members
  where workspace_id = p_workspace_id and user_id = p_user_id
  for update;
  if not found then
    raise exception 'MEMBER_NOT_FOUND' using errcode = 'P0002';
  end if;

  if member.role = 'owner' then
    select count(*) into owner_count
    from public.workspace_members
    where workspace_id = p_workspace_id and role = 'owner';
    if owner_count <= 1 then
      raise exception 'LAST_OWNER_PROTECTED' using errcode = 'P0001';
    end if;
  end if;

  delete from public.workspace_members
  where workspace_id = p_workspace_id and user_id = p_user_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- transfer ownership: the current owner hands 'owner' to another active
-- member and is demoted to 'admin'. workspaces.owner_user_id is kept in sync.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_transfer_workspace_ownership(
  p_workspace_id uuid,
  p_from_user_id uuid,
  p_to_user_id uuid
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  from_member public.workspace_members;
  to_member public.workspace_members;
begin
  if p_from_user_id = p_to_user_id then
    raise exception 'OWNERSHIP_TRANSFER_INVALID' using errcode = 'P0001';
  end if;

  perform public.motionly_lock_workspace_seats(p_workspace_id);

  select * into from_member
  from public.workspace_members
  where workspace_id = p_workspace_id and user_id = p_from_user_id
  for update;
  if not found or from_member.role <> 'owner' then
    raise exception 'NOT_WORKSPACE_OWNER' using errcode = 'P0001';
  end if;

  select * into to_member
  from public.workspace_members
  where workspace_id = p_workspace_id and user_id = p_to_user_id
  for update;
  if not found then
    raise exception 'MEMBER_NOT_FOUND' using errcode = 'P0002';
  end if;
  if to_member.status <> 'active' then
    raise exception 'OWNERSHIP_TARGET_INACTIVE' using errcode = 'P0001';
  end if;

  update public.workspace_members
    set role = 'owner', status = 'active'
    where workspace_id = p_workspace_id and user_id = p_to_user_id;
  update public.workspace_members
    set role = 'admin'
    where workspace_id = p_workspace_id and user_id = p_from_user_id;
  update public.workspaces
    set owner_user_id = p_to_user_id, updated_at = now()
    where id = p_workspace_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- leave: a member removes themselves. The last owner cannot leave without
-- first transferring ownership.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_leave_workspace(
  p_workspace_id uuid,
  p_user_id uuid
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  member public.workspace_members;
  owner_count integer;
begin
  perform public.motionly_lock_workspace_seats(p_workspace_id);

  select * into member
  from public.workspace_members
  where workspace_id = p_workspace_id and user_id = p_user_id
  for update;
  if not found then
    raise exception 'MEMBER_NOT_FOUND' using errcode = 'P0002';
  end if;

  if member.role = 'owner' then
    select count(*) into owner_count
    from public.workspace_members
    where workspace_id = p_workspace_id and role = 'owner';
    if owner_count <= 1 then
      raise exception 'LAST_OWNER_PROTECTED' using errcode = 'P0001';
    end if;
  end if;

  delete from public.workspace_members
  where workspace_id = p_workspace_id and user_id = p_user_id;
end;
$$;

-- These RPCs are the only membership/seat mutation path; browser roles never
-- call them. Only service_role (the API) may execute. Supabase's default
-- privileges grant EXECUTE on new public functions to anon/authenticated too,
-- so revoke from them explicitly (not just PUBLIC).
revoke all on function public.motionly_lock_workspace_seats(uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_active_seat_count(uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_pending_invite_count(uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_create_workspace_invitation(
  uuid, text, public.workspace_role, uuid, timestamptz, integer)
  from public, anon, authenticated;
revoke all on function public.motionly_accept_workspace_invitation(
  text, uuid, integer) from public, anon, authenticated;
revoke all on function public.motionly_revoke_workspace_invitation(uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_change_member_role(
  uuid, uuid, public.workspace_role) from public, anon, authenticated;
revoke all on function public.motionly_remove_workspace_member(uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_transfer_workspace_ownership(uuid, uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_leave_workspace(uuid, uuid)
  from public, anon, authenticated;

grant execute on function public.motionly_create_workspace_invitation(
  uuid, text, public.workspace_role, uuid, timestamptz, integer) to service_role;
grant execute on function public.motionly_accept_workspace_invitation(
  text, uuid, integer) to service_role;
grant execute on function public.motionly_revoke_workspace_invitation(uuid, uuid) to service_role;
grant execute on function public.motionly_change_member_role(
  uuid, uuid, public.workspace_role) to service_role;
grant execute on function public.motionly_remove_workspace_member(uuid, uuid) to service_role;
grant execute on function public.motionly_transfer_workspace_ownership(uuid, uuid, uuid)
  to service_role;
grant execute on function public.motionly_leave_workspace(uuid, uuid) to service_role;

commit;
