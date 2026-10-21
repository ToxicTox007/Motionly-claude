begin;
select plan(62);

insert into auth.users (id, email, raw_user_meta_data)
values
  ('16000000-0000-4000-8000-000000000001', 'task6-owner-a@test.local', '{}'::jsonb),
  ('16000000-0000-4000-8000-000000000002', 'task6-owner-b@test.local', '{}'::jsonb);

insert into public.workspaces (id, name, owner_user_id)
values
  ('36000000-0000-4000-8000-000000000001', 'Task 6 A', '16000000-0000-4000-8000-000000000001'),
  ('36000000-0000-4000-8000-000000000002', 'Task 6 B', '16000000-0000-4000-8000-000000000002');

insert into public.workspace_members (workspace_id, user_id, role, status)
values
  ('36000000-0000-4000-8000-000000000001', '16000000-0000-4000-8000-000000000001', 'owner', 'active'),
  ('36000000-0000-4000-8000-000000000002', '16000000-0000-4000-8000-000000000002', 'owner', 'active');

select is(
  (select count(*)::integer from information_schema.tables
   where table_schema = 'public' and table_name = any(array[
     'workspace_storage_quotas', 'storage_quota_reservations', 'upload_sessions',
     'upload_session_parts', 'object_references', 'signed_url_grants'
   ])),
  6,
  'all Task 6 tables exist'
);

select is(
  (select count(*)::integer from information_schema.columns
   where table_schema = 'public' and table_name = 'workspace_storage_quotas'
     and column_name = any(array[
       'used_bytes', 'reserved_bytes', 'used_object_count', 'reserved_object_count',
       'max_bytes', 'max_objects'
     ])),
  6,
  'quota table accounts for used and reserved bytes and objects'
);

select is(
  (select count(*)::integer from information_schema.columns
   where table_schema = 'public' and table_name = 'upload_sessions'
     and column_name = any(array[
       'uploaded_video_id', 'final_storage_bucket', 'final_storage_path',
       'declared_size_bytes', 'expected_sha256', 'verified_size_bytes',
       'verified_sha256', 'part_size_bytes', 'part_count', 'quota_reservation_id',
       'quota_committed_at', 'expires_at'
     ])),
  12,
  'upload sessions carry final identity, verification, part plan, quota, and expiry'
);

select is(
  (select count(*)::integer from information_schema.columns
   where table_schema = 'public' and table_name = 'uploaded_videos'
     and column_name = any(array[
       'storage_quota_reservation_id', 'legacy_quota_reservation_id',
       'legacy_upload_expires_at'
     ])),
  3,
  'uploads carry committed quota identity plus legacy reservation and expiry linkage'
);

select is(
  (select count(*)::integer from information_schema.columns
   where table_schema = 'public' and table_name = 'storage_deletion_queue'
     and column_name = any(array[
       'quota_reservation_id', 'quota_released_at'
     ])),
  2,
  'durable deletion work carries idempotent quota reconciliation state'
);

select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.upload_session_parts'::regclass
      and contype = 'u'
      and pg_get_constraintdef(oid) = 'UNIQUE (session_id, part_number)'
  ),
  'parts are normalized with one row per session and part number'
);

select ok(
  not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'signed_url_grants'
      and column_name in ('url', 'signed_url', 'signed_url_value')
  ),
  'signed URL grants never store a URL'
);

