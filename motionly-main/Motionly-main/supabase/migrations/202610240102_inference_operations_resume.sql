-- Tier 1 / Task 16B: resumable state for blocking async_job operations.
--
-- The async_job adapter (plan Task 16B, Section 2.10 "async_job") runs in
-- blocking mode: it reserves an operation, submits at the committed boundary,
-- then blocks on a bounded poll loop. When a worker retries after the commit
-- step it must resume polling the recorded job instead of resubmitting (plan
-- invariant 6 and the Task 17 stems "one improvement" row). Resuming needs the
-- captured variables the poll and cleanup requests reference — the opaque job or
-- task id, and any captured URLs — which are not part of the immutable
-- route_snapshot (that holds route data, not per-call captures).
--
-- This adds a single additive jsonb column, resume_vars, that holds only
-- non-secret captured identifiers. It carries the same secret-value guard as
-- route_snapshot (plan invariant 5): a captured credential must never be
-- persisted here. The adapter persists resume_vars at the commit transition.
--
-- Forward-only. Not edited after it is applied (scripts/check_forward_migrations.sh).
begin;

alter table public.inference_operations
  add column resume_vars jsonb not null default '{}'::jsonb;

-- resume_vars carries only captured references (job id, task id, captured URLs),
-- never secret values. Reject the obvious secret-value keys, matching the
-- route_snapshot guard in 202610240101_inference_operations.sql (invariant 5).
alter table public.inference_operations
  add constraint inference_operations_resume_vars_no_secret_values_check check (
    not (resume_vars ?| array['api_key', 'apikey', 'token', 'secret', 'password', 'authorization', 'auth_token', 'access_token', 'bearer'])
  );

commit;
