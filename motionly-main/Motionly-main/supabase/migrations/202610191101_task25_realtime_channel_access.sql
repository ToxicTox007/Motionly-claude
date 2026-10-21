-- Task 25 (Realtime private channels): channel access policies on realtime.messages.
--
-- Supabase Realtime authorizes private channels with Row Level Security on
-- realtime.messages: when a client joins a private topic (or broadcasts /
-- sends presence on it) Realtime opens a transaction as the connecting user's
-- role with their JWT claims loaded, writes a probe row into realtime.messages
-- with realtime.topic() set to the channel topic, checks the RLS policies, and
-- rolls back. A member therefore reaches a private topic only when a SELECT
-- (join / receive) and an INSERT (broadcast / presence) policy both pass.
--
-- Motionly's private topics are tenant-scoped:
--   workspace:<workspace-uuid>  — the workspace's own live channel
--   project:<project-uuid>      — scoped to the project's owning workspace
-- Only an ACTIVE member of that workspace may join, broadcast or send presence.
-- Anyone else (a member of another workspace, the anon role, a signed-out
-- visitor) is denied. public topics are not used by Motionly.
--
-- Ordering: Supabase Realtime creates the `realtime` schema and the
-- realtime.messages table itself, at startup, by running its own migrations
-- against the database. The one-shot db-migrate service therefore waits for
-- Realtime to report healthy before it runs (infra/docker-compose.gpu.yml), so
-- realtime.messages exists by the time this migration is applied. The guard
-- below keeps the migration safe (a no-op) if it is ever applied against a
-- database where Realtime has not yet created its schema — it never creates
-- objects it does not own.
--
-- Forward-only; never edited. High-risk (RLS on a cross-service table): covered
-- by supabase/tests/task_25_realtime_channel_access_test.sql and
-- apps/api/tests/test_migration_contract_coverage.py.

do $$
declare
  v_has_messages boolean;
begin
  select exists (
    select 1
    from information_schema.tables
    where table_schema = 'realtime' and table_name = 'messages'
  ) into v_has_messages;

  if not v_has_messages then
    raise warning
      'task25: realtime.messages is absent; skipping Realtime channel policies. '
      'Start Realtime (it creates the realtime schema) before db-migrate so this '
      'migration can attach the private-channel authorization policies.';
    return;
  end if;

  -- Authorization runs as the connecting role (authenticated / anon). Those
  -- roles must be able to evaluate the policy predicate and reach the helper.
  grant usage on schema realtime to authenticated, anon;
  grant select, insert on realtime.messages to authenticated;

  -- A SECURITY DEFINER helper that decides whether the current user may use the
  -- topic Realtime is authorizing. It parses the topic name and reuses
  -- public.is_workspace_member(uuid), which checks auth.uid() against an ACTIVE
  -- membership row. project:<id> maps to the project's owning workspace.
  create or replace function realtime.motionly_can_access_topic(topic_name text)
  returns boolean
  language plpgsql
  stable
  security definer
  set search_path = public, realtime
  as $fn$
  declare
    v_kind text;
    v_id text;
    v_uuid uuid;
    v_workspace_id uuid;
  begin
    -- Never authorize without an authenticated user; anon has no auth.uid().
    if auth.uid() is null then
      return false;
    end if;
    if topic_name is null then
      return false;
    end if;

    v_kind := split_part(topic_name, ':', 1);
    v_id := split_part(topic_name, ':', 2);
    if v_id = '' then
      return false;
    end if;

    begin
      v_uuid := v_id::uuid;
    exception when others then
      return false;
    end;

    if v_kind = 'workspace' then
      return public.is_workspace_member(v_uuid);
    elsif v_kind = 'project' then
      select p.workspace_id into v_workspace_id
      from public.projects p
      where p.id = v_uuid;
      if v_workspace_id is null then
        return false;
      end if;
      return public.is_workspace_member(v_workspace_id);
    end if;

    -- Unknown / unscoped topic: deny by default.
    return false;
  end;
  $fn$;

  revoke all on function realtime.motionly_can_access_topic(text) from public;
  grant execute on function realtime.motionly_can_access_topic(text)
    to authenticated, anon, service_role;

  -- Join / receive (SELECT) on a private topic: active members of the topic's
  -- workspace only. Dropped-and-recreated so re-applying is safe and the policy
  -- definition is unambiguous.
  drop policy if exists motionly_members_join_private_topics on realtime.messages;
  create policy motionly_members_join_private_topics
    on realtime.messages
    for select
    to authenticated
    using (realtime.motionly_can_access_topic((realtime.topic())::text));

  -- Broadcast / presence (INSERT) on a private topic: the same active members.
  drop policy if exists motionly_members_broadcast_private_topics on realtime.messages;
  create policy motionly_members_broadcast_private_topics
    on realtime.messages
    for insert
    to authenticated
    with check (realtime.motionly_can_access_topic((realtime.topic())::text));
end
$$;
