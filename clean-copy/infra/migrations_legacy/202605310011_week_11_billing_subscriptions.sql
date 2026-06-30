-- Week 11: Stripe billing, subscriptions, usage, and webhook idempotency.

create table if not exists public.subscriptions (
  workspace_id uuid primary key references public.workspaces(id) on delete cascade,
  plan text not null default 'free'
    check (plan in ('free', 'creator', 'pro', 'agency')),
  status text not null default 'none'
    check (status in ('none', 'trialing', 'active', 'past_due', 'canceled', 'unpaid', 'incomplete')),
  stripe_customer_id text,
  stripe_subscription_id text,
  stripe_price_id text,
  current_period_start timestamptz,
  current_period_end timestamptz,
  cancel_at_period_end boolean not null default false,
  updated_at timestamptz not null default now()
);

create table if not exists public.usage_ledger (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  event_type text not null check (event_type in ('render_started', 'premium_action')),
  quantity integer not null default 1 check (quantity > 0),
  source_id uuid,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.stripe_webhook_events (
  id text primary key,
  event_type text not null,
  processed_at timestamptz not null default now()
);

create index if not exists subscriptions_stripe_customer_id_idx
  on public.subscriptions(stripe_customer_id);

create index if not exists subscriptions_stripe_subscription_id_idx
  on public.subscriptions(stripe_subscription_id);

create index if not exists usage_ledger_workspace_created_at_idx
  on public.usage_ledger(workspace_id, created_at desc);

alter table public.subscriptions enable row level security;
alter table public.usage_ledger enable row level security;
alter table public.stripe_webhook_events enable row level security;

drop policy if exists "members read subscriptions" on public.subscriptions;
create policy "members read subscriptions"
on public.subscriptions for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members read usage ledger" on public.usage_ledger;
create policy "members read usage ledger"
on public.usage_ledger for select
using (public.is_workspace_member(workspace_id));

-- Writes are performed by the API with the service role after Stripe webhook verification.
