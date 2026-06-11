-- Week 8 Music and Sound Design.
-- Scope: music library, music selections, beat maps, SFX intensity, and RLS.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'music_provider') then
    create type public.music_provider as enum ('fixture');
  end if;
end $$;

create table if not exists public.music_tracks (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  provider public.music_provider not null,
  provider_track_id text not null,
  title text not null,
  license text not null,
  duration_ms integer not null check (duration_ms >= 0),
  mood_tags text[] not null default array[]::text[],
  bpm integer check (bpm > 0),
  energy numeric(4,3) check (energy >= 0 and energy <= 1),
  vocals boolean not null default false,
  loopable boolean not null default false,
  beat_map jsonb not null default '[]'::jsonb,
  raw_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (workspace_id, project_id, provider, provider_track_id)
);

create table if not exists public.project_music_selections (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  edit_plan_id uuid not null references public.edit_plans(id) on delete cascade,
  version integer not null,
  schema_version integer not null default 1,
  status text not null,
  music_track_id uuid references public.music_tracks(id) on delete set null,
  music_volume numeric(4,3) not null default 0.25 check (music_volume >= 0 and music_volume <= 1),
  sfx_intensity numeric(4,3) not null default 0.25 check (sfx_intensity >= 0 and sfx_intensity <= 1),
  rights_confirmed boolean not null default false,
  cache_key text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists music_tracks_project_provider_idx
on public.music_tracks (workspace_id, project_id, provider);

create index if not exists project_music_selections_project_version_idx
on public.project_music_selections (workspace_id, project_id, version desc, created_at desc);

alter table public.music_tracks enable row level security;
alter table public.project_music_selections enable row level security;

drop policy if exists "members read music tracks" on public.music_tracks;
create policy "members read music tracks"
on public.music_tracks for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members manage music tracks" on public.music_tracks;
create policy "members manage music tracks"
on public.music_tracks for all
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members read project music selections" on public.project_music_selections;
create policy "members read project music selections"
on public.project_music_selections for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members manage project music selections" on public.project_music_selections;
create policy "members manage project music selections"
on public.project_music_selections for all
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));
