-- Default-off, API-owned editorial-learning governance. Operational analytics is
-- intentionally not a training dataset and historical feedback is never backfilled.

create table if not exists public.editorial_learning_policies (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  memory_enabled boolean not null default false,
  training_enabled boolean not null default false,
  retention_days integer not null default 90 check (retention_days between 1 and 365),
  policy_version text not null default 'unacknowledged' check (length(policy_version) <= 80),
  revision integer not null default 1 check (revision > 0),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, user_id)
);

create table if not exists public.editorial_learning_policy_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  user_id uuid not null,
  policy_id uuid references public.editorial_learning_policies(id) on delete set null,
  revision integer not null,
  event_type text not null check (event_type in ('opt_in','opt_out','revoke','retention_change','rollback')),
  reason text not null default 'user_request',
  created_at timestamptz not null default now()
);

create table if not exists public.editorial_feedback_deltas (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  source_event_id uuid unique,
  policy_id uuid references public.editorial_learning_policies(id) on delete set null,
  policy_revision integer not null,
  layer text not null,
  normalizer_version text not null default 'editorial-delta/v1',
  delta jsonb not null check (jsonb_typeof(delta) = 'object'),
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  memory_eligible boolean not null default false,
  training_eligible boolean not null default false,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  unique (source_event_id, normalizer_version)
);

create table if not exists public.editorial_training_datasets (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  status text not null check (status in ('building','frozen','shadow_passed','rejected','promoted','revoked','deleted')),
  manifest_hash text not null check (manifest_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  revoked_at timestamptz
);
create table if not exists public.editorial_training_dataset_items (
  dataset_id uuid not null references public.editorial_training_datasets(id) on delete cascade,
  delta_id uuid not null references public.editorial_feedback_deltas(id) on delete restrict,
  policy_revision integer not null,
  split text not null check (split in ('train','eval')),
  item_hash text not null check (item_hash ~ '^[0-9a-f]{64}$'),
  primary key (dataset_id, delta_id)
);

create table if not exists public.editorial_learning_evaluations (
  id uuid primary key default gen_random_uuid(),
  dataset_id uuid not null references public.editorial_training_datasets(id) on delete cascade,
  threshold_version text not null,
  sample_count integer not null check (sample_count >= 0),
  control_metrics jsonb not null default '{}'::jsonb,
  candidate_metrics jsonb not null default '{}'::jsonb,
  verdict text not null check (verdict in ('passed','rejected')),
  created_at timestamptz not null default now()
);

create table if not exists public.editorial_learning_deployments (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  active_dataset_id uuid references public.editorial_training_datasets(id) on delete set null,
  previous_dataset_id uuid references public.editorial_training_datasets(id) on delete set null,
  state text not null default 'disabled' check (state in ('disabled','shadow','active','rolled_back','revoked')),
  traffic_percent integer not null default 0 check (traffic_percent between 0 and 100),
  revision integer not null default 0,
  rolled_back_at timestamptz,
  reason text,
  updated_at timestamptz not null default now(),
  primary key (workspace_id, project_id, user_id)
);

create index if not exists editorial_learning_deltas_eligible_idx on public.editorial_feedback_deltas (workspace_id, project_id, user_id, created_at) where training_eligible and revoked_at is null;
create index if not exists editorial_learning_dataset_items_idx on public.editorial_training_dataset_items (dataset_id, delta_id);

alter table public.editorial_learning_policies enable row level security;
alter table public.editorial_learning_policy_events enable row level security;
alter table public.editorial_feedback_deltas enable row level security;
alter table public.editorial_training_datasets enable row level security;
alter table public.editorial_training_dataset_items enable row level security;
alter table public.editorial_learning_evaluations enable row level security;
alter table public.editorial_learning_deployments enable row level security;
revoke all on public.editorial_learning_policies, public.editorial_learning_policy_events, public.editorial_feedback_deltas, public.editorial_training_datasets, public.editorial_training_dataset_items, public.editorial_learning_evaluations, public.editorial_learning_deployments from public, anon, authenticated;
grant all on public.editorial_learning_policies, public.editorial_learning_policy_events, public.editorial_feedback_deltas, public.editorial_training_datasets, public.editorial_training_dataset_items, public.editorial_learning_evaluations, public.editorial_learning_deployments to service_role;

create or replace function public.motionly_revoke_editorial_learning(
  p_workspace_id uuid, p_user_id uuid, p_scope text default 'all', p_reason text default 'user_request'
) returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare policy_row public.editorial_learning_policies;
begin
  if p_scope not in ('memory','training','all') then raise exception 'EDITORIAL_SCOPE_INVALID' using errcode = '22023'; end if;
  select * into policy_row from public.editorial_learning_policies where workspace_id = p_workspace_id and user_id = p_user_id for update;
  if not found then return; end if;
  update public.editorial_learning_policies set
    memory_enabled = case when p_scope in ('memory','all') then false else memory_enabled end,
    training_enabled = case when p_scope in ('training','all') then false else training_enabled end,
    revoked_at = now(), revision = revision + 1, updated_at = now()
  where id = policy_row.id;
  update public.editorial_feedback_deltas set memory_eligible = false, training_eligible = false, revoked_at = now()
    where workspace_id = p_workspace_id and user_id = p_user_id and revoked_at is null;
  update public.editorial_training_datasets set status = 'revoked', revoked_at = now()
    where workspace_id = p_workspace_id and user_id = p_user_id and status not in ('deleted','revoked');
  update public.editorial_learning_deployments set active_dataset_id = previous_dataset_id, previous_dataset_id = null,
    state = case when previous_dataset_id is null then 'disabled' else 'rolled_back' end,
    traffic_percent = 0, revision = revision + 1, rolled_back_at = now(), reason = p_reason, updated_at = now()
    where workspace_id = p_workspace_id and user_id = p_user_id;
  insert into public.editorial_learning_policy_events(workspace_id,user_id,policy_id,revision,event_type,reason)
    values(p_workspace_id,p_user_id,policy_row.id,policy_row.revision + 1,'revoke',p_reason);
end; $$;
revoke all on function public.motionly_revoke_editorial_learning(uuid, uuid, text, text) from public, anon, authenticated;
grant execute on function public.motionly_revoke_editorial_learning(uuid, uuid, text, text) to service_role;