select ok(
  not exists (
    select 1 from pg_indexes
    where schemaname = 'public'
      and lower(indexdef) like '%where%now(%'
  ),
  'no partial index contains a volatile now predicate'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.motionly_reserve_storage_quota(uuid,uuid,bigint,integer,timestamptz)',
    'EXECUTE'
  )
  and not has_function_privilege(
    'authenticated', 'public.motionly_commit_storage_quota(uuid)', 'EXECUTE'
  )
  and not has_function_privilege(
    'authenticated', 'public.motionly_release_storage_quota(uuid)', 'EXECUTE'
  )
  and not has_function_privilege(
    'authenticated',
    'public.motionly_delete_committed_storage_quota(uuid)',
    'EXECUTE'
  )
  and not has_function_privilege(
    'authenticated',
    'public.motionly_initialize_upload(uuid,uuid,uuid,text,text,text,text,bigint,integer)',
    'EXECUTE'
  )
  and not has_function_privilege(
    'authenticated',
    'public.motionly_cancel_legacy_upload(uuid,uuid,uuid,uuid)',
    'EXECUTE'
  )
  and not has_function_privilege(
    'authenticated',
    'public.motionly_finalize_upload_session(uuid,uuid,uuid,uuid,bigint,text)',
    'EXECUTE'
  )
  and not has_function_privilege(
    'authenticated',
    'public.motionly_commit_upload_session_quota(uuid,uuid,uuid)',
    'EXECUTE'
  )
  and not has_function_privilege(
    'authenticated',
    'public.motionly_terminate_upload_session(uuid,uuid,uuid,text,text,text,boolean)',
    'EXECUTE'
  )
  and not has_function_privilege(
    'authenticated', 'public.motionly_expire_upload_sessions()', 'EXECUTE'
  )
  and not has_function_privilege(
    'authenticated',
    'public.motionly_expire_storage_quota_reservations()',
    'EXECUTE'
  ),
  'authenticated cannot execute Task 6 service-role RPCs'
);

select ok(
  not exists (
    select 1
    from unnest(array[
      'workspace_storage_quotas', 'storage_quota_reservations', 'upload_sessions',
      'upload_session_parts', 'object_references', 'signed_url_grants'
    ]) as target(table_name)
    where has_table_privilege('authenticated', format('public.%I', table_name), 'INSERT')
       or has_table_privilege('authenticated', format('public.%I', table_name), 'UPDATE')
       or has_table_privilege('authenticated', format('public.%I', table_name), 'DELETE')
  ),
  'authenticated has no Task 6 table mutation privileges'
);

set local role service_role;

insert into public.projects (id, workspace_id, created_by, title)
values
  ('46000000-0000-4000-8000-000000000001', '36000000-0000-4000-8000-000000000001', '16000000-0000-4000-8000-000000000001', 'Task 6 A project'),
  ('46000000-0000-4000-8000-000000000002', '36000000-0000-4000-8000-000000000002', '16000000-0000-4000-8000-000000000002', 'Task 6 B project');

insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path,
  original_filename, mime_type, byte_size, file_size_bytes, status
) values
  (
    '56000000-0000-4000-8000-000000000001',
    '36000000-0000-4000-8000-000000000001',
    '46000000-0000-4000-8000-000000000001',
    'raw-uploads',
    'workspaces/36000000-0000-4000-8000-000000000001/projects/46000000-0000-4000-8000-000000000001/raw/a.mp4',
    'a.mp4', 'video/mp4', 10, 10, 'uploading'
  ),
  (
    '56000000-0000-4000-8000-000000000002',
    '36000000-0000-4000-8000-000000000002',
    '46000000-0000-4000-8000-000000000002',
    'raw-uploads',
    'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/b.mp4',
    'b.mp4', 'video/mp4', 10, 10, 'uploading'
  );

select is(
  (select count(*)::integer
   from public.motionly_initialize_upload(
     '56000000-0000-4000-8000-000000000099',
     '36000000-0000-4000-8000-000000000001',
     '46000000-0000-4000-8000-000000000001',
     'raw-uploads',
     'workspaces/36000000-0000-4000-8000-000000000001/projects/46000000-0000-4000-8000-000000000001/raw/admission.mp4',
     'admission.mp4', 'video/mp4', 10, 1
   )),
  0,
  'workspace-locked admission refuses a second active upload at the limit'
);

insert into public.workspace_storage_quotas (workspace_id, max_bytes, max_objects)
values ('36000000-0000-4000-8000-000000000001', 100, 2);

