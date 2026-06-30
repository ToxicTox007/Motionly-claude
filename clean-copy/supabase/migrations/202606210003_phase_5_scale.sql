alter type public.stock_provider add value if not exists 'runway';

create table if not exists public.phase5_resources (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  kind text not null,
  name text not null,
  status text not null check (status in ('draft','queued','processing','ready','completed','partial','failed')),
  payload jsonb not null default '{}'::jsonb,
  result jsonb not null default '{}'::jsonb,
  revision integer not null default 1,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists phase5_resources_workspace_kind_idx
  on public.phase5_resources(workspace_id, kind, updated_at desc);
create index if not exists phase5_resources_project_kind_idx
  on public.phase5_resources(project_id, kind, updated_at desc)
  where project_id is not null;
create unique index if not exists phase5_collaboration_project_idx
  on public.phase5_resources(project_id)
  where kind = 'collaboration_manifest';

alter table public.phase5_resources enable row level security;
drop policy if exists "Workspace members can manage phase5 resources" on public.phase5_resources;
create policy "Workspace members can manage phase5 resources"
  on public.phase5_resources for all
  using (public.is_workspace_member(workspace_id))
  with check (public.is_workspace_member(workspace_id));

do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime')
     and not exists (
       select 1 from pg_publication_tables
       where pubname = 'supabase_realtime'
         and schemaname = 'public'
         and tablename = 'phase5_resources'
     ) then
    alter publication supabase_realtime add table public.phase5_resources;
  end if;
end $$;
