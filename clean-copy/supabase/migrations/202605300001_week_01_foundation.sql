-- Week 1 foundation schema draft for Supabase.
-- Scope: accounts, workspaces, projects, early artifact tables, storage buckets, and RLS.

create extension if not exists pgcrypto;

create type public.workspace_role as enum ('owner', 'admin', 'member');
create type public.workspace_member_status as enum ('active', 'invited', 'disabled');
create type public.project_status as enum ('draft', 'ready', 'failed');
create type public.render_job_status as enum ('queued', 'running', 'succeeded', 'failed', 'canceled');
create type public.subscription_status as enum ('trialing', 'active', 'past_due', 'canceled', 'incomplete');

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  full_name text,
  avatar_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.workspace_members (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role public.workspace_role not null default 'member',
  status public.workspace_member_status not null default 'active',
  created_at timestamptz not null default now(),
  primary key (workspace_id, user_id)
);

create table public.projects (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  created_by uuid not null references auth.users(id) on delete restrict,
  title text not null,
  status public.project_status not null default 'draft',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.uploaded_videos (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  storage_bucket text not null,
  storage_path text not null,
  original_filename text not null,
  mime_type text not null,
  byte_size bigint not null check (byte_size > 0),
  created_at timestamptz not null default now()
);

create table public.render_jobs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  status public.render_job_status not null default 'queued',
  priority integer not null default 0,
  error_code text,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.exports (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  render_job_id uuid references public.render_jobs(id) on delete set null,
  version integer not null default 1,
  storage_bucket text not null,
  storage_path text not null,
  manifest jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (project_id, version)
);

create table public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  stripe_customer_id text unique,
  stripe_subscription_id text unique,
  status public.subscription_status,
  plan_key text not null default 'starter',
  current_period_end timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.usage_ledger (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid references public.projects(id) on delete set null,
  event_type text not null,
  quantity integer not null check (quantity > 0),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table public.audit_logs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid references public.workspaces(id) on delete cascade,
  actor_user_id uuid references auth.users(id) on delete set null,
  action text not null,
  target_table text,
  target_id uuid,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index projects_workspace_updated_idx on public.projects (workspace_id, updated_at desc);
create index uploaded_videos_project_idx on public.uploaded_videos (project_id);
create index render_jobs_status_priority_created_idx on public.render_jobs (status, priority desc, created_at);
create index render_jobs_project_created_idx on public.render_jobs (project_id, created_at desc);
create index exports_project_version_idx on public.exports (project_id, version desc);
create index usage_ledger_workspace_created_idx on public.usage_ledger (workspace_id, created_at desc);
create index subscriptions_workspace_idx on public.subscriptions (workspace_id);

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger profiles_touch_updated_at
before update on public.profiles
for each row execute function public.touch_updated_at();

create trigger workspaces_touch_updated_at
before update on public.workspaces
for each row execute function public.touch_updated_at();

create trigger projects_touch_updated_at
before update on public.projects
for each row execute function public.touch_updated_at();

create trigger render_jobs_touch_updated_at
before update on public.render_jobs
for each row execute function public.touch_updated_at();

create trigger subscriptions_touch_updated_at
before update on public.subscriptions
for each row execute function public.touch_updated_at();

create or replace function public.is_workspace_member(target_workspace_id uuid)
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.workspace_members wm
    where wm.workspace_id = target_workspace_id
      and wm.user_id = auth.uid()
      and wm.status = 'active'
  );
$$;

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
  display_name := coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email, '@', 1), 'Creator');

  insert into public.profiles (id, email, full_name, avatar_url)
  values (new.id, new.email, display_name, new.raw_user_meta_data->>'avatar_url');

  insert into public.workspaces (name, owner_user_id)
  values (display_name || '''s workspace', new.id)
  returning id into new_workspace_id;

  insert into public.workspace_members (workspace_id, user_id, role, status)
  values (new_workspace_id, new.id, 'owner', 'active');

  insert into public.subscriptions (workspace_id, plan_key, status)
  values (new_workspace_id, 'starter', 'trialing');

  return new;
end;
$$;

create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_user();

alter table public.profiles enable row level security;
alter table public.workspaces enable row level security;
alter table public.workspace_members enable row level security;
alter table public.projects enable row level security;
alter table public.uploaded_videos enable row level security;
alter table public.render_jobs enable row level security;
alter table public.exports enable row level security;
alter table public.subscriptions enable row level security;
alter table public.usage_ledger enable row level security;
alter table public.audit_logs enable row level security;

create policy "users read own profile"
on public.profiles for select
using (id = auth.uid());

create policy "users update own profile"
on public.profiles for update
using (id = auth.uid())
with check (id = auth.uid());

create policy "members read workspaces"
on public.workspaces for select
using (public.is_workspace_member(id));

create policy "members read workspace memberships"
on public.workspace_members for select
using (public.is_workspace_member(workspace_id));

create policy "members read projects"
on public.projects for select
using (public.is_workspace_member(workspace_id));

create policy "members create projects"
on public.projects for insert
with check (public.is_workspace_member(workspace_id) and created_by = auth.uid());

create policy "members update projects"
on public.projects for update
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));

create policy "members read uploaded videos"
on public.uploaded_videos for select
using (public.is_workspace_member(workspace_id));

create policy "members read render jobs"
on public.render_jobs for select
using (public.is_workspace_member(workspace_id));

create policy "members read exports"
on public.exports for select
using (public.is_workspace_member(workspace_id));

create policy "members read subscriptions"
on public.subscriptions for select
using (public.is_workspace_member(workspace_id));

create policy "members read usage ledger"
on public.usage_ledger for select
using (public.is_workspace_member(workspace_id));

create policy "members read audit logs"
on public.audit_logs for select
using (public.is_workspace_member(workspace_id));

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values
  ('raw-uploads', 'raw-uploads', false, 536870912, array['video/mp4', 'video/quicktime', 'video/webm']),
  ('thumbnails', 'thumbnails', false, 10485760, array['image/jpeg', 'image/png', 'image/webp']),
  ('exports', 'exports', false, 536870912, array['video/mp4']),
  ('temp-previews', 'temp-previews', false, 104857600, array['video/mp4', 'image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do nothing;