select is(
  public.motionly_reserve_storage_quota(
    '66000000-0000-4000-8000-000000000001',
    '36000000-0000-4000-8000-000000000001', 60, 1, '2099-01-01T00:00:00Z'
  ),
  true,
  'reserve succeeds when byte and object capacity are available'
);

select is(
  public.motionly_reserve_storage_quota(
    '66000000-0000-4000-8000-000000000001',
    '36000000-0000-4000-8000-000000000001', 60, 1, '2099-01-01T00:00:00Z'
  ),
  true,
  'reserve retry with the same reservation id is idempotent'
);

select is(
  (select reserved_bytes from public.workspace_storage_quotas
   where workspace_id = '36000000-0000-4000-8000-000000000001'),
  60::bigint,
  'duplicate reserve increments reserved bytes once'
);

select is(
  (select reserved_object_count from public.workspace_storage_quotas
   where workspace_id = '36000000-0000-4000-8000-000000000001'),
  1::bigint,
  'duplicate reserve increments reserved object count once'
);

select is(
  public.motionly_reserve_storage_quota(
    '66000000-0000-4000-8000-000000000002',
    '36000000-0000-4000-8000-000000000001', 41, 1, '2099-01-01T00:00:00Z'
  ),
  false,
  'byte quota denial is atomic'
);

select is(
  public.motionly_reserve_storage_quota(
    '66000000-0000-4000-8000-000000000003',
    '36000000-0000-4000-8000-000000000001', 1, 2, '2099-01-01T00:00:00Z'
  ),
  false,
  'object quota denial is atomic'
);

select is(
  public.motionly_commit_storage_quota('66000000-0000-4000-8000-000000000001'),
  true,
  'commit transitions a reservation'
);

select is(
  public.motionly_commit_storage_quota('66000000-0000-4000-8000-000000000001'),
  true,
  'commit retry is idempotent'
);

select ok(
  (select used_bytes = 60 and reserved_bytes = 0
       and used_object_count = 1 and reserved_object_count = 0
   from public.workspace_storage_quotas
   where workspace_id = '36000000-0000-4000-8000-000000000001'),
  'commit moves both bytes and objects from reserved to used exactly once'
);

select is(
  public.motionly_release_storage_quota('66000000-0000-4000-8000-000000000001'),
  false,
  'release cannot subtract committed usage'
);

select is(
  public.motionly_reserve_storage_quota(
    '66000000-0000-4000-8000-000000000004',
    '36000000-0000-4000-8000-000000000001', 20, 1, '2099-01-01T00:00:00Z'
  )
  and public.motionly_release_storage_quota('66000000-0000-4000-8000-000000000004'),
  true,
  'a held reservation can be released'
);

select is(
  public.motionly_release_storage_quota('66000000-0000-4000-8000-000000000004'),
  true,
  'release retry is idempotent'
);

select ok(
  (select reserved_bytes = 0 and reserved_object_count = 0
   from public.workspace_storage_quotas
   where workspace_id = '36000000-0000-4000-8000-000000000001'),
  'release decrements byte and object reservations once'
);

select is(
  public.motionly_reserve_storage_quota(
    '66000000-0000-4000-8000-000000000005',
    '36000000-0000-4000-8000-000000000001', 10, 1, '2099-01-01T00:00:00Z'
  ),
  true,
  'expiry fixture reservation is created'
);

update public.storage_quota_reservations
set expires_at = now() - interval '1 minute'
where id = '66000000-0000-4000-8000-000000000005';

insert into public.upload_sessions (
  id, workspace_id, project_id, uploaded_video_id,
  final_storage_bucket, final_storage_path, original_filename, mime_type,
  declared_size_bytes, part_size_bytes, part_count, status,
  quota_reservation_id, expires_at
) values (
  '76000000-0000-4000-8000-000000000001',
  '36000000-0000-4000-8000-000000000001',
  '46000000-0000-4000-8000-000000000001',
  '56000000-0000-4000-8000-000000000001',
  'raw-uploads',
  'workspaces/36000000-0000-4000-8000-000000000001/projects/46000000-0000-4000-8000-000000000001/raw/a.mp4',
  'a.mp4', 'video/mp4', 10, 10, 1, 'active',
  '66000000-0000-4000-8000-000000000005', now() - interval '1 minute'
);

