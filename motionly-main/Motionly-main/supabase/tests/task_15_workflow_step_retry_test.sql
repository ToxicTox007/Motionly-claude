-- Task 15 pgTAP: a failed workflow step can be re-enqueued under a new BullMQ
-- job id (motionly_prepare_workflow_step retry path), while a queued/running
-- step with a different job id still fails closed.
begin;
select plan(6);

set local role postgres;

insert into auth.users (id, email, raw_user_meta_data) values
 ('1d000000-0000-4000-8000-000000000001', 'task15-retry@test.local', '{}');
insert into public.workspaces (id, name, owner_user_id) values
 ('3d000000-0000-4000-8000-000000000001', 'Task 15 Retry', '1d000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status) values
 ('3d000000-0000-4000-8000-000000000001', '1d000000-0000-4000-8000-000000000001', 'owner', 'active');

set local role service_role;
insert into public.projects (id, workspace_id, created_by, title) values
 ('4d000000-0000-4000-8000-000000000001', '3d000000-0000-4000-8000-000000000001',
  '1d000000-0000-4000-8000-000000000001', 'Task 15 Retry');

insert into public.uploaded_videos (
 id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
 mime_type, byte_size, file_size_bytes, status, workflow_version
) values (
 '5d000000-0000-4000-8000-000000000001', '3d000000-0000-4000-8000-000000000001',
 '4d000000-0000-4000-8000-000000000001', 'raw-uploads', 'task15/source.mp4',
 'source.mp4', 'video/mp4', 2048, 2048, 'processing', 'first-draft-v2'
);
insert into public.object_references (
 id, workspace_id, project_id, storage_bucket, storage_path, owner_type, owner_id,
 object_type, mime_type, size_bytes, sha256
) values (
 '8d000000-0000-4000-8000-000000000001', '3d000000-0000-4000-8000-000000000001',
 '4d000000-0000-4000-8000-000000000001', 'raw-uploads', 'task15/source.mp4',
 'uploaded_video', '5d000000-0000-4000-8000-000000000001', 'source_video',
 'video/mp4', 2048, repeat('a', 64)
);
update public.uploaded_videos set
 source_object_reference_id = '8d000000-0000-4000-8000-000000000001',
 verified_size_bytes = 2048, verified_sha256 = repeat('a', 64)
where id = '5d000000-0000-4000-8000-000000000001';

create temporary table t15_run as
select result from public.motionly_ensure_first_draft_v2(
 '3d000000-0000-4000-8000-000000000001', '4d000000-0000-4000-8000-000000000001',
 '5d000000-0000-4000-8000-000000000001', '1d000000-0000-4000-8000-000000000001'
) as result;

-- Prepare the step under its first transport.
create temporary table t15_prepare as
select * from public.motionly_prepare_workflow_step(
 '3d000000-0000-4000-8000-000000000001', '4d000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from t15_run),
 'preprocess-source-v2', 'preprocess_source', repeat('b', 64), 3,
 'workflow--task15--preprocess--0001', '5d000000-0000-4000-8000-000000000001'
);
select ok((select bullmq_job_id = 'workflow--task15--preprocess--0001' from t15_prepare),
  'step prepared under its first transport');

-- A queued step receiving a DIFFERENT job id is still a conflict.
select throws_ok(
 $$select * from public.motionly_prepare_workflow_step(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from t15_run),
 'preprocess-source-v2','preprocess_source',repeat('b',64),3,
 'workflow--task15--queued-conflict--0002','5d000000-0000-4000-8000-000000000001')$$,
 'P0001', 'WORKFLOW_STEP_TRANSPORT_CONFLICT',
 'a live queued step still rejects a different transport');

-- Simulate a failed first attempt with attempts remaining.
update public.workflow_steps
set state = 'failed', attempt_count = 1, completed_at = now(),
    last_error_code = 'bullmq_enqueue_unavailable'
where workflow_run_id = (select (result->>'id')::uuid from t15_run)
  and step_key = 'preprocess-source-v2';

-- The retry's NEW job id is accepted and re-arms the step.
create temporary table t15_retry as
select * from public.motionly_prepare_workflow_step(
 '3d000000-0000-4000-8000-000000000001', '4d000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from t15_run),
 'preprocess-source-v2', 'preprocess_source', repeat('c', 64), 3,
 'workflow--task15--retry--0003', '5d000000-0000-4000-8000-000000000001'
);
select ok((select bullmq_job_id = 'workflow--task15--retry--0003' from t15_retry),
  'failed step accepts the retry transport');
select is((select state from t15_retry), 'queued',
  'retried step is re-armed to queued');
select is((select input_hash from t15_retry), repeat('c', 64),
  'retried step adopts the new input hash');
select is((select revision from t15_retry), 2,
  'retried step advances its revision');

select * from finish();
rollback;
