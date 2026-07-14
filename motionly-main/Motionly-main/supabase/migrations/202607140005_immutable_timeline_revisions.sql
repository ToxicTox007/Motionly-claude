-- Immutable timeline revisions and source lineage.
-- Scope: optimistic concurrency for edit_plans, media revision tracking,
-- source lineage on derivatives, staleness detection, and append-only audit log.

-- ============================================================================
-- 1. Add `revision` column to edit_plans for optimistic concurrency
-- ============================================================================

alter table public.edit_plans
  add column if not exists revision int not null default 1;

comment on column public.edit_plans.revision is
  'Monotonic counter incremented on every plan mutation. Used for optimistic concurrency.';

-- Trigger: auto-increment revision when the plan jsonb column changes.
create or replace function public.motionly_increment_plan_revision()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if NEW.plan is distinct from OLD.plan then
    NEW.revision := OLD.revision + 1;
  end if;
  return NEW;
end;
$$;

drop trigger if exists edit_plans_increment_revision on public.edit_plans;
create trigger edit_plans_increment_revision
before update on public.edit_plans
for each row execute function public.motionly_increment_plan_revision();

-- ============================================================================
-- 2. Add `media_revision` column to uploaded_videos
-- ============================================================================

alter table public.uploaded_videos
  add column if not exists media_revision int not null default 1;

comment on column public.uploaded_videos.media_revision is
  'Incremented when source media is replaced (re-upload, re-encode).';

-- Trigger: auto-increment media_revision on relevant column changes.
create or replace function public.motionly_increment_media_revision()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if (NEW.storage_path is distinct from OLD.storage_path)
     or (NEW.byte_size is distinct from OLD.byte_size)
     or (NEW.duration_ms is distinct from OLD.duration_ms)
  then
    NEW.media_revision := OLD.media_revision + 1;
  end if;
  return NEW;
end;
$$;

drop trigger if exists uploaded_videos_increment_media_revision on public.uploaded_videos;
create trigger uploaded_videos_increment_media_revision
before update on public.uploaded_videos
for each row execute function public.motionly_increment_media_revision();

-- ============================================================================
-- 3. Add source lineage tracking to derivatives
-- ============================================================================

alter table public.render_jobs
  add column if not exists source_media_revision int,
  add column if not exists source_plan_revision int;

alter table public.broll_slots
  add column if not exists source_media_revision int,
  add column if not exists source_plan_revision int;

alter table public.project_music_selections
  add column if not exists source_plan_revision int;

alter table public.exports
  add column if not exists source_plan_revision int;

-- ============================================================================
-- 4. Add `stale` boolean to derivatives
-- ============================================================================

alter table public.render_jobs
  add column if not exists stale boolean not null default false;

alter table public.exports
  add column if not exists stale boolean not null default false;

alter table public.broll_slots
  add column if not exists stale boolean not null default false;

alter table public.project_music_selections
  add column if not exists stale boolean not null default false;

-- ============================================================================
-- 5. Functions to invalidate stale derivatives
-- ============================================================================

