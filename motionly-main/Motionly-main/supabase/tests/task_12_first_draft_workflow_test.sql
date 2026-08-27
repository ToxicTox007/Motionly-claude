begin;
select plan(42);

select is(
  (select count(*)::integer from information_schema.tables
   where table_schema = 'public'
     and table_name = any(array['workflow_run_artifacts','workflow_run_commands'])),
  2,
  'Task 5 artifact and command ledgers exist'
);
select is(
  (select count(*)::integer from information_schema.columns
   where table_schema = 'public' and table_name = 'workflow_runs'
     and column_name = any(array['retry_of_workflow_run_id','root_workflow_run_id'])),
  2,
  'workflow runs carry immutable retry lineage'
);
select has_column('public', 'uploaded_videos', 'workflow_version',
  'uploads declare durable workflow ownership');
select has_column('public', 'upload_sessions', 'workflow_version',
  'multipart sessions declare durable workflow ownership');
select is(
  (select count(*)::integer from information_schema.columns
   where table_schema = 'public' and table_name = 'render_jobs'
     and column_name = any(array[
       'workflow_run_id','edit_plan_id','timeline_revision_id',
       'source_plan_revision','source_media_revision'
     ])),
  5,
  'render jobs expose workflow and immutable plan/media lineage'
);
select has_column('public', 'exports', 'workflow_run_id',
  'exports expose workflow lineage');
select is(
  (select count(*)::integer from pg_class
   where oid = any(array[
     'public.workflow_run_artifacts'::regclass,
     'public.workflow_run_commands'::regclass
   ]) and relrowsecurity),
  2,
  'RLS is enabled on Task 5 ledgers'
);
select ok(
  exists (select 1 from pg_indexes where schemaname='public'
    and indexname='task5_one_proposal_per_workflow_run_idx'),
  'one proposal target is allowed per workflow run'
);
select ok(
  exists (select 1 from pg_indexes where schemaname='public'
    and indexname='task5_one_draft_render_per_workflow_run_idx'),
  'one draft render target is allowed per workflow run'
);
select ok(
  exists (select 1 from pg_indexes where schemaname='public'
    and indexname='task5_one_base_export_per_render_idx'),
  'one base export is allowed per render under races'
);
select has_function('public','motionly_ensure_first_draft_v2',
  array['uuid','uuid','uuid','uuid'], 'idempotent upload recovery RPC exists');
select has_function('public','motionly_cancel_workflow_run',
  array['uuid','uuid','uuid','integer','text','uuid'], 'fenced cancellation RPC exists');
select has_function('public','motionly_retry_workflow_run',
  array['uuid','uuid','uuid','integer','text','uuid'], 'retry-as-new-run RPC exists');
select has_function('public','motionly_prepare_workflow_step',
  array['uuid','uuid','uuid','text','text','text','integer','text','uuid'],
  'step transport binding RPC exists');
select has_function('public','motionly_link_workflow_artifact',
  array['uuid','uuid','uuid','text','uuid','integer'], 'typed artifact link RPC exists');
select has_function('public','motionly_upsert_base_export',
  array['uuid','uuid','uuid','text','text','text','integer','integer','integer','integer','bigint','text','jsonb'],
  'race-safe base export RPC exists');
select ok(
  not has_function_privilege('anon',
    'public.motionly_cancel_workflow_run(uuid,uuid,uuid,integer,text,uuid)','EXECUTE')
  and not has_function_privilege('authenticated',
    'public.motionly_retry_workflow_run(uuid,uuid,uuid,integer,text,uuid)','EXECUTE')
  and has_function_privilege('service_role',
    'public.motionly_ensure_first_draft_v2(uuid,uuid,uuid,uuid)','EXECUTE'),
  'Task 5 mutation and recovery RPCs are service-role-only'
);
select ok(
  has_table_privilege('authenticated','public.workflow_run_artifacts','SELECT')
  and not has_table_privilege('authenticated','public.workflow_run_artifacts','INSERT')
  and has_table_privilege('authenticated','public.workflow_run_commands','SELECT')
  and not has_table_privilege('authenticated','public.workflow_run_commands','UPDATE'),
  'members can read but cannot mutate Task 5 ledgers'
);
select ok(
  (select proconfig @> array['search_path=public, extensions, pg_temp']
   from pg_proc where oid='public.motionly_cancel_workflow_run(uuid,uuid,uuid,integer,text,uuid)'::regprocedure)
  and (select proconfig @> array['search_path=public, pg_temp']
   from pg_proc where oid='public.motionly_link_workflow_artifact(uuid,uuid,uuid,text,uuid,integer)'::regprocedure),
  'Task 5 security-definer functions have bounded search paths'
);

insert into auth.users (id,email,raw_user_meta_data) values
 ('1c000000-0000-4000-8000-000000000001','task12-a@test.local','{}'),
 ('1c000000-0000-4000-8000-000000000002','task12-b@test.local','{}');