select is(public.motionly_expire_upload_sessions(), 1, 'session expiry processes an active session');
select is(public.motionly_expire_upload_sessions(), 0, 'session expiry retry does no work');
select is(
  (select status from public.upload_sessions where id = '76000000-0000-4000-8000-000000000001'),
  'expired',
  'expired session has a terminal status'
);
select ok(
  (select state = 'released' from public.storage_quota_reservations
   where id = '66000000-0000-4000-8000-000000000005')
  and (select reserved_bytes = 0 and reserved_object_count = 0
       from public.workspace_storage_quotas
       where workspace_id = '36000000-0000-4000-8000-000000000001'),
  'expiry releases its quota reservation exactly once'
);

select is(
  public.motionly_reserve_storage_quota(
    '66000000-0000-4000-8000-000000000006',
    '36000000-0000-4000-8000-000000000001', 10, 1, '2099-01-01T00:00:00Z'
  ),
  true,
  'legacy completion reservation is held for retry'
);

insert into public.object_references (
  id, workspace_id, project_id, storage_bucket, storage_path,
  owner_type, owner_id, object_type, size_bytes
) values (
  '86000000-0000-4000-8000-000000000003',
  '36000000-0000-4000-8000-000000000001',
  '46000000-0000-4000-8000-000000000001',
  'raw-uploads',
  'workspaces/36000000-0000-4000-8000-000000000001/projects/46000000-0000-4000-8000-000000000001/raw/a.mp4',
  'uploaded_video', '56000000-0000-4000-8000-000000000001', 'source_video', 10
);

update public.uploaded_videos
set status = 'uploading',
    error_code = null,
    error_message = null,
    legacy_quota_reservation_id = '66000000-0000-4000-8000-000000000006',
    legacy_upload_expires_at = now() - interval '1 minute'
where id = '56000000-0000-4000-8000-000000000001';

update public.storage_quota_reservations
set expires_at = now() - interval '1 minute'
where id = '66000000-0000-4000-8000-000000000006';

select is(
  public.motionly_expire_storage_quota_reservations(),
  1,
  'scheduled expiry releases and closes an expired legacy upload'
);
select is(
  public.motionly_expire_storage_quota_reservations(),
  0,
  'legacy reservation expiry retry does no work'
);
select ok(
  (select state = 'released' from public.storage_quota_reservations
   where id = '66000000-0000-4000-8000-000000000006')
  and (select reserved_bytes = 0 and reserved_object_count = 0
       from public.workspace_storage_quotas
       where workspace_id = '36000000-0000-4000-8000-000000000001'),
  'legacy reservation expiry decrements byte and object reservations exactly once'
);
select ok(
  (select status = 'failed'
          and error_code = 'legacy_upload_expired'
          and source_object_reference_id is null
          and legacy_quota_reservation_id is null
          and legacy_upload_expires_at is null
   from public.uploaded_videos
   where id = '56000000-0000-4000-8000-000000000001')
  and (select status = 'deleted'
       from public.object_references
       where id = '86000000-0000-4000-8000-000000000003'),
  'legacy expiry fails the upload and deletes its durable reference'
);
select is(
  (select count(*)::integer
   from public.storage_deletion_queue
   where storage_bucket = 'raw-uploads'
     and storage_path = 'workspaces/36000000-0000-4000-8000-000000000001/projects/46000000-0000-4000-8000-000000000001/raw/a.mp4'),
  1,
  'legacy expiry leaves the uploaded object durably queued exactly once'
);

