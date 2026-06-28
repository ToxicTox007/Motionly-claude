-- Week 7 B-roll engine.
-- Scope: transcript-aligned B-roll slots, cached stock assets, provider metadata, and RLS.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'broll_slot_status') then
    create type public.broll_slot_status as enum ('ready', 'disabled', 'failed');
  end if;

  if not exists (select 1 from pg_type where typname = 'stock_provider') then
    create type public.stock_provider as enum ('fixture', 'pexels', 'pixabay');
  end if;

  if not exists (select 1 from pg_type where typname = 'stock_orientation') then
    create type public.stock_orientation as enum ('portrait', 'landscape', 'square');
  end if;
end $$;

create table if not exists public.stock_assets (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  provider public.stock_provider not null,
  provider_asset_id text not null,
  title text not null,
  thumbnail_url text,
  preview_video_url text,
  download_url text,
  license text not null,
  width integer not null check (width > 0),
  height integer not null check (height > 0),
  duration_ms integer not null check (duration_ms >= 0),
  orientation public.stock_orientation not null,
  score numeric(4,3) not null check (score >= 0 and score <= 1),
  raw_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (workspace_id, project_id, provider, provider_asset_id)
);

create table if not exists public.broll_slots (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  edit_plan_id uuid not null references public.edit_plans(id) on delete cascade,
  version integer not null,
  schema_version integer not null default 1,
  cache_key text,
  status public.broll_slot_status not null default 'ready',
  start_ms integer not null check (start_ms >= 0),
  end_ms integer not null check (end_ms > start_ms),
  keywords jsonb not null default '[]'::jsonb,
  intent text not null,
  density text not null,
  priority numeric(4,3) not null check (priority >= 0 and priority <= 1),
  selected_asset_id uuid references public.stock_assets(id) on delete set null,
  candidate_asset_ids jsonb not null default '[]'::jsonb,
  crossfade_ms integer not null default 260 check (crossfade_ms >= 0 and crossfade_ms <= 800),
  provider public.stock_provider not null,
  score numeric(4,3) not null default 0 check (score >= 0 and score <= 1),
  error_code text,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists broll_slots_project_version_idx
on public.broll_slots (workspace_id, project_id, version desc, start_ms asc);

create index if not exists broll_slots_cache_idx
on public.broll_slots (workspace_id, project_id, cache_key);

create index if not exists stock_assets_project_provider_idx
on public.stock_assets (workspace_id, project_id, provider);

alter table public.stock_assets enable row level security;
alter table public.broll_slots enable row level security;

drop policy if exists "members read stock assets" on public.stock_assets;
create policy "members read stock assets"
on public.stock_assets for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members manage stock assets" on public.stock_assets;
create policy "members manage stock assets"
on public.stock_assets for all
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members read broll slots" on public.broll_slots;
create policy "members read broll slots"
on public.broll_slots for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members manage broll slots" on public.broll_slots;
create policy "members manage broll slots"
on public.broll_slots for all
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));
