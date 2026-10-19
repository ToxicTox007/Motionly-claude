begin;
select plan(73);

select is(
  (select count(*)::integer from information_schema.tables
   where table_schema = 'public' and table_name = any(array[
     'workflow_runs', 'workflow_steps', 'workflow_step_attempts',
     'workflow_events', 'transactional_outbox'
   ])),
  5,
  'all Task 7 workflow and outbox tables exist'
);

select is(
  (select count(*)::integer from information_schema.columns
   where table_schema = 'public' and table_name = 'workflow_runs'
     and column_name = any(array[
       'idempotency_key', 'request_hash', 'source_media_revision',
       'source_object_reference_id', 'source_size_bytes', 'source_sha256',
       'temporal_workflow_id', 'active_plan_revision'
     ])),
  8,
  'workflow runs pin request, source, Temporal, and plan lineage'
);

select is(
  (select count(*)::integer from information_schema.columns
   where table_schema = 'public' and table_name = 'workflow_steps'
     and column_name = any(array[
       'step_key', 'step_kind', 'state', 'revision', 'attempt_count',
       'max_attempts', 'input_hash', 'output_hash'
     ])),
  8,
  'workflow steps carry CAS, retry, and result identities'
);

select is(
  (select count(*)::integer from information_schema.columns
   where table_schema = 'public' and table_name = 'workflow_step_attempts'
     and column_name = any(array[
       'attempt_number', 'attempt_key', 'claim_token', 'lease_owner',
       'lease_expires_at', 'result_hash', 'ended_at'
     ])),
  7,
  'attempts carry durable lease and fencing evidence'
);

select is(
  (select count(*)::integer from information_schema.columns
   where table_schema = 'public' and table_name = 'transactional_outbox'
     and column_name = any(array[
       'destination', 'event_type', 'deduplication_key', 'payload_hash',
       'state', 'available_at', 'lease_owner', 'lease_token',
       'lease_expires_at', 'claim_generation', 'attempt_count', 'max_attempts'
     ])),
  12,
  'outbox carries deterministic identity, bounded retry, lease, and fencing state'
);

select is(
  (select count(*)::integer from pg_class
   where oid = any(array[
     'public.workflow_runs'::regclass,
     'public.workflow_steps'::regclass,
     'public.workflow_step_attempts'::regclass,
     'public.workflow_events'::regclass,
     'public.transactional_outbox'::regclass
   ]) and relrowsecurity),
  5,
  'RLS is enabled on every Task 7 table'
);

select ok(
  not exists (
    select 1 from pg_indexes
    where schemaname = 'public' and lower(indexdef) like '%where%now(%'
  ),
  'claim indexes contain no volatile now predicate'
);

select ok(
  exists (
    select 1 from pg_indexes
    where schemaname = 'public'
      and indexname = 'task7_workflow_attempts_one_active_idx'
      and indexdef like '%WHERE (state = ''active''::text)%'
  ),
  'database enforces at most one active attempt per step'
);

select has_function(
  'public', 'motionly_start_workflow_run',
  array['uuid','uuid','uuid','integer','uuid','text','text','text','text','uuid','jsonb'],
  'atomic workflow start RPC exists'
);
select has_function(
  'public', 'motionly_claim_workflow_outbox', array['text','integer','integer'],
  'bounded SKIP LOCKED outbox claim RPC exists'
);
select has_function(
  'public', 'motionly_begin_workflow_step',
  array['uuid','uuid','uuid','text','text','text','text','integer','text','integer'],
  'fenced step begin RPC exists'
);
select has_function(
  'public', 'motionly_complete_workflow_step',
  array['uuid','uuid','uuid','text','uuid','text','jsonb','text','text'],
  'atomic step completion RPC exists'
);
select has_function(
  'public', 'motionly_fail_workflow_step',
  array['uuid','uuid','uuid','text','uuid','text','text','boolean'],
  'atomic step failure RPC exists'
);