-- Mark derivatives stale where their source_plan_revision < current plan revision.
create or replace function public.motionly_invalidate_stale_derivatives(
  p_project_id uuid,
  p_plan_id uuid
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  current_rev int;
begin
  select revision into current_rev
  from public.edit_plans
  where id = p_plan_id;

  if current_rev is null then
    return;
  end if;

  update public.render_jobs
  set stale = true
  where project_id = p_project_id
    and stale = false
    and source_plan_revision is not null
    and source_plan_revision < current_rev;

  update public.exports
  set stale = true
  where project_id = p_project_id
    and stale = false
    and source_plan_revision is not null
    and source_plan_revision < current_rev;

  update public.broll_slots
  set stale = true
  where edit_plan_id = p_plan_id
    and stale = false
    and source_plan_revision is not null
    and source_plan_revision < current_rev;

  update public.project_music_selections
  set stale = true
  where edit_plan_id = p_plan_id
    and stale = false
    and source_plan_revision is not null
    and source_plan_revision < current_rev;
end;
$$;

-- Mark ALL derivatives stale when source media changes.
create or replace function public.motionly_invalidate_on_media_replace(
  p_project_id uuid
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  update public.render_jobs
  set stale = true
  where project_id = p_project_id
    and stale = false;

  update public.exports
  set stale = true
  where project_id = p_project_id
    and stale = false;

  update public.broll_slots
  set stale = true
  where project_id = p_project_id
    and stale = false;

  update public.project_music_selections
  set stale = true
  where project_id = p_project_id
    and stale = false;
end;
$$;

-- ============================================================================
-- 6. Optimistic concurrency function
-- ============================================================================

create or replace function public.motionly_update_plan_with_revision(
  p_plan_id uuid,
  p_expected_revision int,
  p_new_plan jsonb
)
returns setof public.edit_plans
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  updated_row public.edit_plans;
begin
  update public.edit_plans
  set plan = p_new_plan,
      updated_at = now()
  where id = p_plan_id
    and revision = p_expected_revision
  returning * into updated_row;

  if not found then
    raise exception 'REVISION_CONFLICT: edit_plan % expected revision % but current revision differs',
      p_plan_id, p_expected_revision
      using errcode = 'P0002';
  end if;

  -- Invalidate stale derivatives after successful update.
  perform public.motionly_invalidate_stale_derivatives(
    updated_row.project_id,
    updated_row.id
  );

  return next updated_row;
end;
$$;

-- ============================================================================
-- 7. Trigger for media replacement invalidation
-- ============================================================================

create or replace function public.motionly_trigger_media_replace_invalidation()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if (NEW.storage_path is distinct from OLD.storage_path)
     or (NEW.duration_ms is distinct from OLD.duration_ms)
  then
    perform public.motionly_invalidate_on_media_replace(NEW.project_id);
  end if;
  return NEW;
end;
$$;

drop trigger if exists uploaded_videos_invalidate_on_media_replace on public.uploaded_videos;
create trigger uploaded_videos_invalidate_on_media_replace
after update on public.uploaded_videos
for each row execute function public.motionly_trigger_media_replace_invalidation();

-- ============================================================================
-- 8. timeline_revisions audit table (append-only)
-- ============================================================================

create table if not exists public.timeline_revisions (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  edit_plan_id uuid not null references public.edit_plans(id) on delete cascade,
  revision int not null,
  plan_snapshot jsonb not null,
  changed_by uuid references auth.users(id) on delete set null,
  change_type text not null check (change_type in (
    'cuts', 'timeline', 'manual_elements', 'caption_highlights',
    'compile', 'ai_generate', 'restore'
  )),
  created_at timestamptz not null default now()
);

create index if not exists timeline_revisions_plan_revision_idx
on public.timeline_revisions (edit_plan_id, revision desc);

create index if not exists timeline_revisions_project_created_idx
on public.timeline_revisions (project_id, created_at desc);

-- ============================================================================
-- 9. RLS policies for timeline_revisions
-- ============================================================================

alter table public.timeline_revisions enable row level security;

-- Tenant-scoped SELECT for authenticated users (workspace membership).
drop policy if exists "members read timeline revisions" on public.timeline_revisions;
create policy "members read timeline revisions"
on public.timeline_revisions for select
to authenticated
using (public.is_workspace_member(workspace_id));

-- No INSERT/UPDATE/DELETE for authenticated role (service role only).
-- Explicitly deny mutations at the policy level.
drop policy if exists "deny insert timeline revisions" on public.timeline_revisions;
create policy "deny insert timeline revisions"
on public.timeline_revisions for insert
to authenticated
with check (false);

drop policy if exists "deny update timeline revisions" on public.timeline_revisions;
create policy "deny update timeline revisions"
on public.timeline_revisions for update
to authenticated
using (false)
with check (false);

drop policy if exists "deny delete timeline revisions" on public.timeline_revisions;
create policy "deny delete timeline revisions"
on public.timeline_revisions for delete
to authenticated
using (false);

-- ============================================================================
-- Indexes for staleness queries
-- ============================================================================

create index if not exists render_jobs_stale_project_idx
on public.render_jobs (project_id)
where stale = true;

create index if not exists exports_stale_project_idx
on public.exports (project_id)
where stale = true;

create index if not exists broll_slots_stale_plan_idx
on public.broll_slots (edit_plan_id)
where stale = true;

create index if not exists project_music_selections_stale_plan_idx
on public.project_music_selections (edit_plan_id)
where stale = true;
