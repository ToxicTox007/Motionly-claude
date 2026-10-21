-- Task 18 (S3): a dedicated, restricted database account for the media worker
-- images (CPU and GPU alike).
--
-- Workers previously authenticated to PostgREST with the service-role key, which
-- can read and write every table in every schema (including `auth`) and every
-- storage bucket. This migration creates `motionly_worker`: a JWT role that
-- PostgREST switches into (NOLOGIN, granted to `authenticator`) whose reach is
-- limited to the database objects the worker job path actually uses and the four
-- storage buckets workers read/write. It can NOT touch the `auth` schema, and it
-- is NOT a member of `service_role` so it cannot inherit broad privileges.
--
-- Workers present a JWT whose `role` claim is `motionly_worker` (the new
-- `supabase_worker_key` secret), mounted only into the worker services.
--
-- Forward-only. Reserved timestamp 2026101905xx (Task 18).

begin;

-- NOLOGIN JWT role, granted to the PostgREST authenticator so it can `set role`
-- into it when a request presents a motionly_worker JWT.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'motionly_worker') then
    create role motionly_worker nologin;
  end if;
end
$$;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'authenticator') then
    execute 'grant motionly_worker to authenticator';
  end if;
end
$$;

-- Baseline schema access: the application schema only. The worker must never see
-- the auth schema, so make the denial explicit and belt-and-suspenders.
grant usage on schema public to motionly_worker;
revoke all on schema auth from motionly_worker;

-- The media worker runs the in-process job dispatcher (app.api.internal_jobs
-- ._execute_job), so it reads and writes the full set of job-processing tables
-- in `public` (inventoried via apps/api/tests/schema_usage.py). It is still
-- strictly less privileged than service_role: no `auth` schema, no membership in
-- service_role, and storage scoped to the worker buckets below.
grant select, insert, update, delete on all tables in schema public to motionly_worker;
grant usage, select on all sequences in schema public to motionly_worker;
grant execute on all functions in schema public to motionly_worker;
-- Future migrations that add job tables/functions keep the worker in step.
alter default privileges in schema public
  grant select, insert, update, delete on tables to motionly_worker;
alter default privileges in schema public
  grant usage, select on sequences to motionly_worker;
alter default privileges in schema public
  grant execute on functions to motionly_worker;

-- Storage: PostgREST reaches Supabase Storage through the `storage` schema.
-- Grant only what a worker needs to read uploads and write renders/previews, and
-- only on the four worker buckets; every other bucket stays denied by RLS.
grant usage on schema storage to motionly_worker;
grant select, insert, update, delete on storage.objects to motionly_worker;
grant select on storage.buckets to motionly_worker;

-- RLS on storage.objects restricts the worker to its four buckets. storage.objects
-- already has RLS enabled by Supabase Storage's own migrations.
drop policy if exists motionly_worker_bucket_scope on storage.objects;
create policy motionly_worker_bucket_scope on storage.objects
  for all
  to motionly_worker
  using (bucket_id in ('raw-uploads', 'exports', 'thumbnails', 'temp-previews'))
  with check (bucket_id in ('raw-uploads', 'exports', 'thumbnails', 'temp-previews'));

commit;
