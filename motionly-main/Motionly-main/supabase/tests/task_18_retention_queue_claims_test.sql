begin;
select plan(13);

select has_column('public', 'storage_deletion_queue', 'lease_owner',
  'retention queue records the claimant owner');
select has_column('public', 'storage_deletion_queue', 'lease_token',
  'retention queue records a fencing token');
select has_column('public', 'storage_deletion_queue', 'lease_expires_at',
  'retention queue records bounded lease expiry');
select has_function('public', 'motionly_claim_storage_deletion_queue',
  array['text','integer','text','timestamp with time zone','integer'],
  'atomic retention claim RPC exists');
select has_function('public', 'motionly_ack_storage_deletion_queue', array['uuid','uuid'],
  'fenced retention acknowledgement RPC exists');

set local role service_role;
insert into public.storage_deletion_queue (id, storage_bucket, storage_path, source_table, queued_at)
values
  ('a8000000-0000-4000-8000-000000000001', 'raw-uploads', 'task18-retention/one.mp4', 'task18_retention', now() - interval '2 days'),
  ('a8000000-0000-4000-8000-000000000002', 'raw-uploads', 'task18-retention/two.mp4', 'task18_retention', now() - interval '2 days');

create temporary table task18_retention_claim_a as
select * from public.motionly_claim_storage_deletion_queue(
  'retention-worker-a', 10, 'task18_retention', now(), 300
);
select is((select count(*)::integer from task18_retention_claim_a), 2,
  'first runner atomically claims both eligible entries');
select ok(
  (select count(distinct lease_token) = 2 and bool_and(lease_owner = 'retention-worker-a')
   from task18_retention_claim_a),
  'each claimed row has a fresh owner-bound fencing token'
);
select is(
  (select count(*)::integer from public.motionly_claim_storage_deletion_queue(
    'retention-worker-b', 10, 'task18_retention', now(), 300
  )),
  0,
  'second concurrent runner cannot claim live leases'
);

update public.storage_deletion_queue
set lease_expires_at = clock_timestamp() - interval '1 second'
where id = 'a8000000-0000-4000-8000-000000000001';
create temporary table task18_retention_reclaim as
select * from public.motionly_claim_storage_deletion_queue(
  'retention-worker-b', 1, 'task18_retention', now(), 300
);
select is((select count(*)::integer from task18_retention_reclaim), 1,
  'expired lease is reclaimable after a worker crash');
select ok(
  (select lease_owner = 'retention-worker-b'
          and lease_token <> (select lease_token from task18_retention_claim_a where id = 'a8000000-0000-4000-8000-000000000001')
   from task18_retention_reclaim),
  'reclaim receives a new fencing token'
);
select ok(
  not public.motionly_ack_storage_deletion_queue(
    'a8000000-0000-4000-8000-000000000001',
    (select lease_token from task18_retention_claim_a where id = 'a8000000-0000-4000-8000-000000000001')
  ),
  'stale worker cannot acknowledge a reclaimed entry'
);
select ok(
  public.motionly_ack_storage_deletion_queue(
    'a8000000-0000-4000-8000-000000000001',
    (select lease_token from task18_retention_reclaim)
  ),
  'current lease token acknowledges and removes its entry'
);
select is(
  (select count(*)::integer from public.storage_deletion_queue where source_table = 'task18_retention'),
  1,
  'only the acknowledged entry is removed'
);

select * from finish();
rollback;