select ok(
  not has_function_privilege(
    'authenticated',
    'public.motionly_start_workflow_run(uuid,uuid,uuid,integer,uuid,text,text,text,text,uuid,jsonb)',
    'EXECUTE'
  )
  and not has_function_privilege(
    'anon',
    'public.motionly_claim_workflow_outbox(text,integer,integer)',
    'EXECUTE'
  )
  and has_function_privilege(
    'service_role',
    'public.motionly_complete_workflow_step(uuid,uuid,uuid,text,uuid,text,jsonb,text,text)',
    'EXECUTE'
  ),
  'workflow mutation RPCs are service-role-only'
);

select ok(
  not exists (
    select 1
    from unnest(array[
      'workflow_runs', 'workflow_steps', 'workflow_step_attempts',
      'workflow_events', 'transactional_outbox'
    ]) as target(table_name)
    where has_table_privilege('authenticated', format('public.%I', table_name), 'INSERT')
       or has_table_privilege('authenticated', format('public.%I', table_name), 'UPDATE')
       or has_table_privilege('authenticated', format('public.%I', table_name), 'DELETE')
  ),
  'authenticated clients cannot mutate canonical workflow state'
);

select ok(
  pg_get_functiondef('public.motionly_increment_media_revision()'::regprocedure)
    like '%source_object_reference_id%'
  and pg_get_functiondef('public.motionly_increment_media_revision()'::regprocedure)
    like '%verified_size_bytes%'
  and pg_get_functiondef('public.motionly_increment_media_revision()'::regprocedure)
    like '%verified_sha256%',
  'source object and verification changes advance media revision'
);

insert into auth.users (id, email, raw_user_meta_data)
values
  ('17000000-0000-4000-8000-000000000001', 'task7-owner-a@test.local', '{}'::jsonb),
  ('17000000-0000-4000-8000-000000000002', 'task7-owner-b@test.local', '{}'::jsonb);

insert into public.workspaces (id, name, owner_user_id)
values
  ('37000000-0000-4000-8000-000000000001', 'Task 7 A', '17000000-0000-4000-8000-000000000001'),
  ('37000000-0000-4000-8000-000000000002', 'Task 7 B', '17000000-0000-4000-8000-000000000002');

insert into public.workspace_members (workspace_id, user_id, role, status)
values
  ('37000000-0000-4000-8000-000000000001', '17000000-0000-4000-8000-000000000001', 'owner', 'active'),
  ('37000000-0000-4000-8000-000000000002', '17000000-0000-4000-8000-000000000002', 'owner', 'active');

set local role service_role;

insert into public.projects (id, workspace_id, created_by, title)
values
  ('47000000-0000-4000-8000-000000000001', '37000000-0000-4000-8000-000000000001', '17000000-0000-4000-8000-000000000001', 'Task 7 A project'),
  ('47000000-0000-4000-8000-000000000002', '37000000-0000-4000-8000-000000000002', '17000000-0000-4000-8000-000000000002', 'Task 7 B project');

insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path,
  original_filename, mime_type, byte_size, file_size_bytes, status
) values (
  '57000000-0000-4000-8000-000000000001',
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  'raw-uploads',
  'workspaces/37000000-0000-4000-8000-000000000001/projects/47000000-0000-4000-8000-000000000001/raw/source.mp4',
  'source.mp4', 'video/mp4', 2048, 2048, 'uploading'
);

insert into public.object_references (
  id, workspace_id, project_id, storage_bucket, storage_path,
  owner_type, owner_id, object_type, mime_type, size_bytes, sha256
) values (
  '87000000-0000-4000-8000-000000000001',
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  'raw-uploads',
  'workspaces/37000000-0000-4000-8000-000000000001/projects/47000000-0000-4000-8000-000000000001/raw/source.mp4',
  'uploaded_video', '57000000-0000-4000-8000-000000000001',
  'source_video', 'video/mp4', 2048, repeat('a', 64)
);

update public.uploaded_videos
set source_object_reference_id = '87000000-0000-4000-8000-000000000001',
    verified_size_bytes = 2048,
    verified_sha256 = repeat('a', 64)
where id = '57000000-0000-4000-8000-000000000001';

create temporary table task7_first_start as
select result
from public.motionly_start_workflow_run(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  '57000000-0000-4000-8000-000000000001',
  2,
  '87000000-0000-4000-8000-000000000001',
  'first_draft', 'first-draft-v1', 'task7-first-draft-0001', repeat('b', 64),
  '17000000-0000-4000-8000-000000000001', '{"locale":"en"}'::jsonb
) as result;

