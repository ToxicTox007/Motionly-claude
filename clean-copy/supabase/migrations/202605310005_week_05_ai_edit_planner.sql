-- Week 5 AI edit planner.
-- Scope: structured edit plan versions, activation, provider/cost metadata, cache keys, and RLS.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'edit_plan_status') then
    create type public.edit_plan_status as enum ('ready', 'failed');
  end if;

  if not exists (select 1 from pg_type where typname = 'edit_plan_style') then
    create type public.edit_plan_style as enum (
      'luxury',
      'founder',
      'travel',
      'fashion',
      'podcast'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'broll_density') then
    create type public.broll_density as enum ('none', 'low', 'medium', 'high');
  end if;
end $$;

create table if not exists public.edit_plans (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  transcript_id uuid not null references public.transcripts(id) on delete cascade,
  version integer not null check (version > 0),
  schema_version integer not null default 1,
  status public.edit_plan_status not null default 'ready',
  is_active boolean not null default false,
  style public.edit_plan_style not null,
  target_duration_s integer not null check (target_duration_s between 10 and 90),
  broll_density public.broll_density not null default 'medium',
  prompt text not null default '',
  feedback text,
  provider text not null,
  model text not null,
  cache_key text not null,
  schema_hash text not null,
  plan jsonb not null,
  summary jsonb not null,
  validation_errors jsonb not null default '[]'::jsonb,
  latency_ms integer,
  cost_estimate_usd numeric,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, version)
);

create unique index if not exists edit_plans_one_active_per_project_idx
on public.edit_plans (project_id)
where is_active;

create index if not exists edit_plans_project_version_idx
on public.edit_plans (project_id, version desc);

create index if not exists edit_plans_workspace_cache_idx
on public.edit_plans (workspace_id, project_id, cache_key);

drop trigger if exists edit_plans_touch_updated_at on public.edit_plans;
create trigger edit_plans_touch_updated_at
before update on public.edit_plans
for each row execute function public.touch_updated_at();

alter table public.edit_plans enable row level security;

drop policy if exists "members read edit plans" on public.edit_plans;
create policy "members read edit plans"
on public.edit_plans for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members insert edit plans" on public.edit_plans;
create policy "members insert edit plans"
on public.edit_plans for insert
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members update edit plans" on public.edit_plans;
create policy "members update edit plans"
on public.edit_plans for update
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));
