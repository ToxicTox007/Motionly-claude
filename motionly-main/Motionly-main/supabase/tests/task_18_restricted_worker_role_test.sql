-- Task 18 (S3): the restricted motionly_worker database account.
--
-- Proves the worker role can do what workers need (read/write job tables, call
-- the workflow/render RPCs, touch its four storage buckets) but is denied the
-- auth schema, is not a member of service_role, and cannot reach storage
-- buckets outside its allow-list.

begin;
select plan(12);

-- The role exists, is NOLOGIN, and is granted to the PostgREST authenticator.
select ok(
  exists (select 1 from pg_roles where rolname = 'motionly_worker'),
  'the restricted motionly_worker role exists'
);
select ok(
  exists (select 1 from pg_roles where rolname = 'motionly_worker' and rolcanlogin = false),
  'motionly_worker is NOLOGIN (a JWT role PostgREST switches into)'
);
select ok(
  pg_has_role('authenticator', 'motionly_worker', 'MEMBER'),
  'authenticator may switch into motionly_worker'
);

-- The worker is NOT the service role (strictly less privileged).
select ok(
  not pg_has_role('motionly_worker', 'service_role', 'MEMBER'),
  'motionly_worker does not inherit service_role'
);

-- Allowed: read/write the job tables it uses.
select ok(
  has_table_privilege('motionly_worker', 'public.render_jobs', 'SELECT')
  and has_table_privilege('motionly_worker', 'public.render_jobs', 'UPDATE'),
  'worker may read and update render_jobs'
);
select ok(
  has_table_privilege('motionly_worker', 'public.uploaded_videos', 'SELECT'),
  'worker may read uploaded_videos'
);
select ok(
  has_table_privilege('motionly_worker', 'public.video_manifests', 'INSERT'),
  'worker may write video_manifests'
);

-- Allowed: call the workflow/render RPCs workers invoke. Checked by name:
-- has_function_privilege raises (instead of returning false) for a signature
-- that does not exist.
select ok(
  exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = 'motionly_complete_render'
      and has_function_privilege('motionly_worker', p.oid, 'EXECUTE')
  ),
  'worker may execute motionly_complete_render'
);
select ok(
  exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = 'motionly_begin_workflow_step'
      and has_function_privilege('motionly_worker', p.oid, 'EXECUTE')
  ),
  'worker may execute motionly_begin_workflow_step'
);

-- Denied: the auth schema (GoTrue user/credential data).
select ok(
  not has_schema_privilege('motionly_worker', 'auth', 'USAGE'),
  'worker cannot use the auth schema'
);

-- Storage: the worker is scoped to its four buckets by RLS; buckets outside the
-- allow-list are denied by the policy predicate.
select ok(
  exists (
    select 1 from pg_policies
    where schemaname = 'storage' and tablename = 'objects'
      and policyname = 'motionly_worker_bucket_scope'
      and qual like '%raw-uploads%'
      and qual like '%exports%'
      and qual not like '%music%'
  ),
  'storage policy scopes the worker to its four buckets'
);
select ok(
  has_table_privilege('motionly_worker', 'storage.objects', 'SELECT')
  and has_table_privilege('motionly_worker', 'storage.objects', 'INSERT'),
  'worker may read and write storage objects (bounded by the bucket-scope policy)'
);

select * from finish();
rollback;