select is((select count(*)::integer from task7_first_start), 1, 'workflow start returns one run');
select is(
  (select (result->>'deduplicated')::boolean from task7_first_start), false,
  'first workflow start is not a replay'
);

create temporary table task7_start_replay as
select result
from public.motionly_start_workflow_run(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  '57000000-0000-4000-8000-000000000001',
  2,
  '87000000-0000-4000-8000-000000000001',
  'first_draft', 'first-draft-v1', 'task7-first-draft-0001', repeat('b', 64),
  '17000000-0000-4000-8000-000000000001', '{"locale":"en"}'::jsonb
) as result;

select is(
  (select (result->>'deduplicated')::boolean from task7_start_replay), true,
  'same start key and hash returns an idempotent replay'
);
select is((select count(*)::integer from public.workflow_runs), 1, 'start replay creates one run');
select is((select count(*)::integer from public.workflow_events), 1, 'start replay creates one event');
select is((select count(*)::integer from public.transactional_outbox), 1, 'start replay creates one outbox record');
select ok(
  (select payload ? 'workflow_run_id' and payload ? 'source_media_revision'
      and not payload ? 'credential' and not payload ? 'media_bytes'
   from public.transactional_outbox),
  'outbox start payload contains identifiers but no credentials or media bytes'
);

select throws_ok(
  $$select * from public.motionly_start_workflow_run(
    '37000000-0000-4000-8000-000000000001',
    '47000000-0000-4000-8000-000000000001',
    '57000000-0000-4000-8000-000000000001', 2,
    '87000000-0000-4000-8000-000000000001',
    'first_draft', 'first-draft-v1', 'task7-first-draft-0001', repeat('c', 64),
    '17000000-0000-4000-8000-000000000001', '{}'::jsonb
  )$$,
  'P0001', 'WORKFLOW_IDEMPOTENCY_CONFLICT',
  'same workflow key with a different request hash conflicts'
);

select throws_ok(
  $$select * from public.motionly_start_workflow_run(
    '37000000-0000-4000-8000-000000000001',
    '47000000-0000-4000-8000-000000000001',
    '57000000-0000-4000-8000-000000000001', 1,
    '87000000-0000-4000-8000-000000000001',
    'first_draft', 'first-draft-v1', 'task7-stale-source-0001', repeat('d', 64),
    '17000000-0000-4000-8000-000000000001', '{}'::jsonb
  )$$,
  'P0001', 'WORKFLOW_SOURCE_STALE',
  'workflow start rejects a stale source media revision'
);

select throws_ok(
  $$select * from public.motionly_start_workflow_run(
    '37000000-0000-4000-8000-000000000002',
    '47000000-0000-4000-8000-000000000002',
    '57000000-0000-4000-8000-000000000001', 2,
    '87000000-0000-4000-8000-000000000001',
    'first_draft', 'first-draft-v1', 'task7-cross-tenant-0001', repeat('e', 64),
    '17000000-0000-4000-8000-000000000002', '{}'::jsonb
  )$$,
  'P0002', 'WORKFLOW_UPLOAD_NOT_FOUND',
  'workflow start cannot resolve another tenant upload'
);

update public.uploaded_videos
set verified_sha256 = repeat('f', 64)
where id = '57000000-0000-4000-8000-000000000001';
select is(
  (select media_revision from public.uploaded_videos
   where id = '57000000-0000-4000-8000-000000000001'),
  3,
  'verified source identity replacement increments media revision'
);

select throws_ok(
  $$select * from public.motionly_start_workflow_run(
    '37000000-0000-4000-8000-000000000001',
    '47000000-0000-4000-8000-000000000001',
    '57000000-0000-4000-8000-000000000001', 2,
    '87000000-0000-4000-8000-000000000001',
    'first_draft', 'first-draft-v1', 'task7-old-revision-0001', repeat('1', 64),
    '17000000-0000-4000-8000-000000000001', '{}'::jsonb
  )$$,
  'P0001', 'WORKFLOW_SOURCE_STALE',
  'source replacement invalidates workflow starts pinned to the old revision'
);

