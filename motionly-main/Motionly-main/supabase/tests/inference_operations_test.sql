-- Task 16A pgTAP: the inference_operations table is the durable, route-affine
-- record for async inference. It verifies the shape (table and key columns),
-- the capability and state CHECKs, per-workspace idempotency uniqueness,
-- per-route provider_operation_id uniqueness, the route_snapshot secret-value
-- guard, RLS, and the API-authoritative grant posture (service_role full; anon
-- and authenticated none; the worker deferred to the gateway-wiring wave).
--
-- Forward-only guarantee for 202610240101_inference_operations.sql.
begin;
select plan(33);

-- Shape -----------------------------------------------------------------------
select has_table('public', 'inference_operations', 'inference_operations exists');
select col_is_pk('public', 'inference_operations', 'id', 'id is the primary key');
select has_column('public', 'inference_operations', 'workspace_id',
  'workspace_id exists');
select col_not_null('public', 'inference_operations', 'workspace_id',
  'workspace_id is NOT NULL (every async caller carries a workspace)');
select col_is_null('public', 'inference_operations', 'project_id',
  'project_id is nullable (an operation may be workspace-scoped only)');
select has_column('public', 'inference_operations', 'capability', 'capability exists');
select has_column('public', 'inference_operations', 'idempotency_key',
  'idempotency_key exists');
select has_column('public', 'inference_operations', 'provider_route_id',
  'provider_route_id exists');
select has_column('public', 'inference_operations', 'provider_model',
  'provider_model exists');
select has_column('public', 'inference_operations', 'provider_model_revision',
  'provider_model_revision exists');
select has_column('public', 'inference_operations', 'route_snapshot',
  'route_snapshot exists');
select has_column('public', 'inference_operations', 'provider_operation_id',
  'provider_operation_id exists');
select has_column('public', 'inference_operations', 'state', 'state exists');
select col_default_is('public', 'inference_operations', 'cost_unknown', 'false',
  'cost_unknown defaults to false');

-- Tenant fixtures -------------------------------------------------------------
insert into auth.users (id, email, raw_user_meta_data) values
  ('16a00000-0000-4000-8000-000000000001', 'ops-a@16a.test', '{}'::jsonb),
  ('16a00000-0000-4000-8000-000000000002', 'ops-b@16a.test', '{}'::jsonb);
insert into public.workspaces (id, name, owner_user_id) values
  ('16a00000-0000-4000-8000-0000000000a1', 'Ops workspace A',
   '16a00000-0000-4000-8000-000000000001'),
  ('16a00000-0000-4000-8000-0000000000a2', 'Ops workspace B',
   '16a00000-0000-4000-8000-000000000002');

set local role service_role;

-- A valid reservation inserts. ------------------------------------------------
select lives_ok(
  $$insert into public.inference_operations
      (id, workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot, state)
    values
      ('16a00000-0000-4000-8000-0000000000f1',
       '16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-1',
       'route-1', 'model-x', 'rev-1',
       '{"adapter_id":"endpoint-1","adapter_type":"async_job","base_url":"https://example.invalid","model":"model-x","model_revision":"rev-1","mapping_version":"1","secret_ref_name":"bundle:endpoint-1"}'::jsonb,
       'reserved')$$,
  'a valid reservation inserts'
);

-- Capability CHECK ------------------------------------------------------------
select throws_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot)
    values
      ('16a00000-0000-4000-8000-0000000000a1', 'not.a.capability', 'idem-bad-cap',
       'route-1', 'model-x', 'rev-1', '{}'::jsonb)$$,
  '23514', null,
  'an unknown capability is rejected'
);
select lives_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot)
    values
      ('16a00000-0000-4000-8000-0000000000a1', 'video.generation.submit', 'idem-cap-ok',
       'route-1', 'model-x', 'rev-1', '{}'::jsonb)$$,
  'a Section 2.1 capability id is accepted'
);

-- State CHECK -----------------------------------------------------------------
select throws_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot, state)
    values
      ('16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-bad-state',
       'route-1', 'model-x', 'rev-1', '{}'::jsonb, 'teleported')$$,
  '23514', null,
  'an unknown state is rejected'
);
select lives_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot, state)
    values
      ('16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-state-ok',
       'route-1', 'model-x', 'rev-1', '{}'::jsonb, 'submit_unknown')$$,
  'submit_unknown is a valid state'
);

-- route_snapshot secret-value guard -------------------------------------------
select throws_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot)
    values
      ('16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-secret',
       'route-1', 'model-x', 'rev-1', '{"api_key":"sk-live-abc"}'::jsonb)$$,
  '23514', null,
  'a route_snapshot carrying a secret-value key is rejected'
);
select throws_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot)
    values
      ('16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-secret-2',
       'route-1', 'model-x', 'rev-1', '{"token":"t"}'::jsonb)$$,
  '23514', null,
  'a route_snapshot carrying a token value is rejected'
);

