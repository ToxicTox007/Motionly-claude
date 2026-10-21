-- Task 23: rename Stripe-named billing tables/columns to neutral gateway names.
--
-- Live/Stripe billing is removed; billing now runs through the demo payment
-- gateway. This forward-only migration renames the schema to match:
--   subscriptions.stripe_customer_id      -> gateway_customer_id
--   subscriptions.stripe_subscription_id  -> gateway_subscription_id
--   subscriptions.stripe_price_id         -> gateway_price_id
--   table stripe_webhook_events           -> gateway_webhook_events
--   billing_adjustments.stripe_event_id   -> gateway_event_id
--   billing_adjustments.amount_cents       -> amount_paise (INR paise)
--   billing_adjustments.currency default 'usd' -> 'inr'
--   billing_adjustments adjustment_type check: add 'charge'
-- Idempotent (guards on column/table existence) so a partial apply is safe.

-- subscriptions columns ----------------------------------------------------
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'subscriptions'
      and column_name = 'stripe_customer_id'
  ) then
    alter table public.subscriptions rename column stripe_customer_id to gateway_customer_id;
  end if;
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'subscriptions'
      and column_name = 'stripe_subscription_id'
  ) then
    alter table public.subscriptions
      rename column stripe_subscription_id to gateway_subscription_id;
  end if;
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'subscriptions'
      and column_name = 'stripe_price_id'
  ) then
    alter table public.subscriptions rename column stripe_price_id to gateway_price_id;
  end if;
end $$;

create index if not exists subscriptions_gateway_customer_id_idx
  on public.subscriptions(gateway_customer_id);
create index if not exists subscriptions_gateway_subscription_id_idx
  on public.subscriptions(gateway_subscription_id);

-- webhook events table -----------------------------------------------------
do $$
begin
  if exists (
    select 1 from information_schema.tables
    where table_schema = 'public' and table_name = 'stripe_webhook_events'
  ) and not exists (
    select 1 from information_schema.tables
    where table_schema = 'public' and table_name = 'gateway_webhook_events'
  ) then
    alter table public.stripe_webhook_events rename to gateway_webhook_events;
  end if;
end $$;

create table if not exists public.gateway_webhook_events (
  id text primary key,
  event_type text not null,
  processed_at timestamptz not null default now()
);

alter table public.gateway_webhook_events enable row level security;

-- billing_adjustments columns ---------------------------------------------
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'billing_adjustments'
      and column_name = 'stripe_event_id'
  ) then
    alter table public.billing_adjustments rename column stripe_event_id to gateway_event_id;
  end if;
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'billing_adjustments'
      and column_name = 'amount_cents'
  ) then
    alter table public.billing_adjustments rename column amount_cents to amount_paise;
  end if;
end $$;

alter table public.billing_adjustments alter column currency set default 'inr';

-- adjustment_type now also allows 'charge' (receipts for captured payments).
alter table public.billing_adjustments
  drop constraint if exists billing_adjustments_adjustment_type_check;
alter table public.billing_adjustments
  add constraint billing_adjustments_adjustment_type_check
  check (adjustment_type in ('charge', 'refund', 'credit', 'chargeback'));

-- rename the unique index to the neutral name.
alter index if exists billing_adjustments_stripe_event_unique_idx
  rename to billing_adjustments_gateway_event_unique_idx;
create unique index if not exists billing_adjustments_gateway_event_unique_idx
  on public.billing_adjustments(gateway_event_id);