create temporary table task7_second_start as
select result
from public.motionly_start_workflow_run(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  '57000000-0000-4000-8000-000000000001',
  3,
  '87000000-0000-4000-8000-000000000001',
  'first_draft', 'first-draft-v1', 'task7-first-draft-0002', repeat('2', 64),
  '17000000-0000-4000-8000-000000000001', '{}'::jsonb
) as result;
select is((select count(*)::integer from public.workflow_runs), 2, 'new media revision can start a new run');
select is(
  (select count(distinct temporal_workflow_id)::integer from public.workflow_runs), 2,
  'each accepted run has a stable distinct Temporal workflow ID'
);

-- Replacing the source superseded the first run (202608090007): its
-- undelivered start was dead-lettered and a cancellation was queued. Every row
-- here shares now(), so make the second run's start strictly earliest: the
-- claims below take that start first and then the queued cancellation.
select is(
  (select state from public.transactional_outbox
   where workflow_run_id = (select (result->>'id')::uuid from task7_first_start)
     and event_type = 'workflow.start'),
  'dead_letter',
  'source replacement dead-letters the superseded run''s undelivered start'
);
select is(
  (select state from public.transactional_outbox
   where workflow_run_id = (select (result->>'id')::uuid from task7_first_start)
     and event_type = 'workflow.cancelled'),
  'pending',
  'source replacement queues a cancellation for the superseded run'
);
update public.transactional_outbox
set available_at = now() - interval '1 second'
where workflow_run_id = (select (result->>'id')::uuid from task7_second_start)
  and event_type = 'workflow.start';

create temporary table task7_first_claim as
select * from public.motionly_claim_workflow_outbox('dispatcher-a', 1, 60);
select is(
  (select event_type from task7_first_claim), 'workflow.start',
  'the earliest available message is claimed first'
);
select is(
  (select state from task7_first_claim), 'leased',
  'outbox claim leases one available record'
);
select ok(
  (select lease_token is not null and claim_generation = 1 from task7_first_claim),
  'outbox claim returns a first-generation fencing token'
);
select throws_ok(
  $$select * from public.motionly_ack_workflow_outbox(
    (select id from task7_first_claim), gen_random_uuid(),
    'temporal-stale', 'temporal-run-stale'
  )$$,
  'P0001', 'OUTBOX_STALE_CLAIM',
  'stale outbox token cannot acknowledge delivery'
);

create temporary table task7_first_ack as
select * from public.motionly_ack_workflow_outbox(
  (select id from task7_first_claim),
  (select lease_token from task7_first_claim),
  'temporal-message-1', 'temporal-run-1'
);
select is((select state from task7_first_ack), 'dispatched', 'current claim can acknowledge delivery');
select is(
  (select state from public.motionly_ack_workflow_outbox(
    (select id from task7_first_claim),
    (select lease_token from task7_first_claim),
    'temporal-message-1', 'temporal-run-1'
  )),
  'dispatched',
  'same delivery acknowledgment is idempotent'
);

create temporary table task7_second_claim as
select * from public.motionly_claim_workflow_outbox('dispatcher-b', 1, 60);
select is(
  (select event_type from task7_second_claim), 'workflow.cancelled',
  'the queued cancellation is the next available message'
);
create temporary table task7_first_failure as
select * from public.motionly_fail_workflow_outbox(
  (select id from task7_second_claim),
  (select lease_token from task7_second_claim),
  'temporal_unavailable', 'retry safely', now()
);
select is((select state from task7_first_failure), 'failed', 'failed dispatch becomes retryable');

create temporary table task7_retry_claim as
select * from public.motionly_claim_workflow_outbox('dispatcher-c', 1, 60);
select is(
  (select claim_generation from task7_retry_claim), 2,
  'retry receives a new fencing generation'
);
select throws_ok(
  $$select * from public.motionly_fail_workflow_outbox(
    (select id from task7_retry_claim),
    (select lease_token from task7_second_claim),
    'stale_dispatcher', 'must not overwrite', now()
  )$$,
  'P0001', 'OUTBOX_STALE_CLAIM',
  'old fencing token cannot fail a reclaimed message'
);