select is(
  public.motionly_reserve_storage_quota(
    '66000000-0000-4000-8000-000000000007',
    '36000000-0000-4000-8000-000000000002', 10, 1, '2099-01-01T00:00:00Z'
  ),
  true,
  'multipart commit fixture reserves quota'
);

insert into public.upload_sessions (
  id, workspace_id, project_id, uploaded_video_id,
  final_storage_bucket, final_storage_path, original_filename, mime_type,
  declared_size_bytes, part_size_bytes, part_count, status,
  quota_reservation_id, expires_at
) values (
  '76000000-0000-4000-8000-000000000002',
  '36000000-0000-4000-8000-000000000002',
  '46000000-0000-4000-8000-000000000002',
  '56000000-0000-4000-8000-000000000002',
  'raw-uploads',
  'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/b.mp4',
  'b.mp4', 'video/mp4', 10, 10, 1, 'completing',
  '66000000-0000-4000-8000-000000000007', '2099-01-01T00:00:00Z'
);

select is(
  public.motionly_commit_upload_session_quota(
    '76000000-0000-4000-8000-000000000002',
    '36000000-0000-4000-8000-000000000002',
    '46000000-0000-4000-8000-000000000002'
  ),
  true,
  'multipart quota commit records its durable milestone atomically'
);
select is(
  public.motionly_commit_upload_session_quota(
    '76000000-0000-4000-8000-000000000002',
    '36000000-0000-4000-8000-000000000002',
    '46000000-0000-4000-8000-000000000002'
  ),
  true,
  'multipart quota commit retry is idempotent'
);
select ok(
  (select quota_committed_at is not null
   from public.upload_sessions
   where id = '76000000-0000-4000-8000-000000000002')
  and (select state = 'committed'
       from public.storage_quota_reservations
       where id = '66000000-0000-4000-8000-000000000007')
  and (select used_bytes = 10 and reserved_bytes = 0
       from public.workspace_storage_quotas
       where workspace_id = '36000000-0000-4000-8000-000000000002'),
  'multipart commit moves accounting exactly once and persists recovery state'
);
select throws_ok(
  $$select public.motionly_terminate_upload_session(
      '76000000-0000-4000-8000-000000000002',
      '36000000-0000-4000-8000-000000000002',
      '46000000-0000-4000-8000-000000000002',
      'failed', 'should_not_fail', 'should not fail', true
    )$$,
  '55000', 'committed upload must resume completion',
  'terminal cleanup refuses committed multipart finalization'
);
select ok(
  (select status = 'completing' and quota_committed_at is not null
   from public.upload_sessions
   where id = '76000000-0000-4000-8000-000000000002')
  and not exists (
    select 1 from public.storage_deletion_queue
    where storage_path = 'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/b.mp4'
  ),
  'refused committed cleanup preserves finalization state and storage'
);

insert into public.object_references (
  id, workspace_id, project_id, storage_bucket, storage_path,
  owner_type, owner_id, object_type, size_bytes
) values (
  '86000000-0000-4000-8000-000000000004',
  '36000000-0000-4000-8000-000000000002',
  '46000000-0000-4000-8000-000000000002',
  'raw-uploads',
  'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/b.mp4',
  'uploaded_video', '56000000-0000-4000-8000-000000000002', 'source_video', 10
);
insert into public.upload_session_parts (
  workspace_id, project_id, session_id, part_number,
  expected_size_bytes, object_path, status, verified_size_bytes, uploaded_at
) values (
  '36000000-0000-4000-8000-000000000002',
  '46000000-0000-4000-8000-000000000002',
  '76000000-0000-4000-8000-000000000002', 1, 10,
  'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/b.mp4.parts/76000000-0000-4000-8000-000000000002/1',
  'uploaded', 10, now()
);
select is(
  public.motionly_finalize_upload_session(
    '76000000-0000-4000-8000-000000000002',
    '36000000-0000-4000-8000-000000000002',
    '46000000-0000-4000-8000-000000000002',
    '86000000-0000-4000-8000-000000000004',
    10,
    repeat('a', 64)
  ),
  true,
  'verified multipart finalization commits all durable state atomically'
);
select is(
  public.motionly_finalize_upload_session(
    '76000000-0000-4000-8000-000000000002',
    '36000000-0000-4000-8000-000000000002',
    '46000000-0000-4000-8000-000000000002',
    '86000000-0000-4000-8000-000000000004',
    10,
    repeat('a', 64)
  ),
  true,
  'multipart atomic finalization retry is idempotent'
);
select ok(
  (select status = 'completed' and verified_sha256 = repeat('a', 64)
   from public.upload_sessions
   where id = '76000000-0000-4000-8000-000000000002')
  and (select status = 'processing'
              and storage_quota_reservation_id = '66000000-0000-4000-8000-000000000007'
       from public.uploaded_videos
       where id = '56000000-0000-4000-8000-000000000002')
  and (select count(*) = 1
       from public.storage_deletion_queue
       where storage_path = 'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/b.mp4.parts/76000000-0000-4000-8000-000000000002/1'),
  'multipart finalization persists processing state and queues every temporary part'
);