-- error_detail length bound ---------------------------------------------------
select throws_ok(
  format(
    $$insert into public.inference_operations
        (workspace_id, capability, idempotency_key, provider_route_id,
         provider_model, provider_model_revision, route_snapshot, error_detail)
      values
        ('16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-long',
         'route-1', 'model-x', 'rev-1', '{}'::jsonb, %L)$$,
    repeat('x', 2001)
  ),
  '23514', null,
  'an over-long error_detail is rejected'
);

-- Idempotency uniqueness per workspace ----------------------------------------
select throws_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot)
    values
      ('16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-1',
       'route-1', 'model-x', 'rev-1', '{}'::jsonb)$$,
  '23505', null,
  'the same idempotency key in the same workspace is rejected'
);
select lives_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot)
    values
      ('16a00000-0000-4000-8000-0000000000a2', 'audio.diarization', 'idem-1',
       'route-1', 'model-x', 'rev-1', '{}'::jsonb)$$,
  'the same idempotency key in a different workspace is accepted'
);

-- provider_operation_id uniqueness per route ----------------------------------
update public.inference_operations
  set provider_operation_id = 'ext-op-1'
  where id = '16a00000-0000-4000-8000-0000000000f1';
select throws_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot, provider_operation_id)
    values
      ('16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-dup-ext',
       'route-1', 'model-x', 'rev-1', '{}'::jsonb, 'ext-op-1')$$,
  '23505', null,
  'the same provider_operation_id on the same route is rejected'
);
select lives_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot, provider_operation_id)
    values
      ('16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-other-route',
       'route-2', 'model-x', 'rev-1', '{}'::jsonb, 'ext-op-1')$$,
  'the same provider_operation_id on a different route is accepted'
);
select lives_ok(
  $$insert into public.inference_operations
      (workspace_id, capability, idempotency_key, provider_route_id,
       provider_model, provider_model_revision, route_snapshot, provider_operation_id)
    values
      ('16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-null-ext-1',
       'route-1', 'model-x', 'rev-1', '{}'::jsonb, null),
      ('16a00000-0000-4000-8000-0000000000a1', 'audio.diarization', 'idem-null-ext-2',
       'route-1', 'model-x', 'rev-1', '{}'::jsonb, null)$$,
  'a null provider_operation_id does not collide on a route'
);

reset role;

-- Security posture ------------------------------------------------------------
select ok(
  (select relrowsecurity from pg_class where oid = 'public.inference_operations'::regclass),
  'row level security is enabled'
);
select ok(
  not has_table_privilege('anon', 'public.inference_operations', 'SELECT')
  and not has_table_privilege('anon', 'public.inference_operations', 'INSERT')
  and not has_table_privilege('anon', 'public.inference_operations', 'UPDATE')
  and not has_table_privilege('anon', 'public.inference_operations', 'DELETE'),
  'anon has no access'
);
select ok(
  not has_table_privilege('authenticated', 'public.inference_operations', 'SELECT')
  and not has_table_privilege('authenticated', 'public.inference_operations', 'INSERT')
  and not has_table_privilege('authenticated', 'public.inference_operations', 'UPDATE')
  and not has_table_privilege('authenticated', 'public.inference_operations', 'DELETE'),
  'authenticated has no access'
);
select ok(
  has_table_privilege('service_role', 'public.inference_operations', 'SELECT')
  and has_table_privilege('service_role', 'public.inference_operations', 'INSERT')
  and has_table_privilege('service_role', 'public.inference_operations', 'UPDATE')
  and has_table_privilege('service_role', 'public.inference_operations', 'DELETE'),
  'service_role has full access'
);
-- The worker reserves, submits and reconciles operations: SELECT/INSERT/UPDATE
-- and never DELETE. A worker row policy is required because the worker does not
-- bypass RLS; task_18_worker_row_access_test.sql asserts the biconditional
-- (granted RLS tables carry exactly one worker policy).
select ok(
  has_table_privilege('motionly_worker', 'public.inference_operations', 'SELECT')
  and has_table_privilege('motionly_worker', 'public.inference_operations', 'INSERT')
  and has_table_privilege('motionly_worker', 'public.inference_operations', 'UPDATE')
  and not has_table_privilege('motionly_worker', 'public.inference_operations', 'DELETE'),
  'the worker has SELECT/INSERT/UPDATE and never DELETE'
);
select is(
  (select count(*)::int from pg_policy p
   where p.polrelid = 'public.inference_operations'::regclass
     and p.polroles @> array['motionly_worker'::regrole::oid]),
  1,
  'the worker has exactly one row policy'
);

select * from finish();
rollback;
