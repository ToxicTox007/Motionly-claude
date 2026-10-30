-- Database audit DB-12 (migration 202610280208): gateway webhook writes to a
-- subscription are applied in event order; an older event is skipped.

begin;
select plan(10);

insert into auth.users (id, email, raw_user_meta_data) values
  ('0c000000-0000-4000-8000-000000000001', 'dbaudit-billing@test.local', '{}');
-- Sign-up created the personal workspace and its free subscription row.
create temporary table billing_ws on commit drop as
select id from public.workspaces where owner_user_id = '0c000000-0000-4000-8000-000000000001';

select has_column('public', 'subscriptions', 'last_gateway_event_at',
  'subscriptions record the newest applied gateway event');

-- The same statement PostgREST runs for the API's upsert (merge-duplicates).
create function pg_temp.apply_event(p_status text, p_plan text, p_event_at timestamptz)
returns setof public.subscriptions
language sql
as $$
  insert into public.subscriptions (workspace_id, plan, status, last_gateway_event_at)
  select id, p_plan, p_status, p_event_at from billing_ws
  on conflict (workspace_id) do update
    set plan = excluded.plan,
        status = excluded.status,
        last_gateway_event_at = excluded.last_gateway_event_at
  returning *;
$$;
grant select on billing_ws to service_role;
grant execute on function pg_temp.apply_event(text, text, timestamptz) to service_role;

set local role service_role;

select is(
  (select status from pg_temp.apply_event('active', 'creator', '2026-10-01 10:00+00')),
  'active', 'the first gateway event applies'
);
select is(
  (select status from pg_temp.apply_event('canceled', 'free', '2026-10-02 10:00+00')),
  'canceled', 'a newer event applies'
);
select is(
  (select count(*)::int from pg_temp.apply_event('active', 'creator', '2026-10-01 12:00+00')),
  0, 'an older event delivered late is skipped (no row returned)'
);
select is(
  (select status || '/' || plan || '/' || last_gateway_event_at::text
   from public.subscriptions where workspace_id = (select id from billing_ws)),
  'canceled/free/2026-10-02 10:00:00+00', 'the late event did not re-activate the cancelled plan'
);
select is(
  (select status from pg_temp.apply_event('past_due', 'free', '2026-10-02 10:00+00')),
  'past_due', 'an event created at the same instant still applies'
);

-- Writes that carry no event time are never skipped and keep the watermark.
update public.subscriptions set cancel_at_period_end = true
where workspace_id = (select id from billing_ws);
select is(
  (select cancel_at_period_end::text || '/' || last_gateway_event_at::text
   from public.subscriptions where workspace_id = (select id from billing_ws)),
  'true/2026-10-02 10:00:00+00', 'a write without an event time applies and keeps the watermark'
);
insert into public.subscriptions (workspace_id, plan, status)
select id, 'free', 'canceled' from billing_ws
on conflict (workspace_id) do update set status = excluded.status;
select is(
  (select last_gateway_event_at::text from public.subscriptions
   where workspace_id = (select id from billing_ws)),
  '2026-10-02 10:00:00+00', 'an upsert that omits the event time leaves it unchanged'
);

reset role;
select is(
  (select count(*)::int from pg_trigger
   where tgrelid = 'public.subscriptions'::regclass
     and tgname = 'subscriptions_skip_stale_gateway_event' and tgenabled = 'O'),
  1, 'the stale-event trigger is enabled'
);
select ok(
  not has_function_privilege('authenticated',
    'public.motionly_skip_stale_gateway_subscription_event()', 'EXECUTE'),
  'the trigger function is not callable by signed-in users'
);

select * from finish();
rollback;
