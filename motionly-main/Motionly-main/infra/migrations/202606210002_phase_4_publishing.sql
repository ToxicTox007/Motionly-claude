create table if not exists public.publish_connections (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  platform text not null check (platform in ('tiktok', 'instagram', 'youtube')),
  access_token text not null,
  refresh_token text,
  token_expires_at timestamptz,
  external_account_id text not null,
  account_name text not null,
  created_at timestamptz not null default now(),
  unique (workspace_id, platform, external_account_id)
);

-- Credentials are intentionally service-role only. Workspace clients have no direct
-- RLS policy on this table; all reads/writes go through the authenticated API, which
-- strips access_token from responses.
alter table public.publish_connections enable row level security;

create table if not exists public.publish_jobs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  export_id uuid not null references public.exports(id) on delete cascade,
  connection_id uuid not null references public.publish_connections(id) on delete restrict,
  platform text not null check (platform in ('tiktok', 'instagram', 'youtube')),
  status text not null default 'queued' check (status in ('queued', 'publishing', 'published', 'failed')),
  title text not null,
  caption text not null default '',
  privacy text not null default 'private' check (privacy in ('private', 'public', 'unlisted')),
  external_post_id text,
  external_url text,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists publish_jobs_project_created_idx
  on public.publish_jobs(project_id, created_at desc);
create index if not exists publish_jobs_status_idx
  on public.publish_jobs(status, created_at);

alter table public.publish_jobs enable row level security;

drop policy if exists "Workspace members can read publish jobs" on public.publish_jobs;
create policy "Workspace members can read publish jobs"
  on public.publish_jobs for select
  using (public.is_workspace_member(workspace_id));
