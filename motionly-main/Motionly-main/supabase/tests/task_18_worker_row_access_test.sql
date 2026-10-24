-- Workers query as motionly_worker (Task 18), a role row-level security still
-- applies to. Table privileges alone return no rows: the preprocessing worker
-- reported upload_not_found for every upload on the CPU stack. Workers process
-- jobs for every workspace, so each RLS-protected job table the worker is
-- granted (202610230101 narrows the grants to the tables workers use) needs a
-- policy for the role, and no other table keeps one. Credential and identity
-- tables no worker uses stay denied, and storage stays scoped to the four
-- worker buckets.
begin;
select plan(8);

select ok(
  not (select rolbypassrls from pg_roles where rolname = 'motionly_worker'),
  'motionly_worker does not bypass RLS (that would also lift its storage bucket scope)'
);

select is(
  (
    select coalesce(array_agg(c.relname::text order by c.relname), '{}')
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind in ('r', 'p')
      and c.relrowsecurity
      and (
        has_table_privilege('motionly_worker', c.oid, 'SELECT')
        or has_table_privilege('motionly_worker', c.oid, 'INSERT')
        or has_table_privilege('motionly_worker', c.oid, 'UPDATE')
        or has_table_privilege('motionly_worker', c.oid, 'DELETE')
      ) <> exists (
        select 1 from pg_policy p
        where p.polrelid = c.oid
          and p.polroles @> array['motionly_worker'::regrole::oid]
      )
  ),
  '{}'::text[],
  'exactly the RLS tables granted to motionly_worker have a worker policy'
);

select is(
  (
    select count(*)::integer
    from pg_policy p
    join pg_class c on c.oid = p.polrelid
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relname in (
        'admin_audit_events', 'password_reset_requests',
        'workspace_api_keys', 'workspace_invitations'
      )
      and (p.polroles @> array['motionly_worker'::regrole::oid] or p.polroles = '{0}')
  ),
  0,
  'credential and identity tables have no policy that lets the worker read them'
);

-- Behaviour: a row the API created is visible to, and updatable by, the worker.
-- pgTAP lives in a schema the worker cannot use, so the worker's observations
-- are recorded with set_config and asserted after the role is reset.
insert into auth.users (id, email, raw_user_meta_data) values
  ('2e000000-0000-4000-8000-0000000000aa', 'worker-rows@test.local', '{}');
insert into public.projects (id, workspace_id, created_by, title)
select '4e000000-0000-4000-8000-0000000000aa', w.id, w.owner_user_id, 'Worker rows'
from public.workspaces w
where w.owner_user_id = '2e000000-0000-4000-8000-0000000000aa';
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
  mime_type, byte_size, file_size_bytes, status, workflow_version
)
select '5e000000-0000-4000-8000-0000000000aa', p.workspace_id, p.id, 'raw-uploads',
  'worker/rows.mp4', 'rows.mp4', 'video/mp4', 2048, 2048, 'processing', 'first-draft-v2'
from public.projects p where p.id = '4e000000-0000-4000-8000-0000000000aa';

set local role motionly_worker;
select set_config('motionly_test.sees_upload', (
  select count(*)::text from public.uploaded_videos
  where id = '5e000000-0000-4000-8000-0000000000aa'
), true);
select set_config('motionly_test.sees_project', (
  select count(*)::text from public.projects
  where id = '4e000000-0000-4000-8000-0000000000aa'
), true);
with updated as (
  update public.uploaded_videos set original_filename = 'rows-renamed.mp4'
  where id = '5e000000-0000-4000-8000-0000000000aa' returning 1
)
select set_config('motionly_test.updated_upload', (select count(*)::text from updated), true);
reset role;

select is(current_setting('motionly_test.sees_upload')::integer, 1,
  'the worker sees an upload created by the API');
select is(current_setting('motionly_test.sees_project')::integer, 1,
  'the worker sees the project the upload belongs to');
select is(current_setting('motionly_test.updated_upload')::integer, 1,
  'the worker can update that upload');
select is(
  (select original_filename from public.uploaded_videos
   where id = '5e000000-0000-4000-8000-0000000000aa'),
  'rows-renamed.mp4',
  'the worker''s update is stored'
);

select ok(
  exists (
    select 1 from pg_policies
    where schemaname = 'storage' and tablename = 'objects'
      and policyname = 'motionly_worker_bucket_scope'
      and qual not like '%music%'
  ),
  'storage objects stay scoped to the worker buckets'
);

select * from finish();
rollback;