insert into public.workspaces (id,name,owner_user_id) values
 ('3c000000-0000-4000-8000-000000000001','Task 12 A','1c000000-0000-4000-8000-000000000001'),
 ('3c000000-0000-4000-8000-000000000002','Task 12 B','1c000000-0000-4000-8000-000000000002');
insert into public.workspace_members (workspace_id,user_id,role,status) values
 ('3c000000-0000-4000-8000-000000000001','1c000000-0000-4000-8000-000000000001','owner','active'),
 ('3c000000-0000-4000-8000-000000000002','1c000000-0000-4000-8000-000000000002','owner','active');
set local role service_role;
insert into public.projects (id,workspace_id,created_by,title) values
 ('4c000000-0000-4000-8000-000000000001','3c000000-0000-4000-8000-000000000001','1c000000-0000-4000-8000-000000000001','Task 12 A'),
 ('4c000000-0000-4000-8000-000000000002','3c000000-0000-4000-8000-000000000002','1c000000-0000-4000-8000-000000000002','Task 12 B');

insert into public.uploaded_videos (
 id,workspace_id,project_id,storage_bucket,storage_path,original_filename,
 mime_type,byte_size,file_size_bytes,status,workflow_version
) values (
 '5c000000-0000-4000-8000-000000000001','3c000000-0000-4000-8000-000000000001',
 '4c000000-0000-4000-8000-000000000001','raw-uploads','task12/source.mp4',
 'source.mp4','video/mp4',2048,2048,'processing','first-draft-v2'
);
insert into public.object_references (
 id,workspace_id,project_id,storage_bucket,storage_path,owner_type,owner_id,
 object_type,mime_type,size_bytes,sha256
) values (
 '8c000000-0000-4000-8000-000000000001','3c000000-0000-4000-8000-000000000001',
 '4c000000-0000-4000-8000-000000000001','raw-uploads','task12/source.mp4',
 'uploaded_video','5c000000-0000-4000-8000-000000000001','source_video',
 'video/mp4',2048,repeat('a',64)
);
update public.uploaded_videos set
 source_object_reference_id='8c000000-0000-4000-8000-000000000001',
 verified_size_bytes=2048,verified_sha256=repeat('a',64)
where id='5c000000-0000-4000-8000-000000000001';

create temporary table task12_ensure as
select result from public.motionly_ensure_first_draft_v2(
 '3c000000-0000-4000-8000-000000000001','4c000000-0000-4000-8000-000000000001',
 '5c000000-0000-4000-8000-000000000001','1c000000-0000-4000-8000-000000000001'
) as result;
select is((select count(*)::integer from task12_ensure),1,'upload recovery returns one run');
select is((select result->>'workflow_version' from task12_ensure),'first-draft-v2',
  'upload recovery starts first-draft-v2');
select is((select result->>'state' from task12_ensure),'uploaded',
  'new first-draft run starts in uploaded state');

create temporary table task12_ensure_replay as
select result from public.motionly_ensure_first_draft_v2(
 '3c000000-0000-4000-8000-000000000001','4c000000-0000-4000-8000-000000000001',
 '5c000000-0000-4000-8000-000000000001','1c000000-0000-4000-8000-000000000001'
) as result;
select ok((select (result->>'deduplicated')::boolean from task12_ensure_replay),
  'upload recovery is idempotent');
select ok(
  (select count(*)=1 from public.workflow_runs)
  and (select count(*)=1 from public.transactional_outbox),
  'recovery creates one run and one Temporal start outbox');
select ok((select root_workflow_run_id=id from public.workflow_runs),
  'initial workflow is its own immutable retry root');

create temporary table task12_prepare as
select * from public.motionly_prepare_workflow_step(
 '3c000000-0000-4000-8000-000000000001','4c000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from task12_ensure),
 'preprocess-source-v2','preprocess_source',repeat('b',64),3,
 'workflow--task12--preprocess--0001','5c000000-0000-4000-8000-000000000001'
);
select ok((select bullmq_job_id='workflow--task12--preprocess--0001'
  and resource_id='5c000000-0000-4000-8000-000000000001' from task12_prepare),
  'step preparation persists deterministic transport and resource identity');
select is((select count(*)::integer from public.motionly_prepare_workflow_step(
 '3c000000-0000-4000-8000-000000000001','4c000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from task12_ensure),
 'preprocess-source-v2','preprocess_source',repeat('b',64),3,
 'workflow--task12--preprocess--0001','5c000000-0000-4000-8000-000000000001'
)),1,'same step transport identity replays');
select throws_ok(
 $$select * from public.motionly_prepare_workflow_step(
 '3c000000-0000-4000-8000-000000000001','4c000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from task12_ensure),
 'preprocess-source-v2','preprocess_source',repeat('b',64),3,
 'workflow--task12--different--0002','5c000000-0000-4000-8000-000000000001')$$,
 'P0001','WORKFLOW_STEP_TRANSPORT_CONFLICT',
 'step transport identity conflicts fail closed');