create temporary table task7_begin as
select result
from public.motionly_begin_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_second_start),
  'analyze-source-v1', 'analyze_source', 'task7-attempt-0001',
  'worker-a', 60, repeat('3', 64), 3
) as result;
select is(
  (select result->'step'->>'state' from task7_begin), 'running',
  'begin step creates a running fenced attempt'
);

create temporary table task7_begin_replay as
select result
from public.motionly_begin_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_second_start),
  'analyze-source-v1', 'analyze_source', 'task7-attempt-0001',
  'worker-a', 60, repeat('3', 64), 3
) as result;
select is(
  (select (result->>'deduplicated')::boolean from task7_begin_replay), true,
  'same step attempt key replays without a second attempt'
);

create temporary table task7_complete as
select result
from public.motionly_complete_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_second_start),
  'analyze-source-v1',
  (select (result->'attempt'->>'claim_token')::uuid from task7_begin),
  repeat('4', 64), '{"analysis_id":"analysis-1"}'::jsonb,
  'proposal_ready', 'workflow.analysis.completed'
) as result;
select is(
  (select result->'step'->>'state' from task7_complete), 'succeeded',
  'current attempt atomically completes a step'
);
-- Run 1: start and source-superseded events; start and cancellation messages.
-- Run 2: start and completion events; start and successor messages.
select ok(
  (select count(*) = 2 from public.workflow_events
   where workflow_run_id = (select (result->>'id')::uuid from task7_second_start))
  and (select count(*) = 2 from public.transactional_outbox
   where workflow_run_id = (select (result->>'id')::uuid from task7_second_start))
  and (select count(*) = 4 from public.workflow_events)
  and (select count(*) = 4 from public.transactional_outbox),
  'completion appends one event and one successor outbox message'
);

create temporary table task7_complete_replay as
select result
from public.motionly_complete_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_second_start),
  'analyze-source-v1',
  (select (result->'attempt'->>'claim_token')::uuid from task7_begin),
  repeat('4', 64), '{"analysis_id":"analysis-1"}'::jsonb,
  'proposal_ready', 'workflow.analysis.completed'
) as result;
select is(
  (select (result->>'deduplicated')::boolean from task7_complete_replay), true,
  'same result hash completion is an idempotent replay'
);

select throws_ok(
  $$select * from public.motionly_complete_workflow_step(
    '37000000-0000-4000-8000-000000000001',
    '47000000-0000-4000-8000-000000000001',
    (select (result->>'id')::uuid from task7_second_start),
    'analyze-source-v1',
    (select (result->'attempt'->>'claim_token')::uuid from task7_begin),
    repeat('5', 64), '{"analysis_id":"conflict"}'::jsonb,
    'proposal_ready', 'workflow.analysis.completed'
  )$$,
  'P0001', 'WORKFLOW_STEP_RESULT_CONFLICT',
  'different result hash cannot replace accepted completion'
);

select throws_ok(
  $$update public.workflow_step_attempts
    set error_code = 'tampered'
    where id = (select (result->'attempt'->>'id')::uuid from task7_begin)$$,
  '55000', 'IMMUTABLE_RECORD: terminal workflow attempts cannot change',
  'terminal attempts are immutable'
);
select throws_ok(
  $$update public.workflow_events
    set event_type = 'tampered'
    where id = (select id from public.workflow_events order by created_at limit 1)$$,
  '55000', 'IMMUTABLE_RECORD: workflow_events rows are append-only',
  'event history is append-only'
);
select throws_ok(
  $$insert into public.workflow_steps (
      workspace_id, project_id, workflow_run_id, step_key, step_kind
    ) values (
      '37000000-0000-4000-8000-000000000002',
      '47000000-0000-4000-8000-000000000002',
      (select (result->>'id')::uuid from task7_second_start),
      'forged-step', 'forged'
    )$$,
  '23503', null,
  'step foreign key rejects cross-tenant workflow identity'
);
select is(
  (select count(*)::integer from public.workflow_step_attempts where state = 'active'),
  0,
  'successful completion leaves no active attempt'
);

-- Independent audit regression coverage.
update public.transactional_outbox
set max_attempts = attempt_count,
    lease_expires_at = now() - interval '1 second'
