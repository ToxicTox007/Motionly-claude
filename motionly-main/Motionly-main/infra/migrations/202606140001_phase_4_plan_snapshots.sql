create table if not exists public.edit_plan_snapshots (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  source_plan_id uuid not null references public.edit_plans(id) on delete cascade,
  source_plan_version integer not null,
  transcript_id uuid not null references public.transcripts(id) on delete cascade,
  schema_version integer not null,
  style text not null,
  target_duration_s integer not null,
  broll_density text not null,
  prompt text not null default '',
  feedback text,
  provider text not null,
  model text not null,
  cache_key text not null,
  schema_hash text not null,
  summary jsonb not null,
  plan jsonb not null,
  name text not null,
  notes text,
  created_by_user_id uuid not null references auth.users(id) on delete restrict,
  validation_errors jsonb not null default '[]'::jsonb,
  latency_ms integer,
  cost_estimate_usd numeric,
  created_at timestamptz not null default now()
);

create index if not exists edit_plan_snapshots_project_created_idx
  on public.edit_plan_snapshots(project_id, created_at desc);

alter table public.edit_plan_snapshots enable row level security;

drop policy if exists "Workspace members can read edit plan snapshots"
  on public.edit_plan_snapshots;
create policy "Workspace members can read edit plan snapshots"
  on public.edit_plan_snapshots
  for select
  using (public.is_workspace_member(workspace_id));

drop policy if exists "Workspace members can insert edit plan snapshots"
  on public.edit_plan_snapshots;
create policy "Workspace members can insert edit plan snapshots"
  on public.edit_plan_snapshots
  for insert
  with check (public.is_workspace_member(workspace_id));