create temporary table task12_cancel as
select result from public.motionly_cancel_workflow_run(
 '3c000000-0000-4000-8000-000000000001','4c000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from task12_ensure),1,
 'task12-cancel-0001','1c000000-0000-4000-8000-000000000001'
) as result;
select is((select result->>'state' from task12_cancel),'cancelled',
  'fenced cancellation terminalizes canonical state');
select is((select count(*)::integer from public.transactional_outbox
  where event_type='workflow.cancelled'),1,
  'cancellation emits one Temporal outbox signal');
select ok((select (result->>'deduplicated')::boolean from public.motionly_cancel_workflow_run(
 '3c000000-0000-4000-8000-000000000001','4c000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from task12_ensure),1,
 'task12-cancel-0001','1c000000-0000-4000-8000-000000000001') as replay(result)),
  'same cancellation intent replays after revision advances');

create temporary table task12_retry as
select result from public.motionly_retry_workflow_run(
 '3c000000-0000-4000-8000-000000000001','4c000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from task12_ensure),2,
 'task12-retry-0001','1c000000-0000-4000-8000-000000000001'
) as result;
select is((select result->>'state' from task12_retry),'uploaded',
  'retry creates a fresh uploaded run');
select is((select result->>'retry_of_workflow_run_id' from task12_retry),
  (select result->>'id' from task12_ensure),
  'retry records its immutable parent');
select is((select result->>'root_workflow_run_id' from task12_retry),
  (select result->>'id' from task12_ensure),
  'retry preserves the root chain');
select is((select count(*)::integer from public.transactional_outbox),3,
  'retry emits a new start while preserving start and cancel history');
select ok((select (result->>'deduplicated')::boolean from public.motionly_retry_workflow_run(
 '3c000000-0000-4000-8000-000000000001','4c000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from task12_ensure),2,
 'task12-retry-0001','1c000000-0000-4000-8000-000000000001') as replay(result)),
  'same retry intent returns the same child run');
select throws_ok(
 $$select * from public.motionly_retry_workflow_run(
 '3c000000-0000-4000-8000-000000000001','4c000000-0000-4000-8000-000000000001',
 (select (result->>'id')::uuid from task12_retry),1,
 'task12-retry-running','1c000000-0000-4000-8000-000000000001')$$,
 'P0001','WORKFLOW_NOT_RETRYABLE','nonterminal runs cannot be retried');

insert into public.uploaded_videos (
 id,workspace_id,project_id,storage_bucket,storage_path,original_filename,
 mime_type,byte_size,file_size_bytes,status,workflow_version
) values (
 '5c000000-0000-4000-8000-000000000002','3c000000-0000-4000-8000-000000000001',
 '4c000000-0000-4000-8000-000000000001','raw-uploads','task12/recovery.mp4',
 'recovery.mp4','video/mp4',1024,1024,'processing','first-draft-v2'
);
insert into public.object_references (
 id,workspace_id,project_id,storage_bucket,storage_path,owner_type,owner_id,
 object_type,mime_type,size_bytes,sha256
) values (
 '8c000000-0000-4000-8000-000000000002','3c000000-0000-4000-8000-000000000001',
 '4c000000-0000-4000-8000-000000000001','raw-uploads','task12/recovery.mp4',
 'uploaded_video','5c000000-0000-4000-8000-000000000002','source_video',
 'video/mp4',1024,repeat('c',64)
);
update public.uploaded_videos set
 source_object_reference_id='8c000000-0000-4000-8000-000000000002',
 verified_size_bytes=1024,verified_sha256=repeat('c',64)
where id='5c000000-0000-4000-8000-000000000002';
select is((select count(*)::integer from public.motionly_reconcile_first_draft_v2(10)),1,
  'bounded reconciler repairs a finalized upload missing its workflow');
select is((select count(*)::integer from public.motionly_reconcile_first_draft_v2(10)),0,
  'reconciler is idempotent after repair');
select throws_ok(
 $$select * from public.motionly_ensure_first_draft_v2(
 '3c000000-0000-4000-8000-000000000002','4c000000-0000-4000-8000-000000000002',
 '5c000000-0000-4000-8000-000000000001','1c000000-0000-4000-8000-000000000002')$$,
 'P0002','WORKFLOW_UPLOAD_NOT_FOUND','workflow recovery cannot resolve another tenant upload');
select throws_ok(
 $$update public.workflow_run_commands set idempotency_key='tampered-command'
   where workflow_run_id=(select (result->>'id')::uuid from task12_ensure)$$,
 '55000','IMMUTABLE_RECORD: workflow_run_commands rows are append-only',
 'command history is append-only');
select throws_ok(
 $$update public.workflow_runs set root_workflow_run_id=id
   where id=(select (result->>'id')::uuid from task12_retry)$$,
 '55000','IMMUTABLE_IDENTITY: workflow retry lineage cannot change',
 'retry lineage cannot be rewritten');

select * from finish();
rollback;
