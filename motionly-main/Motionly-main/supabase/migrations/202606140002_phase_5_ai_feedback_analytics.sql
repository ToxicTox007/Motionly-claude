create table if not exists public.ai_edit_feedback_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  edit_plan_id uuid references public.edit_plans(id) on delete set null,
  user_id uuid not null references auth.users(id) on delete restrict,
  event_type text not null check (
    event_type in ('ai_suggested', 'kept', 'changed', 'rejected')
  ),
  layer text not null check (
    layer in ('cuts', 'captions', 'broll', 'music', 'effects', 'render')
  ),
  decision_id text,
  action text,
  source text not null,
  ai_payload jsonb not null default '{}'::jsonb,
  user_payload jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists ai_edit_feedback_project_created_idx
  on public.ai_edit_feedback_events(project_id, created_at desc);

create index if not exists ai_edit_feedback_project_layer_idx
  on public.ai_edit_feedback_events(project_id, layer, event_type, created_at desc);

create index if not exists ai_edit_feedback_plan_idx
  on public.ai_edit_feedback_events(edit_plan_id, created_at desc)
  where edit_plan_id is not null;

alter table public.ai_edit_feedback_events enable row level security;

drop policy if exists "Workspace members can read ai edit feedback"
  on public.ai_edit_feedback_events;
create policy "Workspace members can read ai edit feedback"
  on public.ai_edit_feedback_events
  for select
  using (public.is_workspace_member(workspace_id));

drop policy if exists "Workspace members can insert ai edit feedback"
  on public.ai_edit_feedback_events;
create policy "Workspace members can insert ai edit feedback"
  on public.ai_edit_feedback_events
  for insert
  with check (public.is_workspace_member(workspace_id));
