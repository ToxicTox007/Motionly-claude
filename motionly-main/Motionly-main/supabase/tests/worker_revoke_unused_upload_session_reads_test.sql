-- 202610240301: motionly_worker no longer reads upload-session tables, which no
-- worker code uses, and keeps no row policy on them. The worker still reaches
-- uploaded_videos, which it updates.
begin;
select plan(3);

select is(
  (
    select coalesce(array_agg(t order by t), '{}')
    from unnest(array[
      'generic_media_upload_session_parts', 'generic_media_upload_sessions',
      'upload_session_parts', 'upload_sessions'
    ]) as t
    where has_table_privilege('motionly_worker', format('public.%I', t), 'SELECT')
       or has_table_privilege('motionly_worker', format('public.%I', t), 'INSERT')
       or has_table_privilege('motionly_worker', format('public.%I', t), 'UPDATE')
       or has_table_privilege('motionly_worker', format('public.%I', t), 'DELETE')
  ),
  '{}'::text[],
  'the worker has no privilege on the upload-session tables'
);

select is(
  (
    select count(*)::integer
    from pg_policy p
    join pg_class c on c.oid = p.polrelid
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and p.polname = 'motionly_worker_jobs'
      and c.relname in (
        'generic_media_upload_session_parts', 'generic_media_upload_sessions',
        'upload_session_parts', 'upload_sessions'
      )
  ),
  0,
  'no worker row policy remains on the upload-session tables'
);

select ok(
  has_table_privilege('motionly_worker', 'public.uploaded_videos', 'SELECT')
  and has_table_privilege('motionly_worker', 'public.uploaded_videos', 'UPDATE'),
  'the worker still reads and updates uploaded_videos'
);

select * from finish();
rollback;
