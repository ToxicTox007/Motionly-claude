-- Worker audit WJ-11/WJ-14/WJ-15: bounded storage-deletion retries with a
-- dead-letter state, a single live retention run, and a transcript redaction
-- that keeps derived audio intelligence.
begin;
select plan(16);

select has_column('public', 'storage_deletion_queue', 'attempt_count',
  'storage deletions count claim attempts');
select has_column('public', 'storage_deletion_queue', 'dead_lettered_at',
  'storage deletions have a terminal dead-letter state');
select has_function('public', 'motionly_begin_retention_run', array['integer'],
  'single-run retention admission RPC exists');
select has_function('public', 'motionly_redact_transcript_raw_payloads',
  array['timestamp with time zone', 'integer'],
  'targeted transcript redaction RPC exists');

set local role service_role;

-- WJ-14 ---------------------------------------------------------------------
insert into public.storage_deletion_queue (id, storage_bucket, storage_path, source_table, queued_at)
values
  ('a9000000-0000-4000-8000-000000000001', 'raw-uploads', 'wj14/poison.mp4', 'wj14_retention', now() - interval '3 days'),
  ('a9000000-0000-4000-8000-000000000002', 'raw-uploads', 'wj14/healthy.mp4', 'wj14_retention', now() - interval '2 days');

create temporary table wj14_claim as
select * from public.motionly_claim_storage_deletion_queue('retention-worker-a', 10, 'wj14_retention', now(), 300);
select is((select attempt_count from wj14_claim where id = 'a9000000-0000-4000-8000-000000000001'), 1,
  'a claim counts one attempt');
select ok(
  public.motionly_note_storage_deletion_failure(
    'a9000000-0000-4000-8000-000000000001',
    (select lease_token from wj14_claim where id = 'a9000000-0000-4000-8000-000000000001'),
    'storage_permission_denied'
  ),
  'the current lease holder records why a deletion failed'
);
select ok(
  not public.motionly_note_storage_deletion_failure(
    'a9000000-0000-4000-8000-000000000001', gen_random_uuid(), 'forged'
  ),
  'a stale or forged lease cannot overwrite the failure record'
);

-- Simulate 19 more expired, never-acknowledged leases on the poisoned row.
update public.storage_deletion_queue
set attempt_count = 20,
    lease_expires_at = clock_timestamp() - interval '1 second'
where id = 'a9000000-0000-4000-8000-000000000001';
update public.storage_deletion_queue
set lease_expires_at = clock_timestamp() - interval '1 second'
where id = 'a9000000-0000-4000-8000-000000000002';

create temporary table wj14_reclaim as
select * from public.motionly_claim_storage_deletion_queue('retention-worker-b', 10, 'wj14_retention', now(), 300);
select is(
  (select array_agg(id::text order by id) from wj14_reclaim),
  array['a9000000-0000-4000-8000-000000000002'],
  'an exhausted row is no longer claimed; healthy rows still are'
);
select ok(
  (select dead_lettered_at is not null and lease_token is null
          and last_error_code = 'storage_permission_denied'
   from public.storage_deletion_queue where id = 'a9000000-0000-4000-8000-000000000001'),
  'the exhausted row is dead-lettered with its last error kept for replay'
);

-- WJ-11 ---------------------------------------------------------------------
delete from public.retention_runs;
create temporary table wj11_first as select public.motionly_begin_retention_run(21600) as run_id;
select isnt((select run_id from wj11_first), null, 'the first run is admitted');
select is(public.motionly_begin_retention_run(21600), null,
  'a second concurrent run is refused while the first is live');
update public.retention_runs
set started_at = now() - interval '7 hours'
where id = (select run_id from wj11_first);
select isnt(public.motionly_begin_retention_run(21600), null,
  'a run abandoned past the stale window no longer blocks new runs');
select is(
  (select status from public.retention_runs where id = (select run_id from wj11_first)),
  'failed',
  'the abandoned run is closed as failed'
);

-- WJ-15 ---------------------------------------------------------------------
set local role postgres;
insert into auth.users (id, email, raw_user_meta_data) values
  ('19000000-0000-4000-8000-000000000001', 'wj15@test.local', '{}');
insert into public.workspaces (id, name, owner_user_id) values
  ('39000000-0000-4000-8000-000000000001', 'WJ15', '19000000-0000-4000-8000-000000000001');
insert into public.projects (id, workspace_id, created_by, title) values
  ('49000000-0000-4000-8000-000000000001', '39000000-0000-4000-8000-000000000001',
   '19000000-0000-4000-8000-000000000001', 'WJ15');
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
  mime_type, byte_size, file_size_bytes, status
) values (
  '59000000-0000-4000-8000-000000000001', '39000000-0000-4000-8000-000000000001',
  '49000000-0000-4000-8000-000000000001', 'raw-uploads', 'wj15/source.mp4',
  'source.mp4', 'video/mp4', 2048, 2048, 'processing'
);
insert into public.transcripts (
  id, workspace_id, project_id, uploaded_video_id, status, language, provider, model,
  raw_payload, created_at
) values
  ('69000000-0000-4000-8000-000000000001', '39000000-0000-4000-8000-000000000001',
   '49000000-0000-4000-8000-000000000001', '59000000-0000-4000-8000-000000000001',
   'ready', 'en', 'fixture', 'fixture',
   '{"provider_response": {"secret": "x"}, "segments": [1], "audio_intelligence": {"keywords": ["launch"]}}'::jsonb,
   now() - interval '40 days'),
  ('69000000-0000-4000-8000-000000000002', '39000000-0000-4000-8000-000000000001',
   '49000000-0000-4000-8000-000000000001', '59000000-0000-4000-8000-000000000001',
   'ready', 'en', 'fixture', 'fixture',
   '{"provider_response": {"secret": "y"}}'::jsonb,
   now() - interval '2 days');
set local role service_role;

select is(public.motionly_redact_transcript_raw_payloads(now() - interval '30 days', 500), 1,
  'only transcripts past the cutoff with provider keys are redacted');
select is(
  (select raw_payload from public.transcripts where id = '69000000-0000-4000-8000-000000000001'),
  '{"audio_intelligence": {"keywords": ["launch"]}}'::jsonb,
  'provider keys are removed and derived audio intelligence is kept'
);
select is(public.motionly_redact_transcript_raw_payloads(now() - interval '30 days', 500), 0,
  'an already-redacted transcript is not rewritten again');

select * from finish();
rollback;
