-- Task 23: billing tables/columns are renamed from Stripe to neutral gateway
-- names. After all migrations apply, the Stripe-named columns/tables are gone
-- and the gateway-named ones exist with INR semantics.

begin;
select plan(12);

-- subscriptions: gateway-named columns exist, Stripe-named ones do not.
select has_column('public', 'subscriptions', 'gateway_customer_id',
  'subscriptions.gateway_customer_id exists');
select has_column('public', 'subscriptions', 'gateway_subscription_id',
  'subscriptions.gateway_subscription_id exists');
select has_column('public', 'subscriptions', 'gateway_price_id',
  'subscriptions.gateway_price_id exists');
select hasnt_column('public', 'subscriptions', 'stripe_customer_id',
  'subscriptions.stripe_customer_id is gone');
select hasnt_column('public', 'subscriptions', 'stripe_subscription_id',
  'subscriptions.stripe_subscription_id is gone');

-- webhook idempotency table is renamed.
select has_table('public', 'gateway_webhook_events',
  'gateway_webhook_events table exists');
select hasnt_table('public', 'stripe_webhook_events',
  'stripe_webhook_events table is gone');

-- billing_adjustments: gateway-named, INR paise.
select has_column('public', 'billing_adjustments', 'gateway_event_id',
  'billing_adjustments.gateway_event_id exists');
select has_column('public', 'billing_adjustments', 'amount_paise',
  'billing_adjustments.amount_paise exists');
select hasnt_column('public', 'billing_adjustments', 'stripe_event_id',
  'billing_adjustments.stripe_event_id is gone');
select hasnt_column('public', 'billing_adjustments', 'amount_cents',
  'billing_adjustments.amount_cents is gone');

-- adjustment_type now allows 'charge' (a captured-payment receipt).
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.billing_adjustments'::regclass
      and contype = 'c'
      and conname = 'billing_adjustments_adjustment_type_check'
      and pg_get_constraintdef(oid) like '%charge%'
  ),
  'adjustment_type check constraint allows ''charge'''
);

select * from finish();
rollback;