where id = (select id from task7_retry_claim);
create temporary table task7_reaper_claim as
select * from public.motionly_claim_workflow_outbox('dispatcher-reaper', 1, 60);
select is(
  (select state from public.transactional_outbox
   where id = (select id from task7_retry_claim)),
  'dead_letter',
  'an expired final outbox lease is atomically dead-lettered'
);

select throws_ok(
  $$insert into public.workflow_step_attempts (
      workspace_id, project_id, workflow_run_id, workflow_step_id,
      attempt_number, attempt_key, state, lease_owner, lease_expires_at
    ) values (
      '37000000-0000-4000-8000-000000000001',
      '47000000-0000-4000-8000-000000000001',
      (select (result->>'id')::uuid from task7_first_start),
      (select id from public.workflow_steps where step_key = 'analyze-source-v1'),
      99, 'same-tenant-cross-run-attempt', 'active', 'forged', now() + interval '1 minute'
    )$$,
  '23503', null,
  'attempt cannot link a same-tenant step from another workflow run'
);

select throws_ok(
  $$insert into public.workflow_events (
      workspace_id, project_id, workflow_run_id, workflow_step_id,
      sequence_number, event_type, event_key, payload
    ) values (
      '37000000-0000-4000-8000-000000000001',
      '47000000-0000-4000-8000-000000000001',
      (select (result->>'id')::uuid from task7_first_start),
      (select id from public.workflow_steps where step_key = 'analyze-source-v1'),
      99, 'forged', 'same-tenant-cross-run-event', '{}'::jsonb
    )$$,
  '23503', null,
  'event cannot link a same-tenant step from another workflow run'
);

select throws_ok(
  $$insert into public.transactional_outbox (
      workspace_id, project_id, workflow_run_id, workflow_event_id,
      destination, event_type, deduplication_key, payload_hash, payload
    ) values (
      '37000000-0000-4000-8000-000000000001',
      '47000000-0000-4000-8000-000000000001',
      (select (result->>'id')::uuid from task7_first_start),
      (select id from public.workflow_events
       where workflow_run_id = (select (result->>'id')::uuid from task7_second_start)
       order by sequence_number limit 1),
      'bullmq', 'forged', 'same-tenant-cross-run-outbox', repeat('6', 64), '{}'::jsonb
    )$$,
  '23503', null,
  'outbox cannot link a same-tenant event from another workflow run'
);

select throws_ok(
  $$update public.workflow_runs
    set request_hash = repeat('7', 64)
    where id = (select (result->>'id')::uuid from task7_first_start)$$,
  '55000',
  'IMMUTABLE_IDENTITY: workflow run identity and source lineage cannot change',
  'accepted workflow request and source identity are immutable'
);

select throws_ok(
  $$select * from public.motionly_ack_workflow_outbox(
    (select id from task7_first_ack),
    (select lease_token from task7_first_claim),
    'temporal-message-1', 'conflicting-temporal-run'
  )$$,
  'P0001', 'OUTBOX_TEMPORAL_RUN_ID_CONFLICT',
  'Temporal start acknowledgment rejects a conflicting run identity'
);

select throws_ok(
  $$update public.workflow_runs
    set state = 'draft_ready', revision = revision + 1
    where id = (select (result->>'id')::uuid from task7_second_start)$$,
  'P0001', 'WORKFLOW_TRANSITION_INVALID: proposal_ready -> draft_ready',
  'workflow projection rejects skipped lifecycle transitions'
);

