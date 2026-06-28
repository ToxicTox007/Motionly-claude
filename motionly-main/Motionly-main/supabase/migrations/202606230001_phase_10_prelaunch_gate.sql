-- Phase 10: durable pre-launch billing adjustment evidence.

create table if not exists public.billing_adjustments (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  stripe_event_id text not null,
  adjustment_type text not null check (adjustment_type in ('refund', 'credit', 'chargeback')),
  amount_cents integer not null,
  currency text not null default 'usd',
  reason text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create unique index if not exists billing_adjustments_stripe_event_unique_idx
  on public.billing_adjustments(stripe_event_id);

create index if not exists billing_adjustments_workspace_created_idx
  on public.billing_adjustments(workspace_id, created_at desc);

alter table public.billing_adjustments enable row level security;

drop policy if exists "members read billing adjustments" on public.billing_adjustments;
create policy "members read billing adjustments"
on public.billing_adjustments for select
using (public.is_workspace_member(workspace_id));

-- Writes are service-role only after Stripe signature verification.
