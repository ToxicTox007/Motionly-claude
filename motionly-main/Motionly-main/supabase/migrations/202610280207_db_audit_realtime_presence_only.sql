-- Database audit DB-25: browsers may track presence on a private project or
-- workspace topic, but may not broadcast on it.
--
-- The INSERT policy on realtime.messages admitted both presence and broadcast
-- from any active member, so a member could send forged MANIFEST_UPDATED /
-- PLAN_REVISION_UPDATED events that other editors act on (ScalePanel advances
-- its tracked revision from the payload). Those events come only from the API
-- (app/services/collaboration.py, Realtime's REST broadcast with the service
-- role). The web app only listens for broadcasts and tracks presence, so the
-- INSERT policy now admits presence only. Join/receive (SELECT) is unchanged.
--
-- Like 202610191101, this is a no-op with a warning when realtime.messages is
-- absent (Realtime creates it; db-migrate waits for Realtime to be healthy).
--
-- Forward-only. Covered by supabase/tests/db_audit_realtime_presence_only_test.sql.

do $$
begin
  if to_regclass('realtime.messages') is null then
    raise warning 'db-audit: realtime.messages is absent; presence-only broadcast policy not applied. '
      'Start Realtime before db-migrate.';
    return;
  end if;

  drop policy if exists motionly_members_broadcast_private_topics on realtime.messages;
  create policy motionly_members_broadcast_private_topics
    on realtime.messages
    for insert
    to authenticated
    with check (
      realtime.messages.extension = 'presence'
      and realtime.motionly_can_access_topic((realtime.topic())::text)
    );
end
$$;
