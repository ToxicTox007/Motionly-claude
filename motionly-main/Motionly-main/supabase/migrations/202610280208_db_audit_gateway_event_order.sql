-- Database audit DB-12: payment gateway webhooks change a subscription in the
-- order the gateway produced them, not the order they happen to arrive.
--
-- The gateway retries a failed delivery with backoff, so an older event can
-- arrive after a newer one: a subscription.charged delayed behind a
-- subscription.cancelled re-activated the cancelled plan, and a late
-- charge_failed marked a renewed subscription past due. Duplicate deliveries
-- were already ignored (202610280104); different events were not ordered.
--
-- subscriptions.last_gateway_event_at records the creation time of the newest
-- gateway event applied to the row. The API sends the event's time with every
-- webhook-driven write; a write carrying an older time than the stored one is
-- skipped by the trigger below (a BEFORE UPDATE trigger that returns NULL),
-- under the row lock the upsert already takes, so two deliveries racing for
-- one workspace are ordered as well. Writes that do not carry an event time
-- (the cancel-at-period-end endpoint, the sign-up trigger, admin repair) keep
-- the stored value and are never skipped. Events created at the same instant
-- are all applied, in arrival order, as before.
--
-- Forward-only. Covered by supabase/tests/db_audit_gateway_event_order_test.sql.

begin;

alter table public.subscriptions
  add column if not exists last_gateway_event_at timestamptz;

comment on column public.subscriptions.last_gateway_event_at is
  'Creation time of the newest payment gateway event applied to this row; older events are skipped.';

create or replace function public.motionly_skip_stale_gateway_subscription_event()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  if new.last_gateway_event_at is not null
     and old.last_gateway_event_at is not null
     and new.last_gateway_event_at < old.last_gateway_event_at
  then
    -- Skip this row: the subscription already reflects a newer event.
    return null;
  end if;
  return new;
end;
$$;
revoke all on function public.motionly_skip_stale_gateway_subscription_event()
  from public, anon, authenticated;

drop trigger if exists subscriptions_skip_stale_gateway_event on public.subscriptions;
create trigger subscriptions_skip_stale_gateway_event
before update on public.subscriptions
for each row execute function public.motionly_skip_stale_gateway_subscription_event();

commit;
