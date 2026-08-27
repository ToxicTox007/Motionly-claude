begin;
select plan(12);

select has_function(
  'public', 'motionly_trigger_media_replace_invalidation', array[]::text[],
  'source replacement trigger function exists'
);

insert into auth.users (id, email, raw_user_meta_data)
values ('18000000-0000-4000-8000-000000000001', 'task18-owner@test.local', '{}'::jsonb);
insert into public.workspaces (id, name, owner_user_id)
values ('38000000-0000-4000-8000-000000000001', 'Task 18', '18000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status)
values ('38000000-0000-4000-8000-000000000001', '18000000-0000-4000-8000-000000000001', 'owner', 'active');
set local role service_role;
insert into public.projects (id, workspace_id, created_by, title)
values ('48000000-0000-4000-8000-000000000001', '38000000-0000-4000-8000-000000000001', '18000000-0000-4000-8000-000000000001', 'Task 18 project');
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
  mime_type, byte_size, file_size_bytes, status, workflow_version
) values (
  '58000000-0000-4000-8000-000000000001',
  '38000000-0000-4000-8000-000000000001',
  '48000000-0000-4000-8000-000000000001',
  'raw-uploads', 'task18/source-v1.mp4', 'source-v1.mp4',
  'video/mp4', 2048, 2048, 'processing', 'first-draft-v2'
);
insert into public.object_references (
  id, workspace_id, project_id, storage_bucket, storage_path,
  owner_type, owner_id, object_type, mime_type, size_bytes, sha256
) values (
  '88000000-0000-4000-8000-000000000001',
  '38000000-0000-4000-8000-000000000001',
  '48000000-0000-4000-8000-000000000001',
  'raw-uploads', 'task18/source-v1.mp4',
  'uploaded_video', '58000000-0000-4000-8000-000000000001', 'source_video',
  'video/mp4', 2048, repeat('a', 64)
);
update public.uploaded_videos
set source_object_reference_id = '88000000-0000-4000-8000-000000000001',
    verified_size_bytes = 2048,
    verified_sha256 = repeat('a', 64)
where id = '58000000-0000-4000-8000-000000000001';

create temporary table task18_initial_run as
select result from public.motionly_ensure_first_draft_v2(
  '38000000-0000-4000-8000-000000000001',
  '48000000-0000-4000-8000-000000000001',
  '58000000-0000-4000-8000-000000000001',
  '18000000-0000-4000-8000-000000000001'
) as result;
select is((select result->>'state' from task18_initial_run), 'uploaded',
  'initial first-draft run starts on the current source revision');

create temporary table task18_active_attempt as
select result from public.motionly_begin_workflow_step(
  '38000000-0000-4000-8000-000000000001',
  '48000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task18_initial_run),
  'preprocess-source-v2', 'preprocess_source', 'task18-active-attempt',
  'task18-worker', 60, repeat('b', 64), 3
) as result;
select is((select result->'attempt'->>'state' from task18_active_attempt), 'active',
  'old workflow owns an active fenced worker attempt before replacement');

insert into public.object_references (
  id, workspace_id, project_id, storage_bucket, storage_path,
  owner_type, owner_id, object_type, mime_type, size_bytes, sha256
) values (
  '88000000-0000-4000-8000-000000000002',
  '38000000-0000-4000-8000-000000000001',
  '48000000-0000-4000-8000-000000000001',
  'raw-uploads', 'task18/source-v2.mp4',
  'uploaded_video', '58000000-0000-4000-8000-000000000001', 'source_video',
  'video/mp4', 4096, repeat('c', 64)
);
update public.uploaded_videos
set storage_path = 'task18/source-v2.mp4',
    byte_size = 4096,
    file_size_bytes = 4096,
    source_object_reference_id = '88000000-0000-4000-8000-000000000002',
    verified_size_bytes = 4096,
    verified_sha256 = repeat('c', 64)
where id = '58000000-0000-4000-8000-000000000001';

select is(
  (select media_revision from public.uploaded_videos where id = '58000000-0000-4000-8000-000000000001'),
  3,
  'source replacement advances the immutable media revision'
);
select is(
  (select state from public.workflow_runs where id = (select (result->>'id')::uuid from task18_initial_run)),
  'superseded',
  'source replacement supersedes the old canonical workflow'
);
select is(
  (select state from public.workflow_step_attempts
   where id = (select (result->'attempt'->>'id')::uuid from task18_active_attempt)),
  'cancelled',
  'source replacement fences the active worker attempt'
);
select is(
  (select state from public.transactional_outbox
   where workflow_run_id = (select (result->>'id')::uuid from task18_initial_run)
     and event_type = 'workflow.start'),
  'dead_letter',
  'replacement prevents the old workflow start from being dispatched later'
);
select ok(
  exists (
    select 1 from public.transactional_outbox
    where workflow_run_id = (select (result->>'id')::uuid from task18_initial_run)
      and event_type = 'workflow.cancelled'
      and payload = jsonb_build_object(
        'workflow_run_id', (select (result->>'id')::uuid from task18_initial_run),
        'state', 'superseded'
      )
  ),
  'replacement emits the canonical Temporal cancellation signal with supersession state'
);
select ok(
  not exists (
    select 1 from public.transactional_outbox
    where workflow_run_id = (select (result->>'id')::uuid from task18_initial_run)
      and event_type <> 'workflow.cancelled'
      and state in ('pending', 'failed', 'leased')
  ),
  'no old workflow message remains eligible for dispatch after replacement'
);

create temporary table task18_recovered as
select result from public.motionly_reconcile_first_draft_v2(10) as result;
select is((select count(*)::integer from task18_recovered), 1,
  'reconciler creates exactly one fresh workflow for the replacement source');
select ok(
  (select (result->>'source_media_revision')::integer = 3
          and result->>'state' = 'uploaded'
   from task18_recovered),
  'recovered workflow is pinned to the replacement media revision'
);
select is(
  (select count(*)::integer from public.workflow_runs
   where uploaded_video_id = '58000000-0000-4000-8000-000000000001'
     and state not in ('export_ready', 'failed', 'cancelled', 'superseded')),
  1,
  'exactly one current-source workflow remains nonterminal'
);

select * from finish();
rollback;