delete from public.uploaded_videos
where id = '56000000-0000-4000-8000-000000000002';
select is(
  (select quota_reservation_id
   from public.storage_deletion_queue
   where storage_path = 'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/b.mp4'),
  '66000000-0000-4000-8000-000000000007'::uuid,
  'uploaded-video deletion preserves committed quota identity in durable work'
);
select is(
  public.motionly_delete_committed_storage_quota(
    '66000000-0000-4000-8000-000000000007'
  ),
  true,
  'physical object deletion decrements committed quota'
);
select is(
  public.motionly_delete_committed_storage_quota(
    '66000000-0000-4000-8000-000000000007'
  ),
  true,
  'committed quota deletion retry is idempotent'
);
select ok(
  (select state = 'deleted' and deleted_at is not null
   from public.storage_quota_reservations
   where id = '66000000-0000-4000-8000-000000000007')
  and (select used_bytes = 0 and used_object_count = 0
       from public.workspace_storage_quotas
       where workspace_id = '36000000-0000-4000-8000-000000000002'),
  'physical deletion decrements bytes and objects exactly once without underflow'
);

insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path,
  original_filename, mime_type, byte_size, file_size_bytes, status
) values (
  '56000000-0000-4000-8000-000000000003',
  '36000000-0000-4000-8000-000000000002',
  '46000000-0000-4000-8000-000000000002',
  'raw-uploads',
  'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/abort.mp4',
  'abort.mp4', 'video/mp4', 5, 5, 'uploading'
);
select is(
  public.motionly_reserve_storage_quota(
    '66000000-0000-4000-8000-000000000008',
    '36000000-0000-4000-8000-000000000002', 5, 1, '2099-01-01T00:00:00Z'
  ),
  true,
  'multipart abort fixture reserves quota'
);
insert into public.upload_sessions (
  id, workspace_id, project_id, uploaded_video_id,
  final_storage_bucket, final_storage_path, original_filename, mime_type,
  declared_size_bytes, part_size_bytes, part_count, status,
  quota_reservation_id, expires_at
) values (
  '76000000-0000-4000-8000-000000000003',
  '36000000-0000-4000-8000-000000000002',
  '46000000-0000-4000-8000-000000000002',
  '56000000-0000-4000-8000-000000000003',
  'raw-uploads',
  'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/abort.mp4',
  'abort.mp4', 'video/mp4', 5, 5, 1, 'completing',
  '66000000-0000-4000-8000-000000000008', '2099-01-01T00:00:00Z'
);
insert into public.upload_session_parts (
  workspace_id, project_id, session_id, part_number,
  expected_size_bytes, object_path
) values (
  '36000000-0000-4000-8000-000000000002',
  '46000000-0000-4000-8000-000000000002',
  '76000000-0000-4000-8000-000000000003', 1, 5,
  'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/abort.mp4.parts/76000000-0000-4000-8000-000000000003/1'
);
select is(
  public.motionly_terminate_upload_session(
    '76000000-0000-4000-8000-000000000003',
    '36000000-0000-4000-8000-000000000002',
    '46000000-0000-4000-8000-000000000002',
    'aborted', 'upload_aborted', 'Upload aborted.', false
  ),
  'aborted',
  'multipart abort atomically transitions and queues cleanup'
);
select is(
  public.motionly_terminate_upload_session(
    '76000000-0000-4000-8000-000000000003',
    '36000000-0000-4000-8000-000000000002',
    '46000000-0000-4000-8000-000000000002',
    'aborted', 'upload_aborted', 'Upload aborted.', false
  ),
  'aborted',
  'multipart abort cleanup retry is idempotent'
);
select ok(
  (select status = 'aborted'
   from public.upload_sessions
   where id = '76000000-0000-4000-8000-000000000003')
  and (select state = 'released'
       from public.storage_quota_reservations
       where id = '66000000-0000-4000-8000-000000000008')
  and (select status = 'failed' and error_code = 'upload_aborted'
       from public.uploaded_videos
       where id = '56000000-0000-4000-8000-000000000003'),
  'multipart abort releases quota and fails its upload exactly once'
);
select is(
  (select count(*)::integer
   from public.storage_deletion_queue
   where storage_path = 'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/abort.mp4.parts/76000000-0000-4000-8000-000000000003/1'
     and source_table = 'upload_session_parts'),
  1,
  'multipart abort durably queues each part exactly once'
);
select is(
  (select count(*)::integer
   from public.storage_deletion_queue
   where storage_path = 'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/abort.mp4'
     and source_table = 'upload_sessions'),
  1,
  'terminal cleanup preserves pre-transition completing state and queues the final object'
);

