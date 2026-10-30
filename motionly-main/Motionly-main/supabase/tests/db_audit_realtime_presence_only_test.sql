-- Database audit DB-25 (migration 202610280207): members may send presence on
-- a private topic but not broadcasts. Skipped where Realtime has not created
-- realtime.messages (the db stage starts Realtime, so CI runs it).

begin;
select plan(2);

select case
  when to_regclass('realtime.messages') is null then skip('realtime.messages is absent', 1)
  else ok(
    exists (
      select 1 from pg_policies
      where schemaname = 'realtime' and tablename = 'messages'
        and policyname = 'motionly_members_broadcast_private_topics'
        and cmd = 'INSERT'
        and with_check ~ 'extension'
        and with_check ~ 'presence'
        and with_check ~ 'motionly_can_access_topic'),
    'the INSERT policy admits presence on an authorized topic only')
end;

select case
  when to_regclass('realtime.messages') is null then skip('realtime.messages is absent', 1)
  else ok(
    exists (
      select 1 from pg_policies
      where schemaname = 'realtime' and tablename = 'messages'
        and policyname = 'motionly_members_join_private_topics'
        and cmd = 'SELECT'),
    'members can still join and receive on their private topics')
end;

select * from finish();
rollback;
