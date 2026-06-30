-- Phase 0 production schema repair.
-- Makes the final schema explicit for both fresh installs and databases upgraded
-- through the original week-by-week migration chain.

-- The foundation migration created the compatibility states below. Keep those
-- values readable for existing rows, and add every state written by current code.
alter type public.render_job_status add value if not exists 'preparing';
alter type public.render_job_status add value if not exists 'rendering';
alter type public.render_job_status add value if not exists 'uploading';
alter type public.render_job_status add value if not exists 'complete';

alter table public.render_jobs
  add column if not exists uploaded_video_id uuid references public.uploaded_videos(id) on delete cascade,
  add column if not exists caption_track_id uuid references public.caption_tracks(id) on delete cascade,
  add column if not exists planner_version text,
  add column if not exists manifest jsonb not null default '{}'::jsonb,
  add column if not exists keep_segments jsonb not null default '[]'::jsonb,
  add column if not exists specs jsonb not null default '{}'::jsonb,
  add column if not exists progress_pct integer not null default 0,
  add column if not exists cost_estimate_usd numeric,
  add column if not exists latency_ms integer;

update public.render_jobs
set planner_version = 'legacy'
where planner_version is null;

alter table public.render_jobs
  alter column planner_version set default 'unknown',
  alter column planner_version set not null;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.render_jobs'::regclass
      and conname = 'render_jobs_progress_pct_check'
  ) then
    alter table public.render_jobs
      add constraint render_jobs_progress_pct_check
      check (progress_pct between 0 and 100);
  end if;

  -- Preserve legacy rows that predate upload/caption references while enforcing
  -- the current contract for every new or updated row.
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.render_jobs'::regclass
      and conname = 'render_jobs_uploaded_video_required'
  ) then
    alter table public.render_jobs
      add constraint render_jobs_uploaded_video_required
      check (uploaded_video_id is not null) not valid;
  end if;
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.render_jobs'::regclass
      and conname = 'render_jobs_caption_track_required'
  ) then
    alter table public.render_jobs
      add constraint render_jobs_caption_track_required
      check (caption_track_id is not null) not valid;
  end if;
end $$;

do $$
begin
  if not exists (select 1 from public.render_jobs where uploaded_video_id is null) then
    alter table public.render_jobs alter column uploaded_video_id set not null;
    alter table public.render_jobs drop constraint if exists render_jobs_uploaded_video_required;
  end if;
  if not exists (select 1 from public.render_jobs where caption_track_id is null) then
    alter table public.render_jobs alter column caption_track_id set not null;
    alter table public.render_jobs drop constraint if exists render_jobs_caption_track_required;
  end if;
end $$;

alter table public.exports
  add column if not exists format public.export_format not null default 'mp4',
  add column if not exists width integer not null default 1080,
  add column if not exists height integer not null default 1920,
  add column if not exists fps integer not null default 30,
  add column if not exists duration_ms integer,
  add column if not exists file_size_bytes bigint,
  add column if not exists thumbnail_url text,
  add column if not exists settings_summary jsonb not null default '{}'::jsonb,
  add column if not exists restored_from_export_id uuid references public.exports(id) on delete set null;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.exports'::regclass
      and conname = 'exports_render_job_required'
  ) then
    alter table public.exports
      add constraint exports_render_job_required
      check (render_job_id is not null) not valid;
  end if;
  if not exists (
    select 1 from public.exports where render_job_id is null
  ) then
    alter table public.exports alter column render_job_id set not null;
    alter table public.exports drop constraint if exists exports_render_job_required;
  end if;
end $$;

-- Canonical billing shape used by SupabaseBillingRepository.
update public.subscriptions
set plan = coalesce(plan, 'free'),
    status = coalesce(status, 'none');

alter table public.subscriptions
  alter column plan set default 'free',
  alter column plan set not null,
  alter column status set default 'none',
  alter column status set not null;

alter table public.subscriptions drop constraint if exists subscriptions_plan_check;
alter table public.subscriptions
  add constraint subscriptions_plan_check
  check (plan in ('free', 'creator', 'pro', 'agency'));

alter table public.subscriptions drop constraint if exists subscriptions_status_check;
alter table public.subscriptions
  add constraint subscriptions_status_check
  check (status in ('none', 'trialing', 'active', 'past_due', 'canceled', 'unpaid', 'incomplete'));

-- The original primary key was an unused surrogate ID. Re-key safely to the
-- workspace identifier used for every repository upsert.
do $$
declare
  primary_key_columns text[];
begin
  select array_agg(att.attname order by key_columns.ordinality)
  into primary_key_columns
  from pg_constraint con
  cross join lateral unnest(con.conkey) with ordinality as key_columns(attnum, ordinality)
  join pg_attribute att
    on att.attrelid = con.conrelid and att.attnum = key_columns.attnum
  where con.conrelid = 'public.subscriptions'::regclass
    and con.contype = 'p';

  if primary_key_columns is distinct from array['workspace_id']::text[] then
    alter table public.subscriptions drop constraint if exists subscriptions_pkey;
    drop index if exists public.subscriptions_workspace_unique_idx;
    alter table public.subscriptions
      add constraint subscriptions_pkey primary key (workspace_id);
  else
    drop index if exists public.subscriptions_workspace_unique_idx;
  end if;
end $$;

alter table public.subscriptions
  drop column if exists id,
  drop column if exists plan_key;

alter table public.usage_ledger
  add column if not exists source_id uuid;

alter table public.usage_ledger drop constraint if exists usage_ledger_event_type_check;
alter table public.usage_ledger
  add constraint usage_ledger_event_type_check
  check (event_type in ('render_started', 'premium_action')) not valid;

alter table public.usage_ledger drop column if exists project_id;

drop index if exists public.subscriptions_workspace_idx;
drop index if exists public.subscriptions_stripe_customer_id_idx;
drop index if exists public.subscriptions_stripe_subscription_id_idx;
drop index if exists public.usage_ledger_workspace_created_idx;

create index if not exists usage_ledger_workspace_created_at_idx
  on public.usage_ledger(workspace_id, created_at desc);

drop type if exists public.subscription_status;

-- New users must be inserted using the canonical billing columns, not the
-- removed foundation-era plan_key column.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  new_workspace_id uuid;
  display_name text;
begin
  display_name := coalesce(
    new.raw_user_meta_data->>'full_name',
    split_part(new.email, '@', 1),
    'Creator'
  );

  insert into public.profiles (id, email, full_name, avatar_url)
  values (new.id, new.email, display_name, new.raw_user_meta_data->>'avatar_url');

  insert into public.workspaces (name, owner_user_id)
  values (display_name || '''s workspace', new.id)
  returning id into new_workspace_id;

  insert into public.workspace_members (workspace_id, user_id, role, status)
  values (new_workspace_id, new.id, 'owner', 'active');

  insert into public.subscriptions (workspace_id, plan, status)
  values (new_workspace_id, 'free', 'none')
  on conflict (workspace_id) do nothing;

  return new;
end;
$$;

-- These tables had no application reader or writer. Keeping unused security-
-- sensitive/operational tables only creates misleading schema surface.
drop table if exists public.launch_ops_events;
drop table if exists public.launch_feedback;
drop table if exists public.audit_logs;
