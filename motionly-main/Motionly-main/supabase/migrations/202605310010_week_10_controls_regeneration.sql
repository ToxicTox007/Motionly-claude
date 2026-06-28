-- Week 10 User Controls and Regeneration.
-- Scope: user control settings, artifact layer versions, export restore metadata, and RLS.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'regeneration_layer') then
    create type public.regeneration_layer as enum (
      'transcript',
      'captions',
      'plan',
      'cuts',
      'broll',
      'music',
      'effects',
      'render'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'regeneration_action') then
    create type public.regeneration_action as enum (
      'make_punchier',
      'more_cinematic',
      'less_broll',
      'change_music'
    );
  end if;
end $$;

create table if not exists public.project_control_settings (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  version integer not null check (version > 0),
  schema_version integer not null default 1,
  controls jsonb not null default '{}'::jsonb,
  action public.regeneration_action,
  export_options jsonb not null default '{"resolution":"1080p","format":"mp4","platform":"instagram"}'::jsonb,
  settings_summary jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (project_id, version)
);

create table if not exists public.artifact_versions (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  layer public.regeneration_layer not null,
  version integer not null check (version > 0),
  artifact_id uuid,
  invalidates_from_version integer,
  cache_key text not null,
  settings_summary jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (project_id, layer, version)
);

alter table public.exports
  add column if not exists thumbnail_url text,
  add column if not exists settings_summary jsonb not null default '{}'::jsonb,
  add column if not exists restored_from_export_id uuid references public.exports(id) on delete set null;

create index if not exists project_control_settings_project_version_idx
on public.project_control_settings (workspace_id, project_id, version desc);

create index if not exists artifact_versions_project_layer_idx
on public.artifact_versions (workspace_id, project_id, layer, version desc);

create index if not exists exports_restored_from_idx
on public.exports (restored_from_export_id)
where restored_from_export_id is not null;

alter table public.project_control_settings enable row level security;
alter table public.artifact_versions enable row level security;

drop policy if exists "members read project control settings" on public.project_control_settings;
create policy "members read project control settings"
on public.project_control_settings for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members manage project control settings" on public.project_control_settings;
create policy "members manage project control settings"
on public.project_control_settings for all
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members read artifact versions" on public.artifact_versions;
create policy "members read artifact versions"
on public.artifact_versions for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members manage artifact versions" on public.artifact_versions;
create policy "members manage artifact versions"
on public.artifact_versions for all
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));
