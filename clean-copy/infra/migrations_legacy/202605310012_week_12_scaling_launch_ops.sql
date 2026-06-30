-- Week 12: production scaling, observability, and beta launch operations.

create table if not exists public.launch_feedback (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid references public.workspaces(id) on delete set null,
  user_id uuid references auth.users(id) on delete set null,
  category text not null default 'general'
    check (category in ('general', 'bug', 'billing', 'render_quality', 'feature_request')),
  severity text not null default 'normal'
    check (severity in ('low', 'normal', 'high', 'urgent')),
  message text not null check (char_length(message) between 10 and 4000),
  page_url text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.launch_ops_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid references public.workspaces(id) on delete set null,
  project_id uuid references public.projects(id) on delete set null,
  render_job_id uuid references public.render_jobs(id) on delete set null,
  event_type text not null,
  severity text not null default 'info'
    check (severity in ('info', 'warning', 'error')),
  request_id text,
  error_code text,
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists launch_feedback_workspace_created_at_idx
  on public.launch_feedback(workspace_id, created_at desc);

create index if not exists launch_ops_events_created_at_idx
  on public.launch_ops_events(created_at desc);

create index if not exists launch_ops_events_render_job_id_idx
  on public.launch_ops_events(render_job_id);

alter table public.launch_feedback enable row level security;
alter table public.launch_ops_events enable row level security;

drop policy if exists "members insert feedback" on public.launch_feedback;
create policy "members insert feedback"
on public.launch_feedback for insert
with check (workspace_id is null or public.is_workspace_member(workspace_id));

drop policy if exists "members read own feedback" on public.launch_feedback;
create policy "members read own feedback"
on public.launch_feedback for select
using (workspace_id is not null and public.is_workspace_member(workspace_id));

-- Launch ops events are written and read by service-role API/admin tooling only.
