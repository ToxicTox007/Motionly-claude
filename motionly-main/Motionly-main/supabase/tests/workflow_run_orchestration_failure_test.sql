-- Worker audit WJ-06: the orchestration can fail a canonical run whose
-- Temporal execution failed, so it no longer stays non-terminal forever.
begin;
select plan(8);

select has_function('public', 'motionly_fail_workflow_run',
  array['uuid', 'uuid', 'uuid', 'text', 'text'],
  'orchestration run-failure RPC exists');
select ok(
  not has_function_privilege('authenticated',
    'public.motionly_fail_workflow_run(uuid,uuid,uuid,text,text)', 'EXECUTE'),
  'clients cannot fail workflow runs');

set local role postgres;
insert into auth.users (id, email, raw_user_meta_data) values
 ('1f000000-0000-4000-8000-000000000001', 'wj06@test.local', '{}');
insert into public.workspaces (id, name, owner_user_id) values
 ('3f000000-0000-4000-8000-000000000001', 'WJ06', '1f000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status) values
 ('3f000000-0000-4000-8000-000000000001', '1f000000-0000-4000-8000-000000000001', 'owner', 'active');

set local role service_role;
insert into public.projects (id, workspace_id, created_by, title) values
 ('4f000000-0000-4000-8000-000000000001', '3f000000-0000-4000-8000-000000000001',
  '1f000000-0000-4000-8000-000000000001', 'WJ06');
insert into public.uploaded_videos (
 id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
 mime_type, byte_size, file_size_bytes, status, workflow_version
) values (
 '5f000000-0000-4000-8000-000000000001', '3f000000-0000-4000-8000-000000000001',
 '4f000000-0000-4000-8000-000000000001', 'raw-uploads', 'wj06/source.mp4',
 'source.mp4', 'video/mp4', 2048, 2048, 'processing', 'first-draft-v2'
);
insert into public.object_references (
 id, workspace_id, project_id, storage_bucket, storage_path, owner_type, owner_id,
 object_type, mime_type, size_bytes, sha256
) values (
 '8f000000-0000-4000-8000-000000000001', '3f000000-0000-4000-8000-000000000001',
 '4f000000-0000-4000-8000-000000000001', 'raw-uploads', 'wj06/source.mp4',
 'uploaded_video', '5f000000-0000-4000-8000-000000000001', 'source_video',
 'video/mp4', 2048, repeat('b', 64)
);
update public.uploaded_videos set
 source_object_reference_id = '8f000000-0000-4000-8000-000000000001',
 verified_size_bytes = 2048, verified_sha256 = repeat('b', 64)
where id = '5f000000-0000-4000-8000-000000000001';

select public.motionly_ensure_first_draft_v2(
 '3f000000-0000-4000-8000-000000000001', '4f000000-0000-4000-8000-000000000001',
 '5f000000-0000-4000-8000-000000000001', '1f000000-0000-4000-8000-000000000001'
);
create temporary table wj06_run as
select id, revision from public.workflow_runs
where project_id = '4f000000-0000-4000-8000-000000000001';

select throws_ok(
  $$select public.motionly_fail_workflow_run(
      gen_random_uuid(), '4f000000-0000-4000-8000-000000000001',
      (select id from wj06_run), 'orchestration_failed', null)$$,
  'P0002', 'WORKFLOW_NOT_FOUND',
  'a run is only failed within its own tenant');

create temporary table wj06_first as
select public.motionly_fail_workflow_run(
  '3f000000-0000-4000-8000-000000000001', '4f000000-0000-4000-8000-000000000001',
  (select id from wj06_run), 'Bullmq Enqueue Unavailable', null) as result;
select is((select (result->>'changed')::boolean from wj06_first), true,
  'a non-terminal run is failed');
select ok(
  (select state = 'failed'
          and failure_code = 'bullmq_enqueue_unavailable'
          and failure_message = 'The workflow stopped before it finished.'
          and revision = (select revision from wj06_run) + 1
   from public.workflow_runs where id = (select id from wj06_run)),
  'the failure is recorded with a normalized code and a bumped revision');
select is(
  (select count(*)::integer from public.workflow_events
   where workflow_run_id = (select id from wj06_run) and event_type = 'workflow.run.failed'),
  1,
  'the failure is recorded in the run event log');

create temporary table wj06_second as
select public.motionly_fail_workflow_run(
  '3f000000-0000-4000-8000-000000000001', '4f000000-0000-4000-8000-000000000001',
  (select id from wj06_run), 'another_code', null) as result;
select is((select (result->>'changed')::boolean from wj06_second), false,
  'failing a terminal run again is an idempotent no-op');
select is(
  (select failure_code from public.workflow_runs where id = (select id from wj06_run)),
  'bullmq_enqueue_unavailable',
  'a repeated call does not overwrite the recorded failure');

select * from finish();
rollback;
