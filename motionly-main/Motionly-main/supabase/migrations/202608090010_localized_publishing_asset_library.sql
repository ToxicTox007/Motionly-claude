-- Locale-aware publishing packages and a workspace-wide reusable asset index.
-- Browser clients stay API-only; service_role performs all persistence.

begin;

alter table public.caption_tracks
  add column if not exists language text not null default 'original',
  add column if not exists source_track_id uuid references public.caption_tracks(id) on delete set null,
  add column if not exists review_status text not null default 'approved';

alter table public.caption_tracks
  drop constraint if exists caption_tracks_language_check,
  add constraint caption_tracks_language_check
    check (language in ('original', 'en', 'hi', 'es', 'pt', 'fr', 'de', 'ar')),
  drop constraint if exists caption_tracks_review_status_check,
  add constraint caption_tracks_review_status_check
    check (review_status in ('needs_review', 'approved'));

create unique index if not exists caption_tracks_workspace_project_id_idx
  on public.caption_tracks (workspace_id, project_id, id);
create index if not exists caption_tracks_project_language_idx
  on public.caption_tracks (workspace_id, project_id, language, updated_at desc);

create table if not exists public.localized_publish_packages (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null,
  source_caption_track_id uuid not null,
  caption_track_id uuid not null,
  edit_plan_id uuid,
  language text not null check (language in ('en', 'hi', 'es', 'pt', 'fr', 'de', 'ar')),
  review_status text not null default 'needs_review' check (review_status in ('needs_review', 'approved')),
  overlay_translations jsonb not null default '[]'::jsonb,
  title_suggestions jsonb not null default '[]'::jsonb,
  description_suggestions jsonb not null default '[]'::jsonb,
  selected_title text,
  selected_description text,
  revision integer not null default 1 check (revision > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint localized_publish_packages_project_tenant_fk
    foreign key (workspace_id, project_id)
    references public.projects (workspace_id, id) on delete cascade,
  constraint localized_publish_packages_source_track_tenant_fk
    foreign key (workspace_id, project_id, source_caption_track_id)
    references public.caption_tracks (workspace_id, project_id, id) on delete restrict,
  constraint localized_publish_packages_track_tenant_fk
    foreign key (workspace_id, project_id, caption_track_id)
    references public.caption_tracks (workspace_id, project_id, id) on delete restrict,
  constraint localized_publish_packages_plan_tenant_fk
    foreign key (workspace_id, project_id, edit_plan_id)
    references public.edit_plans (workspace_id, project_id, id) on delete set null,
  unique (workspace_id, project_id, id)
);

create index if not exists localized_publish_packages_project_updated_idx
  on public.localized_publish_packages (workspace_id, project_id, updated_at desc);

create table if not exists public.asset_library_items (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  kind text not null check (kind in ('broll', 'music', 'upload', 'export', 'clip', 'brand_asset', 'style')),
  name text not null check (char_length(name) between 1 and 180),
  description text not null default '' check (char_length(description) <= 600),
  source_project_id uuid,
  source_asset_id uuid,
  preview_url text,
  payload jsonb not null default '{}'::jsonb,
  search_text text generated always as (lower(name || ' ' || description || ' ' || kind)) stored,
  is_favorite boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint asset_library_items_project_tenant_fk
    foreign key (workspace_id, source_project_id)
    references public.projects (workspace_id, id) on delete set null
);

create unique index if not exists asset_library_items_workspace_source_key_idx
  on public.asset_library_items (workspace_id, (payload->>'source_key'))
  where payload ? 'source_key';
create index if not exists asset_library_items_workspace_search_idx
  on public.asset_library_items (workspace_id, is_favorite desc, updated_at desc);

create table if not exists public.asset_collections (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 120),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, id)
);

create table if not exists public.asset_collection_items (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  collection_id uuid not null,
  asset_id uuid not null,
  created_at timestamptz not null default now(),
  primary key (collection_id, asset_id),
  constraint asset_collection_items_collection_tenant_fk
    foreign key (workspace_id, collection_id)
    references public.asset_collections (workspace_id, id) on delete cascade,
  constraint asset_collection_items_asset_tenant_fk
    foreign key (workspace_id, asset_id)
    references public.asset_library_items (workspace_id, id) on delete cascade
);

create index if not exists asset_collection_items_workspace_collection_idx
  on public.asset_collection_items (workspace_id, collection_id, created_at desc);

create or replace function public.touch_localized_publishing_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

do $localized_publishing_triggers$
declare
  table_name text;
begin
  foreach table_name in array array['localized_publish_packages', 'asset_library_items', 'asset_collections'] loop
    execute format('drop trigger if exists %I on public.%I', table_name || '_touch_updated_at', table_name);
    execute format(
      'create trigger %I before update on public.%I for each row execute function public.touch_localized_publishing_updated_at()',
      table_name || '_touch_updated_at', table_name
    );
  end loop;
end;
$localized_publishing_triggers$;

alter table public.localized_publish_packages enable row level security;
alter table public.asset_library_items enable row level security;
alter table public.asset_collections enable row level security;
alter table public.asset_collection_items enable row level security;

revoke all on public.localized_publish_packages, public.asset_library_items,
  public.asset_collections, public.asset_collection_items from public, anon, authenticated;
grant select, insert, update, delete on public.localized_publish_packages,
  public.asset_library_items, public.asset_collections, public.asset_collection_items to service_role;

commit;