-- The first run was superseded by the source replacement above, so the
-- final-expiry checks use their own run on the current source revision.
create temporary table task7_final_start as
select result
from public.motionly_start_workflow_run(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  '57000000-0000-4000-8000-000000000001', 3,
  '87000000-0000-4000-8000-000000000001',
  'first_draft', 'first-draft-v1', 'task7-final-expiry-0001', repeat('a', 64),
  '17000000-0000-4000-8000-000000000001', '{}'::jsonb
) as result;
create temporary table task7_final_begin as
select result
from public.motionly_begin_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_final_start),
  'final-expiry-v1', 'analyze_source', 'final-expiry-attempt-0001',
  'worker-final', 60, repeat('8', 64), 1
) as result;
select is(
  (select result->'step'->>'state' from task7_final_begin),
  'running',
  'final bounded step attempt begins normally'
);
update public.workflow_step_attempts
set lease_expires_at = now() - interval '1 second'
where id = (select (result->'attempt'->>'id')::uuid from task7_final_begin);
create temporary table task7_final_exhausted as
select result
from public.motionly_begin_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_final_start),
  'final-expiry-v1', 'analyze_source', 'final-expiry-attempt-0002',
  'worker-reaper', 60, repeat('8', 64), 1
) as result;
select is(
  (select (result->>'exhausted')::boolean from task7_final_exhausted),
  true,
  'expired final step attempt commits exhaustion instead of rolling back'
);
select is(
  (select state from public.workflow_runs
   where id = (select (result->>'id')::uuid from task7_final_start)),
  'failed',
  'step exhaustion terminalizes the workflow run'
);
select is(
  (select count(*)::integer from public.workflow_step_attempts
   where workflow_step_id = (select id from public.workflow_steps
                             where step_key = 'final-expiry-v1')
     and state = 'active'),
  0,
  'step exhaustion leaves no active attempt wedge'
);
select throws_ok(
  $$update public.workflow_runs
    set state = 'analyzing', revision = revision + 1
    where id = (select (result->>'id')::uuid from task7_final_start)$$,
  '55000', 'WORKFLOW_TERMINAL: terminal workflow lifecycle cannot change',
  'terminal workflow cannot be reopened'
);

create temporary table task7_third_start as
select result
from public.motionly_start_workflow_run(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  '57000000-0000-4000-8000-000000000001', 3,
  '87000000-0000-4000-8000-000000000001',
  'first_draft', 'first-draft-v1', 'task7-first-draft-0003', repeat('9', 64),
  '17000000-0000-4000-8000-000000000001', '{}'::jsonb
) as result;
create temporary table task7_expired_failure_begin as
select result
from public.motionly_begin_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_third_start),
  'expired-failure-v1', 'side_effect', 'expired-failure-attempt',
  'worker-expired', 60, null, 2
) as result;
update public.workflow_step_attempts
set lease_expires_at = now() - interval '1 second'
where id = (select (result->'attempt'->>'id')::uuid from task7_expired_failure_begin);
select throws_ok(
  $$select * from public.motionly_fail_workflow_step(
    '37000000-0000-4000-8000-000000000001',
    '47000000-0000-4000-8000-000000000001',
    (select (result->>'id')::uuid from task7_third_start),
    'expired-failure-v1',
    (select (result->'attempt'->>'claim_token')::uuid from task7_expired_failure_begin),
    'late_failure', 'must be fenced', false
  )$$,
  'P0001', 'WORKFLOW_STEP_STALE_CLAIM',
  'expired step token cannot fail a workflow'
);

create temporary table task7_scoped_attempt_a as
select result from public.motionly_begin_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_third_start),
  'scoped-attempt-a', 'parallel', 'shared-step-attempt-key',
  'worker-a', 60, null, 3
) as result;
create temporary table task7_scoped_attempt_b as
select result from public.motionly_begin_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_third_start),
  'scoped-attempt-b', 'parallel', 'shared-step-attempt-key',
  'worker-b', 60, null, 3
) as result;
select isnt(
  (select result->'attempt'->>'id' from task7_scoped_attempt_a),
  (select result->'attempt'->>'id' from task7_scoped_attempt_b),
  'attempt idempotency keys are scoped to their logical step'
);

select throws_ok(
  $$select * from public.motionly_begin_workflow_step(
    '37000000-0000-4000-8000-000000000001',
    '47000000-0000-4000-8000-000000000001',
    (select (result->>'id')::uuid from task7_third_start),
    'scoped-attempt-a', 'changed-kind', 'shared-step-attempt-key',
    'worker-a', 60, repeat('a', 64), 4
  )$$,
  'P0001', 'WORKFLOW_STEP_IDEMPOTENCY_CONFLICT',
  'attempt replay validates logical step identity before deduplication'
);

