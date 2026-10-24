-- Banned users lose access immediately, not when their access token expires.
--
-- An admin ban sets auth.users.banned_until (GoTrue). GoTrue then refuses new
-- sign-ins and token refreshes, but an access token issued before the ban
-- stays cryptographically valid until it expires (an hour by default), and
-- nothing else looked at the ban: the API's membership check, the browser's
-- row-level security (is_workspace_member) and Realtime channel authorization
-- (which uses is_workspace_member) all kept admitting the banned user.
--
-- Every workspace authorization path now also requires that the user is not
-- banned (or soft-deleted). The API resolves the caller's role through
-- motionly_workspace_member_role, which applies the same rule.
begin;

-- True while the user is banned or soft-deleted in GoTrue. plpgsql, so the
-- auth.users columns (owned by GoTrue's migrations) are resolved at run time.
create or replace function public.motionly_user_access_suspended(p_user_id uuid)
returns boolean
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  return exists (
    select 1
    from auth.users u
    where u.id = p_user_id
      and (
        (u.banned_until is not null and u.banned_until > now())
        or u.deleted_at is not null
      )
  );
end;
$$;

-- The RLS helper (browser reads, Realtime topic access): unchanged except that
-- a banned or deleted user is never a member.
create or replace function public.is_workspace_member(target_workspace_id uuid)
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.workspace_members wm
    where wm.workspace_id = target_workspace_id
      and wm.user_id = auth.uid()
      and wm.status = 'active'
  )
  and not public.motionly_user_access_suspended(auth.uid());
$$;

-- The API's membership check (services/auth.py): the active member's role, or
-- no row for a non-member, an inactive member, or a banned/deleted user. A set
-- of rows so PostgREST answers GET /rpc/... with the same shape as a table read.
create or replace function public.motionly_workspace_member_role(
  p_workspace_id uuid,
  p_user_id uuid
)
returns table (role public.workspace_role)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select wm.role
  from public.workspace_members wm
  where wm.workspace_id = p_workspace_id
    and wm.user_id = p_user_id
    and wm.status = 'active'
    and not public.motionly_user_access_suspended(p_user_id)
  limit 1;
$$;

-- Only the API (service_role) asks; browser roles and the worker must not be
-- able to probe whether a given user is banned or a member of a workspace.
revoke all on function public.motionly_user_access_suspended(uuid)
  from public, anon, authenticated, motionly_worker;
revoke all on function public.motionly_workspace_member_role(uuid, uuid)
  from public, anon, authenticated, motionly_worker;
grant execute on function public.motionly_user_access_suspended(uuid) to service_role;
grant execute on function public.motionly_workspace_member_role(uuid, uuid) to service_role;

commit;
