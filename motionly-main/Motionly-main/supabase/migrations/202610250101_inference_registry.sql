-- Task 20 (M5): database inference registry and AES-256-GCM encrypted secrets.
--
-- Tier 3 adds the database registry source alongside the file registry (plan
-- Section 2.4). Four registry tables plus a singleton version counter; the
-- counter is bumped in the same transaction as every registry write by a
-- trigger, so a RuntimeHolder can reload atomically when it changes
-- (apps/api/app/services/inference_runtime.py).
--
-- RLS is API-authoritative: the API/admin surface writes through the service
-- role, and the restricted worker role (Task 18) reads only the catalog tables
-- the runtime builder needs (ai_adapters, ai_models, ai_routes and the version
-- counter — see apps/api/tests/worker_db_reachability.py). The worker is never
-- granted ai_secrets: a secret value is encrypted at rest and only the API/ops
-- context decrypts it. Workspace-scope columns exist but a feature flag keeps
-- them disabled; the application validator rejects non-global entries while the
-- flag is off.
begin;

-- 1. Adapters: an endpoint and how to reach it (no secret value).
create table if not exists public.ai_adapters (
  id text primary key,
  type text not null,
  base_url text,
  network text not null default 'public' check (network in ('public', 'private')),
  auth jsonb not null default '{"type":"none"}'::jsonb,
  timeout_s double precision,
  health jsonb,
  extra_headers jsonb not null default '{}'::jsonb,
  extra_body jsonb not null default '{}'::jsonb,
  api_version text,
  data_processor jsonb,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 2. Models: identity, revision and licence metadata. Licence and commercial
-- use are nullable because remote models may omit them.
create table if not exists public.ai_models (
  id text primary key,
  model_id text not null,
  revision text not null,
  licence text,
  commercial_use boolean,
  source_type text,
  source_url text,
  capabilities text[] not null default '{}',
  min_vram_gb double precision,
  weights_sha256 text,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 3. Routes: the route table (plan Section 2.3), including the Section 2.11
-- offering jsonb for user model choice (Task 29).
create table if not exists public.ai_routes (
  id text primary key,
  capability text not null,
  adapter_id text not null references public.ai_adapters(id) on delete restrict,
  model_id text not null,
  model_ref text references public.ai_models(id) on delete restrict,
  model_revision text not null,
  allow_provider_managed_revision boolean not null default false,
  priority integer not null default 100,
  strategy text,
  provider text,
  pricing jsonb,
  pricing_status text not null default 'priced'
    check (pricing_status in ('priced', 'free', 'unknown')),
  explicitly_free boolean not null default false,
  budget jsonb,
  hard_cap jsonb,
  limits jsonb,
  timeout_s double precision,
  compute_requirement text not null default 'any'
    check (compute_requirement in ('cpu', 'gpu', 'remote', 'any')),
  requires_gpu boolean not null default false,
  scope text not null default 'global',
  offering jsonb,
  params jsonb not null default '{}'::jsonb,
  mapping jsonb,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists ai_routes_capability_priority_idx
  on public.ai_routes(capability, priority);

-- 4. Secrets: AES-256-GCM ciphertext only. There is deliberately no plaintext
-- column; name+scope is the key. AAD binds name, scope and key_version.
create table if not exists public.ai_secrets (
  scope text not null default 'global',
  name text not null,
  ciphertext text not null,
  nonce text not null,
  key_version text not null,
  fingerprint text not null,
  created_by uuid,
  created_at timestamptz not null default now(),
  rotated_at timestamptz,
  last_used_at timestamptz,
  primary key (scope, name)
);

-- 5. Version counter: a single row bumped in the same transaction as each write.
create table if not exists public.ai_registry_version (
  id integer primary key default 1 check (id = 1),
  version bigint not null default 0,
  updated_at timestamptz not null default now()
);
insert into public.ai_registry_version (id, version)
  values (1, 0)
  on conflict (id) do nothing;

-- The trigger bumps the singleton version on every registry write, atomically
-- within the writing transaction (plan Task 20 "atomic version bump").
create or replace function public.motionly_bump_ai_registry_version()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.ai_registry_version
     set version = version + 1, updated_at = now()
   where id = 1;
  return null;
end;
$$;

drop trigger if exists ai_adapters_bump_version on public.ai_adapters;
create trigger ai_adapters_bump_version
  after insert or update or delete on public.ai_adapters
  for each statement execute function public.motionly_bump_ai_registry_version();
drop trigger if exists ai_models_bump_version on public.ai_models;
create trigger ai_models_bump_version
  after insert or update or delete on public.ai_models
  for each statement execute function public.motionly_bump_ai_registry_version();
drop trigger if exists ai_routes_bump_version on public.ai_routes;
create trigger ai_routes_bump_version
  after insert or update or delete on public.ai_routes
  for each statement execute function public.motionly_bump_ai_registry_version();
drop trigger if exists ai_secrets_bump_version on public.ai_secrets;
create trigger ai_secrets_bump_version
  after insert or update or delete on public.ai_secrets
  for each statement execute function public.motionly_bump_ai_registry_version();

-- 6. RLS. API-authoritative: the service role manages every table; the worker
-- reads only the catalog tables and the version counter it needs to build the
-- runtime, and is never granted ai_secrets.
alter table public.ai_adapters enable row level security;
alter table public.ai_models enable row level security;
alter table public.ai_routes enable row level security;
alter table public.ai_secrets enable row level security;
alter table public.ai_registry_version enable row level security;

drop policy if exists "Service role manages ai_adapters" on public.ai_adapters;
create policy "Service role manages ai_adapters" on public.ai_adapters
  for all to service_role using (true) with check (true);
drop policy if exists "Service role manages ai_models" on public.ai_models;
create policy "Service role manages ai_models" on public.ai_models
  for all to service_role using (true) with check (true);
drop policy if exists "Service role manages ai_routes" on public.ai_routes;
create policy "Service role manages ai_routes" on public.ai_routes
  for all to service_role using (true) with check (true);
drop policy if exists "Service role manages ai_secrets" on public.ai_secrets;
create policy "Service role manages ai_secrets" on public.ai_secrets
  for all to service_role using (true) with check (true);
drop policy if exists "Service role manages ai_registry_version" on public.ai_registry_version;
create policy "Service role manages ai_registry_version" on public.ai_registry_version
  for all to service_role using (true) with check (true);

-- The worker reads the catalog for every workspace (the registry is global), so
-- its read policies are unconditional. ai_secrets gets NO worker policy.
drop policy if exists "Worker reads ai_adapters" on public.ai_adapters;
create policy "Worker reads ai_adapters" on public.ai_adapters
  for select to motionly_worker using (true);
drop policy if exists "Worker reads ai_models" on public.ai_models;
create policy "Worker reads ai_models" on public.ai_models
  for select to motionly_worker using (true);
drop policy if exists "Worker reads ai_routes" on public.ai_routes;
create policy "Worker reads ai_routes" on public.ai_routes
  for select to motionly_worker using (true);
drop policy if exists "Worker reads ai_registry_version" on public.ai_registry_version;
create policy "Worker reads ai_registry_version" on public.ai_registry_version
  for select to motionly_worker using (true);

-- 7. Grants. The service role manages everything; the worker reads only the
-- catalog tables and the version counter (apps/api/tests/test_worker_db_grants.py
-- checks this allowlist against the runtime builder's reads in both directions).
grant select, insert, update, delete on public.ai_adapters to service_role;
grant select, insert, update, delete on public.ai_models to service_role;
grant select, insert, update, delete on public.ai_routes to service_role;
grant select, insert, update, delete on public.ai_secrets to service_role;
grant select, insert, update, delete on public.ai_registry_version to service_role;

grant select on public.ai_adapters to motionly_worker;
grant select on public.ai_models to motionly_worker;
grant select on public.ai_routes to motionly_worker;
grant select on public.ai_registry_version to motionly_worker;

commit;
