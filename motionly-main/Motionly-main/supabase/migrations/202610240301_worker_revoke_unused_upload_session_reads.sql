-- Least privilege follow-up to 202610230101 (provider-neutral inference, Task 19).
--
-- 202610230101 derived the motionly_worker allowlist from worker-reachable code
-- (apps/api/tests/worker_db_reachability.py). Four upload-session tables were on
-- it only because a removed vendor video adapter called `inspect.signature`,
-- and the name-based reachability matched the unrelated
-- GenericMediaUploadSessionService.inspect, which reads these tables. No worker
-- code reads upload sessions: the Node worker never names them, and the Python
-- worker entry points never call the session services. With that adapter gone,
-- the grants are unused, and apps/api/tests/test_worker_db_grants.py requires
-- unused grants to be removed rather than accumulate.
--
-- Checked before revoking (static review; no table, view or policy the worker
-- uses depends on these grants):
--   * every trigger on uploaded_videos (a table the worker updates) runs a
--     SECURITY DEFINER function or touch_updated_at, and none reads these tables;
--   * the only policies that name these tables are the member-read policies on
--     the two generic-media tables, which apply to `authenticated`, not the worker;
--   * no view reads these tables.
--
-- The worker row policy on each table is dropped too: only RLS tables granted to
-- the worker may carry it (supabase/tests/task_18_worker_row_access_test.sql).
-- Idempotent: REVOKE of an absent privilege is a no-op, and the policy is
-- dropped only if it exists.
begin;

revoke select on public.generic_media_upload_session_parts from motionly_worker;
revoke select on public.generic_media_upload_sessions from motionly_worker;
revoke select on public.upload_session_parts from motionly_worker;
revoke select on public.upload_sessions from motionly_worker;

drop policy if exists motionly_worker_jobs on public.generic_media_upload_session_parts;
drop policy if exists motionly_worker_jobs on public.generic_media_upload_sessions;
drop policy if exists motionly_worker_jobs on public.upload_session_parts;
drop policy if exists motionly_worker_jobs on public.upload_sessions;

commit;
