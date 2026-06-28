-- Phase 8: durable worker liveness, provider SLOs, and unit cost attribution.

create table if not exists public.worker_heartbeats (
  worker_id text primary key,
  worker_role text not null,
  queues text[] not null default '{}',
  version text,
  metadata jsonb not null default '{}',
  started_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

create table if not exists public.provider_slo_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid references public.workspaces(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  provider text not null,
  operation text not null,
  success boolean not null,
  latency_ms integer not null check (latency_ms >= 0),
  error_code text,
  created_at timestamptz not null default now()
);
create index if not exists provider_slo_events_provider_created_idx
  on public.provider_slo_events(provider, created_at desc);

create table if not exists public.cost_attribution_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  render_job_id uuid references public.render_jobs(id) on delete cascade,
  export_id uuid references public.exports(id) on delete set null,
  source_id text not null,
  provider text not null,
  category text not null check (category in ('tokens','generated_seconds','compute_cpu','compute_gpu','storage','egress')),
  quantity numeric(18,6) not null check (quantity >= 0),
  unit text not null,
  unit_cost_usd numeric(18,8) not null check (unit_cost_usd >= 0),
  cost_usd numeric(18,8) generated always as (quantity * unit_cost_usd) stored,
  approved_at timestamptz,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index if not exists cost_attribution_workspace_created_idx
  on public.cost_attribution_events(workspace_id, created_at desc);
create index if not exists cost_attribution_approved_export_idx
  on public.cost_attribution_events(export_id) where approved_at is not null;
create unique index if not exists cost_attribution_source_unique_idx
  on public.cost_attribution_events(workspace_id, provider, category, source_id);

alter table public.worker_heartbeats enable row level security;
alter table public.provider_slo_events enable row level security;
alter table public.cost_attribution_events enable row level security;
-- Operational writes and reads are service-role only.

alter table public.phase5_resources drop constraint if exists phase5_resources_status_check;
alter table public.phase5_resources add constraint phase5_resources_status_check
  check (status in ('draft','queued','processing','ready','completed','partial','failed','canceled'));