-- NOTE (integration, Task 17): motionly_finalize_legacy_upload was dropped by
-- migration 202610190401 (the legacy one-shot upload completion path was
-- removed). The former finalize-vs-cancel race scenarios are therefore no
-- longer reachable. motionly_cancel_legacy_upload is retained (the /cancel
-- endpoint still uses it), so its positive cancellation path is kept below.
select is(
  public.motionly_reserve_storage_quota(
    '66000000-0000-4000-8000-000000000010',
    '36000000-0000-4000-8000-000000000002', 4, 1, '2099-01-01T00:00:00Z'
  ),
  true,
  'legacy cancel fixture reserves quota'
);
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path,
  original_filename, mime_type, byte_size, file_size_bytes, status,
  source_object_reference_id, legacy_quota_reservation_id, legacy_upload_expires_at
) values (
  '56000000-0000-4000-8000-000000000005',
  '36000000-0000-4000-8000-000000000002',
  '46000000-0000-4000-8000-000000000002',
  'raw-uploads',
  'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/cancel-wins.mp4',
  'cancel-wins.mp4', 'video/mp4', 4, 4, 'uploading',
  null, '66000000-0000-4000-8000-000000000010', '2099-01-01T00:00:00Z'
);
insert into public.object_references (
  id, workspace_id, project_id, storage_bucket, storage_path,
  owner_type, owner_id, object_type, size_bytes
) values (
  '86000000-0000-4000-8000-000000000006',
  '36000000-0000-4000-8000-000000000002',
  '46000000-0000-4000-8000-000000000002',
  'raw-uploads',
  'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/cancel-wins.mp4',
  'uploaded_video', '56000000-0000-4000-8000-000000000005', 'source_video', 4
);
update public.uploaded_videos
set source_object_reference_id = '86000000-0000-4000-8000-000000000006'
where id = '56000000-0000-4000-8000-000000000005';
select is(
  public.motionly_cancel_legacy_upload(
    '56000000-0000-4000-8000-000000000005',
    '36000000-0000-4000-8000-000000000002',
    '46000000-0000-4000-8000-000000000002',
    '66000000-0000-4000-8000-000000000010'
  ),
  true,
  'legacy cancellation releases the reservation and dereferences the object'
);
select ok(
  (select status = 'failed'
          and error_code = 'upload_canceled'
          and source_object_reference_id is null
   from public.uploaded_videos
   where id = '56000000-0000-4000-8000-000000000005')
  and (select state = 'released'
       from public.storage_quota_reservations
       where id = '66000000-0000-4000-8000-000000000010')
  and (select status = 'deleted'
       from public.object_references
       where id = '86000000-0000-4000-8000-000000000006')
  and (select count(*) = 1
       from public.storage_deletion_queue
       where storage_path = 'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/cancel-wins.mp4'),
  'cancel outcome is fully failed, released, dereferenced, and queued'
);