create temporary table task7_terminal_parallel as
select result from public.motionly_fail_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_third_start),
  'scoped-attempt-a',
  (select (result->'attempt'->>'claim_token')::uuid from task7_scoped_attempt_a),
  'terminal_parallel_failure', 'close every sibling', false
) as result;
select ok(
  (select state = 'cancelled'
   from public.workflow_step_attempts
   where id = (select (result->'attempt'->>'id')::uuid from task7_scoped_attempt_b))
  and not exists (
    select 1 from public.workflow_step_attempts
    where workflow_run_id = (select (result->>'id')::uuid from task7_third_start)
      and state = 'active'
  ),
  'terminal transition cancels every parallel sibling attempt'
);

create temporary table task7_terminal_sibling_replay as
select result from public.motionly_begin_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_third_start),
  'scoped-attempt-b', 'parallel', 'shared-step-attempt-key',
  'worker-b', 60, null, 3
) as result;
select ok(
  (select (result->>'deduplicated')::boolean
   from task7_terminal_sibling_replay)
  and (select result->'attempt'->>'state' = 'cancelled'
       from task7_terminal_sibling_replay),
  'terminal sibling replay returns cancelled evidence, never an active claim'
);

select throws_ok(
  $$select * from public.motionly_begin_workflow_step(
    '37000000-0000-4000-8000-000000000001',
    '47000000-0000-4000-8000-000000000001',
    (select (result->>'id')::uuid from task7_third_start),
    'scoped-attempt-b', 'changed-kind-only', 'shared-step-attempt-key',
    'worker-b', 60, null, 3
  )$$,
  'P0001', 'WORKFLOW_STEP_IDEMPOTENCY_CONFLICT',
  'replay rejects a changed step kind even when other identity fields match'
);
select throws_ok(
  $$select * from public.motionly_begin_workflow_step(
    '37000000-0000-4000-8000-000000000001',
    '47000000-0000-4000-8000-000000000001',
    (select (result->>'id')::uuid from task7_third_start),
    'scoped-attempt-b', 'parallel', 'shared-step-attempt-key',
    'worker-b', 60, null, 4
  )$$,
  'P0001', 'WORKFLOW_STEP_IDEMPOTENCY_CONFLICT',
  'replay rejects a changed maximum attempt bound'
);
select throws_ok(
  $$select * from public.motionly_begin_workflow_step(
    '37000000-0000-4000-8000-000000000001',
    '47000000-0000-4000-8000-000000000001',
    (select (result->>'id')::uuid from task7_third_start),
    'scoped-attempt-b', null, 'shared-step-attempt-key',
    'worker-b', 60, null, 3
  )$$,
  '22023', 'WORKFLOW_STEP_ARGUMENT_REQUIRED',
  'replay rejects null required identity before deduplication'
);

create temporary table task7_normalized_error_start as
select result from public.motionly_start_workflow_run(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  '57000000-0000-4000-8000-000000000001', 3,
  '87000000-0000-4000-8000-000000000001',
  'first_draft', 'first-draft-v1', 'task7-normalized-error-0001', repeat('8', 64),
  '17000000-0000-4000-8000-000000000001', '{}'::jsonb
) as result;
create temporary table task7_normalized_error_begin as
select result from public.motionly_begin_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_normalized_error_start),
  'normalized-error-v1', 'analyze_source', 'normalized-error-attempt',
  'worker-normalized-error', 60, repeat('8', 64), 3
) as result;
create temporary table task7_normalized_error_failure as
select result from public.motionly_fail_workflow_step(
  '37000000-0000-4000-8000-000000000001',
  '47000000-0000-4000-8000-000000000001',
  (select (result->>'id')::uuid from task7_normalized_error_start),
  'normalized-error-v1',
  (select (result->'attempt'->>'claim_token')::uuid from task7_normalized_error_begin),
  'GenericNodeError', 'generic worker failure', true
) as result;
select ok(
  (select error_code = 'generic_node_error'
   from public.workflow_step_attempts
   where id = (select (result->'attempt'->>'id')::uuid from task7_normalized_error_begin))
  and (select payload->>'error_code' = 'generic_node_error'
       from public.transactional_outbox
       where workflow_run_id = (select (result->>'id')::uuid
                                from task7_normalized_error_start)
         and event_type = 'workflow.step.failed'),
  'generic worker errors normalize before canonical attempt and Temporal outbox persistence'
);

select * from finish();
rollback;
