-- Stage 2 visual intelligence manifests.
-- Scope: per-upload video_manifest.json storage, model metadata, status, versioning, and RLS.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'visual_manifest_status') then
    create type public.visual_manifest_status as enum (
      'queued',
      'processing',
      'ready',
      'failed'
    );
  end if;
end $$;

create table if not exists public.video_manifests (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  uploaded_video_id uuid not null references public.uploaded_videos(id) on delete cascade,
  version integer not null,
  schema_version integer not null default 1,
  status public.visual_manifest_status not null default 'queued',
  provider text not null,
  model text not null,
  manifest jsonb,
  error_code text,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (uploaded_video_id, version)
);

create index if not exists video_manifests_project_updated_idx
on public.video_manifests (workspace_id, project_id, updated_at desc);

create index if not exists video_manifests_upload_status_idx
on public.video_manifests (workspace_id, project_id, uploaded_video_id, status, updated_at desc);

drop trigger if exists video_manifests_touch_updated_at on public.video_manifests;
create trigger video_manifests_touch_updated_at
before update on public.video_manifests
for each row execute function public.touch_updated_at();

alter table public.video_manifests enable row level security;

drop policy if exists "members read video manifests" on public.video_manifests;
create policy "members read video manifests"
on public.video_manifests for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members manage video manifests" on public.video_manifests;
create policy "members manage video manifests"
on public.video_manifests for all
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));
