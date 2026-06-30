-- Week 2 upload and preprocessing foundation.
-- Scope: upload state machine, metadata fields, events, active project video, and storage policies.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'uploaded_video_status') then
    create type public.uploaded_video_status as enum (
      'created',
      'uploading',
      'uploaded',
      'processing',
      'ready',
      'failed'
    );
  end if;
end $$;

alter table public.projects
add column if not exists active_video_id uuid;

alter table public.uploaded_videos
add column if not exists file_size_bytes bigint,
add column if not exists duration_ms integer,
add column if not exists width integer,
add column if not exists height integer,
add column if not exists fps numeric,
add column if not exists codec text,
add column if not exists bitrate bigint,
add column if not exists frame_count integer,
add column if not exists has_audio boolean,
add column if not exists orientation text,
add column if not exists thumbnail_path text,
add column if not exists status public.uploaded_video_status not null default 'created',
add column if not exists error_code text,
add column if not exists error_message text,
add column if not exists metadata jsonb not null default '{}'::jsonb,
add column if not exists updated_at timestamptz not null default now();

update public.uploaded_videos
set file_size_bytes = byte_size
where file_size_bytes is null and byte_size is not null;

alter table public.uploaded_videos
alter column file_size_bytes set not null;

alter table public.uploaded_videos
drop constraint if exists uploaded_videos_file_size_bytes_check;

alter table public.uploaded_videos
add constraint uploaded_videos_file_size_bytes_check check (file_size_bytes > 0);

alter table public.uploaded_videos
drop constraint if exists uploaded_videos_orientation_check;

alter table public.uploaded_videos
add constraint uploaded_videos_orientation_check
check (orientation is null or orientation in ('vertical', 'landscape', 'square', 'unknown'));

alter table public.uploaded_videos
drop constraint if exists uploaded_videos_status_ready_metadata_check;

alter table public.uploaded_videos
add constraint uploaded_videos_status_ready_metadata_check
check (status <> 'ready' or (duration_ms is not null and width is not null and height is not null));

create table if not exists public.upload_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  uploaded_video_id uuid not null references public.uploaded_videos(id) on delete cascade,
  event_type text not null,
  from_status public.uploaded_video_status,
  to_status public.uploaded_video_status,
  job_id text,
  request_id text,
  error_code text,
  error_message text,
  latency_ms integer,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists uploaded_videos_workspace_status_created_idx
on public.uploaded_videos (workspace_id, status, created_at desc);

create index if not exists upload_events_video_created_idx
on public.upload_events (uploaded_video_id, created_at desc);

drop trigger if exists uploaded_videos_touch_updated_at on public.uploaded_videos;
create trigger uploaded_videos_touch_updated_at
before update on public.uploaded_videos
for each row execute function public.touch_updated_at();

alter table public.upload_events enable row level security;

drop policy if exists "members read upload events" on public.upload_events;
create policy "members read upload events"
on public.upload_events for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members insert uploaded videos" on public.uploaded_videos;
create policy "members insert uploaded videos"
on public.uploaded_videos for insert
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members update uploaded videos" on public.uploaded_videos;
create policy "members update uploaded videos"
on public.uploaded_videos for update
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members insert upload events" on public.upload_events;
create policy "members insert upload events"
on public.upload_events for insert
with check (public.is_workspace_member(workspace_id));

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'projects_active_video_id_fkey'
  ) then
    alter table public.projects
    add constraint projects_active_video_id_fkey
    foreign key (active_video_id) references public.uploaded_videos(id) on delete set null;
  end if;
end $$;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values
  ('raw-uploads', 'raw-uploads', false, 536870912, array['video/mp4', 'video/quicktime', 'video/webm']),
  ('thumbnails', 'thumbnails', false, 10485760, array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update
set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
