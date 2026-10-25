-- Tier 1 / Task 16A: persisted async inference operations and route affinity.
--
-- An asynchronous submission is route-affine (plan invariant 6): once a remote
-- job may exist, retries poll the original route snapshot and never submit to a
-- different route. This table is the durable record of that affinity. It stores
-- an idempotency key, the capability, the original route id/model/revision, an
-- immutable route snapshot, the external operation id when known, the state in
-- the Section 2.5 state machine, bounded error detail, cost accounting and
-- lifecycle timestamps.
--
-- "provider" here means an opaque external execution identity (plan Section
-- 2.5), not a hard-coded vendor. Routing and user-facing logic use
-- route/capability terminology.
--
-- Security posture: API-authoritative (plan Section 2.6 and the pattern of
-- 202607140004 provider_slo_events / cost_attribution_events). No client ever
-- reads or writes this table directly: anon and authenticated get nothing,
-- service_role gets everything, and motionly_worker gets the least privilege it
-- needs to reserve, submit and reconcile operations (SELECT/INSERT/UPDATE, no
-- DELETE), following 202610230101_task18_worker_least_privilege.sql.
--
-- The route_snapshot must hold only a secret *reference* name, never a secret
-- value (plan invariant 5). A CHECK rejects the obvious secret-value keys so a
-- future caller cannot persist a live credential by mistake.
--
-- Forward-only. Not edited after it is applied (scripts/check_forward_migrations.sh).
begin;

create table public.inference_operations (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces (id) on delete cascade,
  -- Every current async caller (diarization, stem separation, generated video)
  -- runs inside a workspace-scoped job, so workspace_id is mandatory. project_id
  -- is optional because an operation can be workspace-scoped without a project,
  -- but when present it must stay in the same tenant as the operation.
  project_id uuid,
  capability text not null,
  idempotency_key text not null,
  provider_route_id text not null,
  provider_model text not null,
  provider_model_revision text not null,
  route_snapshot jsonb not null,
  provider_operation_id text,
  state text not null default 'reserved',
  error_code text,
  error_detail text,
  estimated_cost_usd numeric,
  actual_cost_usd numeric,
  cost_unknown boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  submitted_at timestamptz,
  completed_at timestamptz,
  constraint inference_operations_capability_check check (
    capability in (
      'planning.chat',
      'transcription.speech_to_text',
      'vision.caption',
      'vision.object_detection',
      'embeddings.text',
      'embeddings.image',
      'audio.diarization',
      'audio.emotion',
      'audio.stem_separation',
      'image.generation',
      'video.generation.submit',
      'video.generation.status',
      'matte.segmentation',
      'audio.sound_classification'
    )
  ),
  constraint inference_operations_state_check check (
    state in (
      'reserved',
      'submitting',
      'submitted',
      'submit_unknown',
      'running',
      'succeeded',
      'failed',
      'cancelled',
      'needs_review'
    )
  ),
  -- Bound error_detail so a provider payload (which may embed secrets or large
  -- bodies) can never be stored verbatim. Callers strip it to a plain message
  -- first; this is the backstop.
  constraint inference_operations_error_detail_length_check check (
    error_detail is null or char_length(error_detail) <= 2000
  ),
  constraint inference_operations_error_code_length_check check (
    error_code is null or char_length(error_code) <= 200
  ),
  -- A route snapshot carries only references, never secret values. Reject the
  -- obvious secret-value keys (invariant 5). "secret_ref_name" is allowed
  -- because it is a reference, not a value.
  constraint inference_operations_route_snapshot_no_secret_values_check check (
    not (route_snapshot ?| array['api_key', 'apikey', 'token', 'secret', 'password', 'authorization', 'auth_token', 'access_token', 'bearer'])
  )
);

-- Idempotency is scoped per workspace: the same (workspace_id, idempotency_key)
-- reserves exactly one operation. workspace_id is NOT NULL, so a partial index
-- is unnecessary.
create unique index inference_operations_workspace_idempotency_key
  on public.inference_operations (workspace_id, idempotency_key);

-- An external operation id is unique per route where it is known. A null id
-- (reserved / submit_unknown before reconciliation) does not participate.
create unique index inference_operations_route_provider_operation_id
  on public.inference_operations (provider_route_id, provider_operation_id)
  where provider_operation_id is not null;

-- Route-affine reconciliation reads non-terminal operations for a route.
create index inference_operations_route_non_terminal
  on public.inference_operations (provider_route_id)
  where state not in ('succeeded', 'failed', 'cancelled');

-- /health/dependencies counts operations by state (e.g. submit_unknown).
create index inference_operations_state
  on public.inference_operations (state);

-- Tenant integrity: the project reference terminates at (workspace_id, id), the
-- same composite key pattern as 202608090012 / 202607140004. project_id null is
-- allowed; a present project must be in this workspace.
alter table public.inference_operations
  add constraint inference_operations_project_tenant_fk
  foreign key (workspace_id, project_id)
  references public.projects (workspace_id, id)
  on delete cascade;

-- Standard updated_at trigger (public.touch_updated_at, 202605300001).
create trigger inference_operations_touch_updated_at
before update on public.inference_operations
for each row execute function public.touch_updated_at();

-- Row level security: API-authoritative. Clients never see this table.
alter table public.inference_operations enable row level security;
revoke all privileges on table public.inference_operations from anon, authenticated;
grant all privileges on table public.inference_operations to service_role;

-- Least-privilege worker grant (pattern: 202610230101_task18_worker_least_privilege.sql).
-- Worker code reaches this table to reserve, submit and reconcile operations, so
-- the worker needs SELECT/INSERT/UPDATE. It never deletes: operation history is
-- durable and only the service role prunes it. apps/api/tests/test_worker_db_grants.py
-- checks this allowlist against worker-reachable code in both directions.
grant select, insert, update on public.inference_operations to motionly_worker;

-- The worker does not bypass RLS and processes jobs for every workspace, so a
-- new RLS table it is granted needs a worker row policy or it sees no rows.
-- supabase/tests/task_18_worker_row_access_test.sql asserts that exactly the
-- RLS tables granted to the worker carry this policy (pattern:
-- 202610200301_task18_worker_row_access.sql).
create policy motionly_worker_jobs on public.inference_operations
  for all to motionly_worker using (true) with check (true);

commit;
