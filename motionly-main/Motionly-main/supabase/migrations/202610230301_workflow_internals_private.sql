-- Workflow plumbing is not browser data.
--
-- 202608080001 / 202608080008 granted `authenticated` SELECT on every workflow
-- table with a member-read policy. Three of them are internal machinery rather
-- than anything a workspace member needs to see:
--
--   transactional_outbox    Temporal/BullMQ dispatch payloads, dedup keys,
--                           lease state and dispatch errors
--   workflow_run_commands   cancel/retry idempotency keys and request hashes
--   workflow_step_attempts  per-attempt queue job ids, attempt keys, error text
--
-- The web app never reads them (it reads workflow state through the API with
-- the service role), so any member could only use the grant to read another
-- member's dispatch internals straight from PostgREST. Revoke it. Members keep
-- reading the user-facing state (workflow_runs, workflow_steps,
-- workflow_events, workflow_run_artifacts) as before.
begin;

revoke select on public.transactional_outbox from authenticated, anon;
revoke select on public.workflow_run_commands from authenticated, anon;
revoke select on public.workflow_step_attempts from authenticated, anon;

drop policy if exists task7_members_read_workflow_outbox on public.transactional_outbox;
drop policy if exists task5_members_read_workflow_commands on public.workflow_run_commands;
drop policy if exists task7_members_read_workflow_attempts on public.workflow_step_attempts;

commit;
