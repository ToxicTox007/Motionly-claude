-- Task 32 pgTAP: first-draft-v1 is retired. New first-draft-v1 workflow_runs are
-- refused by a BEFORE INSERT guard, the default version is first-draft-v2, and
-- pre-existing first-draft-v1 rows remain readable and updatable (history
-- preserved). Forward-only guarantee for the 202610200101 migration.
begin;
select plan(6);

-- The retired-version guard exists as a function and an INSERT-scoped trigger.
select has_function(
  'public', 'motionly_refuse_retired_workflow_version', array[]::text[],
  'retired-version guard function exists'
);
select is(
  (select action_timing || ' ' || event_manipulation
   from information_schema.triggers
   where trigger_name = 'task32_workflow_runs_refuse_retired_version'
     and event_object_table = 'workflow_runs'),
  'BEFORE INSERT',
  'the retired-version guard fires only before insert, never on update'
);

-- New first-draft runs default to the live first-draft-v2 contract.
select is(
  (select column_default from information_schema.columns
   where table_schema = 'public' and table_name = 'workflow_runs'
     and column_name = 'workflow_version'),
  '''first-draft-v2''::text',
  'workflow_runs.workflow_version default is first-draft-v2'
);

set local role postgres;
insert into auth.users (id, email, raw_user_meta_data) values
 ('12000000-0000-4000-8000-0000000000a1', 'task32-v1@test.local', '{}');
insert into public.workspaces (id, name, owner_user_id) values
 ('32000000-0000-4000-8000-0000000000a1', 'Task 32 V1', '12000000-0000-4000-8000-0000000000a1');
insert into public.workspace_members (workspace_id, user_id, role, status) values
 ('32000000-0000-4000-8000-0000000000a1', '12000000-0000-4000-8000-0000000000a1', 'owner', 'active');

set local role service_role;
insert into public.projects (id, workspace_id, created_by, title) values
 ('42000000-0000-4000-8000-0000000000a1', '32000000-0000-4000-8000-0000000000a1',
  '12000000-0000-4000-8000-0000000000a1', 'Task 32 V1');
insert into public.uploaded_videos (
 id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
 mime_type, byte_size, file_size_bytes, status
) values (
 '52000000-0000-4000-8000-0000000000a1', '32000000-0000-4000-8000-0000000000a1',
 '42000000-0000-4000-8000-0000000000a1', 'raw-uploads', 'task32/source.mp4',
 'source.mp4', 'video/mp4', 2048, 2048, 'processing'
);
insert into public.object_references (
 id, workspace_id, project_id, storage_bucket, storage_path, owner_type, owner_id,
 object_type, mime_type, size_bytes, sha256
) values (
 '82000000-0000-4000-8000-0000000000a1', '32000000-0000-4000-8000-0000000000a1',
 '42000000-0000-4000-8000-0000000000a1', 'raw-uploads', 'task32/source.mp4',
 'uploaded_video', '52000000-0000-4000-8000-0000000000a1', 'source_video',
 'video/mp4', 2048, repeat('a', 64)
);
update public.uploaded_videos set
 source_object_reference_id = '82000000-0000-4000-8000-0000000000a1',
 verified_size_bytes = 2048, verified_sha256 = repeat('a', 64)
where id = '52000000-0000-4000-8000-0000000000a1';

-- A NEW first-draft-v1 run is refused by the guard.
select throws_ok(
  $$select * from public.motionly_start_workflow_run(
    '32000000-0000-4000-8000-0000000000a1',
    '42000000-0000-4000-8000-0000000000a1',
    '52000000-0000-4000-8000-0000000000a1', 2,
    '82000000-0000-4000-8000-0000000000a1',
    'first_draft', 'first-draft-v1', 'task32-new-v1-0001', repeat('b', 64),
    '12000000-0000-4000-8000-0000000000a1', '{}'::jsonb
  )$$,
  'P0001', 'WORKFLOW_VERSION_RETIRED: first-draft-v1 is retired; start first-draft-v2',
  'a new first-draft-v1 workflow run is refused'
);

-- A new first-draft-v2 run is still accepted.
create temporary table task32_v2_run as
select result from public.motionly_start_workflow_run(
  '32000000-0000-4000-8000-0000000000a1',
  '42000000-0000-4000-8000-0000000000a1',
  '52000000-0000-4000-8000-0000000000a1', 2,
  '82000000-0000-4000-8000-0000000000a1',
  'first_draft', 'first-draft-v2', 'task32-new-v2-0001', repeat('c', 64),
  '12000000-0000-4000-8000-0000000000a1', '{}'::jsonb
) as result;
select is(
  (select result->>'workflow_version' from task32_v2_run), 'first-draft-v2',
  'a new first-draft-v2 workflow run is still accepted'
);

-- A pre-existing first-draft-v1 row (seeded with triggers bypassed via
-- session_replication_role, modelling history created before the migration)
-- remains readable and updatable: the guard never fires on UPDATE, so revision
-- bumps and state transitions on legacy rows still work.
set local role postgres;
set local session_replication_role = replica;
insert into public.workflow_runs (
  id, workspace_id, project_id, uploaded_video_id, source_media_revision,
  source_object_reference_id, workflow_type, workflow_version, idempotency_key,
  request_hash, temporal_workflow_id, state, root_workflow_run_id
) values (
  '62000000-0000-4000-8000-0000000000a1',
  '32000000-0000-4000-8000-0000000000a1',
  '42000000-0000-4000-8000-0000000000a1',
  '52000000-0000-4000-8000-0000000000a1', 2,
  '82000000-0000-4000-8000-0000000000a1',
  'first_draft', 'first-draft-v1', 'task32-legacy-v1-0001', repeat('d', 64),
  'motionly:legacy:task32-v1', 'analyzing',
  '62000000-0000-4000-8000-0000000000a1'
);
set local session_replication_role = origin;
set local role service_role;

update public.workflow_runs set revision = revision + 1, state = 'proposal_ready'
where id = '62000000-0000-4000-8000-0000000000a1';
select is(
  (select workflow_version || ':' || state || ':' || revision
   from public.workflow_runs where id = '62000000-0000-4000-8000-0000000000a1'),
  'first-draft-v1:proposal_ready:2',
  'a pre-existing first-draft-v1 row is still readable and updatable'
);

select * from finish();
rollback;
