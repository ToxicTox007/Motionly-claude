-- Worker audit WJ-05: a project-delete cancellation is a lease, not a one-shot
-- hand-out. A failed Temporal call makes it claimable again; only an explicit
-- completion retires it; a request that keeps failing is dead-lettered.
begin;
select plan(10);

select has_function('public', 'motionly_complete_workflow_cancellation', array['uuid'],
  'cancellation completion RPC exists');
select has_function('public', 'motionly_release_workflow_cancellation',
  array['uuid', 'text', 'integer'], 'cancellation release RPC exists');

set local role service_role;
-- The table deliberately has no foreign keys (it must survive the cascade).
insert into public.workflow_cancellation_requests (
  id, workspace_id, project_id, workflow_run_id, temporal_workflow_id, reason
) values
  ('c5000000-0000-4000-8000-000000000001', gen_random_uuid(), gen_random_uuid(),
   gen_random_uuid(), 'motionly-first-draft-wj05-a', 'project_deleted'),
  ('c5000000-0000-4000-8000-000000000002', gen_random_uuid(), gen_random_uuid(),
   gen_random_uuid(), 'motionly-first-draft-wj05-b', 'project_deleted');

select is((select count(*)::integer from public.motionly_claim_workflow_cancellations(50)), 2,
  'open requests are claimed');
select is((select count(*)::integer from public.motionly_claim_workflow_cancellations(50)), 0,
  'leased requests are not handed out twice');

-- Temporal failed for request A: it is released with a backoff ...
select ok(public.motionly_release_workflow_cancellation(
  'c5000000-0000-4000-8000-000000000001', 'temporal_rpc_unavailable', 30),
  'a failed cancellation is released for retry');
-- ... and becomes claimable again once the backoff passes.
update public.workflow_cancellation_requests
set lease_expires_at = now() - interval '1 second'
where id in ('c5000000-0000-4000-8000-000000000001', 'c5000000-0000-4000-8000-000000000002');
select ok(public.motionly_complete_workflow_cancellation('c5000000-0000-4000-8000-000000000002'),
  'Temporal confirmed request B');

create temporary table wj05_retry as
select * from public.motionly_claim_workflow_cancellations(50);
select is((select array_agg(id::text) from wj05_retry), array['c5000000-0000-4000-8000-000000000001'],
  'the failed request is retried; the completed one is never handed out again');
select is((select attempt_count from wj05_retry), 2, 'retries are counted');

-- A request that keeps failing stops being retried and is kept for operators.
update public.workflow_cancellation_requests
set attempt_count = 20, lease_expires_at = now() - interval '1 second'
where id = 'c5000000-0000-4000-8000-000000000001';
select is((select count(*)::integer from public.motionly_claim_workflow_cancellations(50)), 0,
  'an exhausted request is no longer claimed');
select ok(
  (select dead_lettered_at is not null and last_error_code = 'temporal_rpc_unavailable'
   from public.workflow_cancellation_requests where id = 'c5000000-0000-4000-8000-000000000001'),
  'the exhausted request is dead-lettered with its last error');

select * from finish();
rollback;