select throws_ok(
  $$insert into public.upload_sessions (
      id, workspace_id, project_id, uploaded_video_id,
      final_storage_bucket, final_storage_path, original_filename, mime_type,
      declared_size_bytes, part_size_bytes, part_count, quota_reservation_id, expires_at
    ) values (
      '76000000-0000-4000-8000-000000000099',
      '36000000-0000-4000-8000-000000000001',
      '46000000-0000-4000-8000-000000000002',
      '56000000-0000-4000-8000-000000000002',
      'raw-uploads',
      'workspaces/36000000-0000-4000-8000-000000000001/projects/46000000-0000-4000-8000-000000000002/raw/forged.mp4',
      'forged.mp4', 'video/mp4', 10, 10, 1,
      '66000000-0000-4000-8000-000000000005', '2099-01-01T00:00:00Z'
    )$$,
  '23503', null,
  'session composite foreign keys reject cross-tenant identity'
);

insert into public.object_references (
  id, workspace_id, project_id, storage_bucket, storage_path,
  owner_type, owner_id, object_type, size_bytes
) values
  (
    '86000000-0000-4000-8000-000000000001',
    '36000000-0000-4000-8000-000000000001',
    '46000000-0000-4000-8000-000000000001',
    'raw-uploads',
    'workspaces/36000000-0000-4000-8000-000000000001/projects/46000000-0000-4000-8000-000000000001/raw/ref.mp4',
    'uploaded_video', '56000000-0000-4000-8000-000000000001', 'source_video', 10
  ),
  (
    '86000000-0000-4000-8000-000000000002',
    '36000000-0000-4000-8000-000000000002',
    '46000000-0000-4000-8000-000000000002',
    'raw-uploads',
    'workspaces/36000000-0000-4000-8000-000000000002/projects/46000000-0000-4000-8000-000000000002/raw/ref.mp4',
    'uploaded_video', '56000000-0000-4000-8000-000000000002', 'source_video', 10
  );

select ok(
  not exists (
    select 1
    from unnest(array[
      'workspace_storage_quotas', 'storage_quota_reservations', 'upload_sessions',
      'upload_session_parts', 'object_references', 'signed_url_grants'
    ]) as target(table_name)
    where not has_table_privilege('service_role', format('public.%I', table_name), 'INSERT')
       or not has_table_privilege('service_role', format('public.%I', table_name), 'UPDATE')
       or not has_table_privilege('service_role', format('public.%I', table_name), 'DELETE')
  ),
  'service role retains Task 6 table mutation privileges'
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '16000000-0000-4000-8000-000000000001', true);

select is(
  (select count(*)::integer from public.object_references
   where id = '86000000-0000-4000-8000-000000000001'),
  1,
  'authenticated member can select an own-tenant active reference'
);
select is(
  (select count(*)::integer from public.object_references
   where id = '86000000-0000-4000-8000-000000000002'),
  0,
  'authenticated member cannot select another tenant reference'
);
select throws_ok(
  $$insert into public.workspace_storage_quotas (workspace_id)
    values ('36000000-0000-4000-8000-000000000002')$$,
  '42501', null,
  'authenticated cannot mutate Task 6 accounting directly'
);

select * from finish();
rollback;
