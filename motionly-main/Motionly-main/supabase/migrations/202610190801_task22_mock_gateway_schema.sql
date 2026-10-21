-- Task 22: the mock payment gateway's private database schema + role.
--
-- The gateway is a standalone demo-only service (BILLING_MODE=demo). It owns
-- its own schema `mock_gateway` and a dedicated login role `mock_gateway_role`
-- that can touch ONLY that schema: it has no access to `public` (the
-- application's tenant data) and, conversely, the application roles
-- (service_role, authenticated, anon, authenticator) cannot read
-- `mock_gateway`. Money never moves; rows here only record demo payment
-- state. Full card numbers are NEVER stored — only the brand and last four.
--
-- Forward-only. Reserved timestamp 2026101908xx (Task 22).

begin;

create schema if not exists mock_gateway;

-- Isolated role: owns and may use only its own schema.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'mock_gateway_role') then
    create role mock_gateway_role nologin;
  end if;
end
$$;

-- The role may use and create within its own schema, and nothing else.
grant usage, create on schema mock_gateway to mock_gateway_role;
-- Explicitly deny the application schema to the gateway role.
revoke all on schema public from mock_gateway_role;
-- And deny the gateway schema to every application/browser role.
revoke all on schema mock_gateway from public, anon, authenticated, service_role;

create table if not exists mock_gateway.orders (
  id text primary key,
  amount_paise bigint not null check (amount_paise > 0),
  currency text not null default 'INR' check (currency = 'INR'),
  status text not null default 'created'
    check (status in ('created', 'captured', 'failed', 'refunded', 'partially_refunded')),
  amount_refunded_paise bigint not null default 0 check (amount_refunded_paise >= 0),
  method text check (method in ('upi', 'card')),
  receipt text,
  -- Only the card brand and last four digits are ever stored.
  card_brand text,
  card_last4 text check (card_last4 is null or card_last4 ~ '^[0-9]{4}$'),
  notes jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint orders_refund_within_amount check (amount_refunded_paise <= amount_paise)
);

create table if not exists mock_gateway.subscriptions (
  id text primary key,
  amount_paise bigint not null check (amount_paise > 0),
  currency text not null default 'INR' check (currency = 'INR'),
  status text not null default 'active'
    check (status in ('active', 'cancelled', 'past_due')),
  total_cycles integer not null check (total_cycles >= 1),
  completed_cycles integer not null default 0 check (completed_cycles >= 0),
  card_brand text,
  card_last4 text check (card_last4 is null or card_last4 ~ '^[0-9]{4}$'),
  notes jsonb not null default '{}'::jsonb,
  fail_next_renewal boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists mock_gateway.refunds (
  id text primary key,
  order_id text not null references mock_gateway.orders(id) on delete cascade,
  amount_paise bigint not null check (amount_paise > 0),
  created_at timestamptz not null default now()
);

-- Webhook events: unique event ids so the receiver (Task 23) can de-duplicate.
create table if not exists mock_gateway.webhook_events (
  id text primary key,
  event text not null,
  created_at timestamptz not null default now()
);

-- Idempotency keys for the server-to-server API.
create table if not exists mock_gateway.idempotency_keys (
  key text primary key,
  resource_id text not null,
  created_at timestamptz not null default now()
);

-- The gateway role owns its tables.
grant select, insert, update, delete on all tables in schema mock_gateway to mock_gateway_role;
alter default privileges in schema mock_gateway
  grant select, insert, update, delete on tables to mock_gateway_role;

commit;
