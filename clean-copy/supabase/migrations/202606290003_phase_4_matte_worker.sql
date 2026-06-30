do $$
begin
  if not exists (select 1 from pg_type where typname = 'matte_status') then
    create type public.matte_status as enum (
      'pending',
      'processing',
      'completed',
      'failed',
      'skipped'
    );
  end if;
end $$;

create table if not exists public.segmentation_mattes (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  uploaded_video_id uuid not null references public.uploaded_videos(id) on delete cascade,
  status public.matte_status not null default 'pending',
  cache_key text not null,
  source_file_hash text,
  segmentation_settings jsonb not null default '{}'::jsonb,
  model_version text not null,
  matte jsonb,
  error_code text,
  error_message text,
  retry_count integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (uploaded_video_id, cache_key)
);

create index if not exists segmentation_mattes_upload_updated_idx
on public.segmentation_mattes (workspace_id, project_id, uploaded_video_id, updated_at desc);

create index if not exists segmentation_mattes_status_idx
on public.segmentation_mattes (workspace_id, status, updated_at desc);

drop trigger if exists segmentation_mattes_touch_updated_at on public.segmentation_mattes;
create trigger segmentation_mattes_touch_updated_at
before update on public.segmentation_mattes
for each row execute function public.touch_updated_at();

alter table public.segmentation_mattes enable row level security;

drop policy if exists "members read segmentation mattes" on public.segmentation_mattes;
create policy "members read segmentation mattes"
on public.segmentation_mattes for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members manage segmentation mattes" on public.segmentation_mattes;
create policy "members manage segmentation mattes"
on public.segmentation_mattes for all
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));
