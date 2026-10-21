-- Task 15 pgTAP: deleting a project records a durable Temporal cancellation that
-- survives the ON DELETE CASCADE, so the dispatcher can stop the orphaned
-- first-draft workflow instead of letting it keep calling the database.
begin;
select plan(6);

set local role postgres;

insert into auth.users (id, email, raw_user_meta_data) values
 ('1e000000-0000-4000-8000-000000000001', 'task15-del@test.local', '{}');
insert into public.workspaces (id, name, owner_user_id) values
 ('3e000000-0000-4000-8000-000000000001', 'Task 15 Delete', '1e000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status) values
 ('3e000000-0000-4000-8000-000000000001', '1e000000-0000-4000-8000-000000000001', 'owner', 'active');

set local role service_role;
insert into public.projects (id, workspace_id, created_by, title) values
 ('4e000000-0000-4000-8000-000000000001', '3e000000-0000-4000-8000-000000000001',
  '1e000000-0000-4000-8000-000000000001', 'Task 15 Delete');
insert into public.uploaded_videos (
 id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
 mime_type, byte_size, file_size_bytes, status, workflow_version
) values (
 '5e000000-0000-4000-8000-000000000001', '3e000000-0000-4000-8000-000000000001',
 '4e000000-0000-4000-8000-000000000001', 'raw-uploads', 'task15/del.mp4',
 'del.mp4', 'video/mp4', 2048, 2048, 'processing', 'first-draft-v2'
);
insert into public.object_references (
 id, workspace_id, project_id, storage_bucket, storage_path, owner_type, owner_id,
 object_type, mime_type, size_bytes, sha256
) values (
 '8e000000-0000-4000-8000-000000000001', '3e000000-0000-4000-8000-000000000001',
 '4e000000-0000-4000-8000-000000000001', 'raw-uploads', 'task15/del.mp4',
 'uploaded_video', '5e000000-0000-4000-8000-000000000001', 'source_video',
 'video/mp4', 2048, repeat('a', 64)
);
update public.uploaded_videos set
 source_object_reference_id = '8e000000-0000-4000-8000-000000000001',
 verified_size_bytes = 2048, verified_sha256 = repeat('a', 64)
where id = '5e000000-0000-4000-8000-000000000001';

create temporary table t15_del_run as
select result from public.motionly_ensure_first_draft_v2(
 '3e000000-0000-4000-8000-000000000001', '4e000000-0000-4000-8000-000000000001',
 '5e000000-0000-4000-8000-000000000001', '1e000000-0000-4000-8000-000000000001'
) as result;

-- Capture the run's Temporal identity before the project is deleted.
create temporary table t15_run_identity as
select id as run_id, temporal_workflow_id
from public.workflow_runs
where project_id = '4e000000-0000-4000-8000-000000000001';

-- The trigger and its function are installed on projects.
select has_function('public', 'motionly_cancel_project_workflows_on_delete',
  'project-delete workflow cancellation function exists');
select ok(
  exists(
    select 1 from pg_trigger
    where tgrelid = 'public.projects'::regclass
      and tgname = 'motionly_cancel_project_workflows'
      and not tgisinternal
  ),
  'projects carry the BEFORE DELETE workflow cancellation trigger');

-- The run starts non-terminal.
select is(
  (select state from public.workflow_runs
   where project_id = '4e000000-0000-4000-8000-000000000001'),
  'uploaded',
  'the first-draft run is running before deletion');

-- Deleting the project cascades the workflow_runs away. The durable
-- cancellation request has NO foreign key to those rows, so it survives and
-- carries the run's Temporal identity for the dispatcher.
delete from public.projects where id = '4e000000-0000-4000-8000-000000000001';

-- The run rows are gone (the cascade removed them).
select is(
  (select count(*)::integer from public.workflow_runs
   where project_id = '4e000000-0000-4000-8000-000000000001'),
  0,
  'the cascade removed the orphaned workflow run');

-- A durable cancellation request remains with the run's temporal_workflow_id.
select ok(
  exists(
    select 1
    from public.workflow_cancellation_requests c
    join t15_run_identity i
      on c.workflow_run_id = i.run_id
     and c.temporal_workflow_id = i.temporal_workflow_id
    where c.project_id = '4e000000-0000-4000-8000-000000000001'
      and c.reason = 'project_deleted'
      and c.dispatched_at is null
  ),
  'a durable Temporal cancellation survives the cascade with the run identity');

-- The dispatcher can claim it, which marks it dispatched exactly once.
select ok(
  (select count(*)::integer from public.motionly_claim_workflow_cancellations(50)
   where project_id = '4e000000-0000-4000-8000-000000000001') = 1
  and (select count(*)::integer from public.motionly_claim_workflow_cancellations(50)
       where project_id = '4e000000-0000-4000-8000-000000000001') = 0,
  'the dispatcher claims the cancellation request exactly once');

select * from finish();
rollback;
