-- Week 4 basic auto editor and first export.
-- Scope: cut-plan backed render jobs, render events, exports bucket, and RLS.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'render_job_status') then
    create type public.render_job_status as enum (
      'queued',
      'preparing',
      'rendering',
      'uploading',
      'complete',
      'failed',
      'canceled'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'export_format') then
    create type public.export_format as enum ('mp4');
  end if;
end $$;

create table if not exists public.render_jobs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  uploaded_video_id uuid not null references public.uploaded_videos(id) on delete cascade,
  caption_track_id uuid not null references public.caption_tracks(id) on delete cascade,
  status public.render_job_status not null default 'queued',
  priority integer not null default 0,
  planner_version text not null,
  manifest jsonb not null default '{}'::jsonb,
  keep_segments jsonb not null default '[]'::jsonb,
  specs jsonb not null default '{}'::jsonb,
  progress_pct integer not null default 0 check (progress_pct between 0 and 100),
  error_code text,
  error_message text,
  cost_estimate_usd numeric,
  latency_ms integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.render_job_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  render_job_id uuid not null references public.render_jobs(id) on delete cascade,
  event_type text not null,
  from_status public.render_job_status,
  to_status public.render_job_status not null,
  progress_pct integer not null default 0 check (progress_pct between 0 and 100),
  message text not null,
  error_code text,
  latency_ms integer,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.exports (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  render_job_id uuid not null references public.render_jobs(id) on delete cascade,
  version integer not null check (version > 0),
  storage_bucket text not null,
  storage_path text not null,
  format public.export_format not null default 'mp4',
  width integer not null default 1080,
  height integer not null default 1920,
  fps integer not null default 30,
  duration_ms integer,
  file_size_bytes bigint,
  created_at timestamptz not null default now(),
  unique (project_id, version)
);

create index if not exists render_jobs_status_priority_created_idx
on public.render_jobs (status, priority desc, created_at);

create index if not exists render_jobs_project_created_idx
on public.render_jobs (project_id, created_at desc);

create index if not exists render_job_events_job_created_idx
on public.render_job_events (render_job_id, created_at);

create index if not exists exports_project_version_idx
on public.exports (project_id, version desc);

drop trigger if exists render_jobs_touch_updated_at on public.render_jobs;
create trigger render_jobs_touch_updated_at
before update on public.render_jobs
for each row execute function public.touch_updated_at();

alter table public.render_jobs enable row level security;
alter table public.render_job_events enable row level security;
alter table public.exports enable row level security;

drop policy if exists "members read render jobs" on public.render_jobs;
create policy "members read render jobs"
on public.render_jobs for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members insert render jobs" on public.render_jobs;
create policy "members insert render jobs"
on public.render_jobs for insert
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members update render jobs" on public.render_jobs;
create policy "members update render jobs"
on public.render_jobs for update
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members read render job events" on public.render_job_events;
create policy "members read render job events"
on public.render_job_events for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members insert render job events" on public.render_job_events;
create policy "members insert render job events"
on public.render_job_events for insert
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members read exports" on public.exports;
create policy "members read exports"
on public.exports for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members insert exports" on public.exports;
create policy "members insert exports"
on public.exports for insert
with check (public.is_workspace_member(workspace_id));

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('exports', 'exports', false, 1073741824, array['video/mp4'])
on conflict (id) do update
set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
