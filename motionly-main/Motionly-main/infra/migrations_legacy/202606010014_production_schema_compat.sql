-- Production compatibility fixes for databases that applied earlier week migrations.

alter table public.subscriptions
add column if not exists plan text,
add column if not exists stripe_price_id text,
add column if not exists current_period_start timestamptz,
add column if not exists cancel_at_period_end boolean not null default false;

alter table public.subscriptions
alter column status drop default;

alter table public.subscriptions
alter column status type text using status::text;

update public.subscriptions
set plan = case
  when plan is not null then plan
  when plan_key in ('creator', 'pro', 'agency') then plan_key
  else 'free'
end;

alter table public.subscriptions
alter column plan set default 'free',
alter column plan set not null;

do $$
begin
  if exists (
    select 1
    from pg_constraint
    where conname = 'subscriptions_plan_check'
  ) then
    alter table public.subscriptions drop constraint subscriptions_plan_check;
  end if;
end $$;

alter table public.subscriptions
add constraint subscriptions_plan_check
check (plan in ('free', 'creator', 'pro', 'agency'));

update public.subscriptions
set status = 'none'
where status is null;

do $$
begin
  if exists (
    select 1
    from pg_constraint
    where conname = 'subscriptions_status_check'
  ) then
    alter table public.subscriptions drop constraint subscriptions_status_check;
  end if;
end $$;

alter table public.subscriptions
alter column status set default 'none',
alter column status set not null;

create unique index if not exists subscriptions_workspace_unique_idx
on public.subscriptions(workspace_id);

create index if not exists subscriptions_stripe_customer_id_idx
on public.subscriptions(stripe_customer_id);

create index if not exists subscriptions_stripe_subscription_id_idx
on public.subscriptions(stripe_subscription_id);

alter table public.usage_ledger
add column if not exists source_id uuid;
