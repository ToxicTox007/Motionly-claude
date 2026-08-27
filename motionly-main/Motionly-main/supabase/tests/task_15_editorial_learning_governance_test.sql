begin;
select plan(30);

select has_table('public', 'editorial_learning_policies', 'editorial policy ledger exists');
select has_table('public', 'editorial_feedback_deltas', 'normalized feedback ledger exists');
select has_table('public', 'editorial_training_datasets', 'frozen dataset ledger exists');
select has_table('public', 'editorial_training_dataset_items', 'frozen dataset items exist');
select has_table('public', 'editorial_learning_deployments', 'per-user deployment ledger exists');
select ok((select relrowsecurity from pg_class where oid = 'public.editorial_learning_policies'::regclass), 'policy RLS is enabled');
select ok((select relrowsecurity from pg_class where oid = 'public.editorial_feedback_deltas'::regclass), 'delta RLS is enabled');
select ok((select relrowsecurity from pg_class where oid = 'public.editorial_training_datasets'::regclass), 'dataset RLS is enabled');
select ok((select relrowsecurity from pg_class where oid = 'public.editorial_learning_deployments'::regclass), 'deployment RLS is enabled');
select has_function('public', 'motionly_set_editorial_learning_policy', array['uuid','uuid','boolean','boolean','text','integer','integer'], 'policy compare-and-swap RPC exists');
select has_function('public', 'motionly_capture_editorial_feedback_delta', array['uuid','uuid','uuid','uuid','text','jsonb','text','text','boolean','boolean'], 'idempotent delta capture RPC exists');
select has_function('public', 'motionly_freeze_editorial_training_dataset', array['uuid','uuid','uuid','integer'], 'frozen dataset RPC exists');
select has_function('public', 'motionly_promote_editorial_learning_dataset', array['uuid','uuid','uuid','uuid','integer'], 'promotion RPC exists');
select has_function('public', 'motionly_rollback_editorial_learning_deployment', array['uuid','uuid','uuid','text'], 'rollback RPC exists');
select ok(
  not has_function_privilege('anon', 'public.motionly_set_editorial_learning_policy(uuid,uuid,boolean,boolean,text,integer,integer)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'public.motionly_capture_editorial_feedback_delta(uuid,uuid,uuid,uuid,text,jsonb,text,text,boolean,boolean)', 'EXECUTE')
  and has_function_privilege('service_role', 'public.motionly_freeze_editorial_training_dataset(uuid,uuid,uuid,integer)', 'EXECUTE'),
  'editorial mutation RPCs are service-role-only'
);

insert into auth.users (id, email, raw_user_meta_data) values
  ('90e00000-0000-4000-8000-000000000001', 'editorial-governance@test.local', '{}');
insert into public.workspaces (id, name, owner_user_id) values
  ('91e00000-0000-4000-8000-000000000001', 'Editorial governance', '90e00000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status) values
  ('91e00000-0000-4000-8000-000000000001', '90e00000-0000-4000-8000-000000000001', 'owner', 'active');
set local role service_role;
insert into public.projects (id, workspace_id, created_by, title) values
  ('92e00000-0000-4000-8000-000000000001', '91e00000-0000-4000-8000-000000000001', '90e00000-0000-4000-8000-000000000001', 'Editorial governance');

select is(
  (select count(*)::integer from public.editorial_learning_policies where workspace_id = '91e00000-0000-4000-8000-000000000001'),
  0,
  'a user has no learning policy until explicit consent is recorded'
);
create temporary table editorial_policy as
select * from public.motionly_set_editorial_learning_policy(
  '91e00000-0000-4000-8000-000000000001',
  '90e00000-0000-4000-8000-000000000001',
  true, true, 'editorial-consent/v1', 30, 0
);
select is((select count(*)::integer from editorial_policy), 1, 'explicit policy consent is persisted');
select is((select revision from editorial_policy), 1, 'first policy write begins at revision one');
select throws_ok(
  $$select * from public.motionly_set_editorial_learning_policy(
    '91e00000-0000-4000-8000-000000000001',
    '90e00000-0000-4000-8000-000000000001',
    true, false, 'editorial-consent/v2', 30, 0
  )$$,
  '40001', 'EDITORIAL_POLICY_STALE', 'stale consent compare-and-swap fails closed'
);
create temporary table editorial_delta as
select * from public.motionly_capture_editorial_feedback_delta(
  '91e00000-0000-4000-8000-000000000001',
  '92e00000-0000-4000-8000-000000000001',
  '90e00000-0000-4000-8000-000000000001',
  '93e00000-0000-4000-8000-000000000001',
  'cuts', '{"layer":"cuts","after":{"pace":"fast"}}'::jsonb,
  'editorial-delta/v1', repeat('a', 64), true, true
);
select is((select count(*)::integer from editorial_delta), 1, 'consented normalized delta is captured');
select is(
  (select id from public.motionly_capture_editorial_feedback_delta(
    '91e00000-0000-4000-8000-000000000001',
    '92e00000-0000-4000-8000-000000000001',
    '90e00000-0000-4000-8000-000000000001',
    '93e00000-0000-4000-8000-000000000001',
    'cuts', '{"layer":"cuts","after":{"pace":"fast"}}'::jsonb,
    'editorial-delta/v1', repeat('a', 64), true, true
  )),
  (select id from editorial_delta),
  'source-event delta capture is idempotent'
);
select is((select count(*)::integer from public.editorial_feedback_deltas), 1, 'idempotent replay adds no duplicate delta');
create temporary table editorial_dataset as
select * from public.motionly_freeze_editorial_training_dataset(
  '91e00000-0000-4000-8000-000000000001',
  '92e00000-0000-4000-8000-000000000001',
  '90e00000-0000-4000-8000-000000000001',
  1
);
select is((select count(*)::integer from editorial_dataset), 1, 'consented delta freezes into a dataset');
select is((select count(*)::integer from public.editorial_training_dataset_items), 1, 'frozen dataset stores its immutable item manifest');
create temporary table editorial_deployment as
select * from public.motionly_promote_editorial_learning_dataset(
  '91e00000-0000-4000-8000-000000000001',
  '92e00000-0000-4000-8000-000000000001',
  '90e00000-0000-4000-8000-000000000001',
  (select id from editorial_dataset), 0
);
select is((select state from editorial_deployment), 'active', 'consented frozen dataset promotes atomically');
select is((select revision from editorial_deployment), 1, 'promotion increments deployment revision');
select public.motionly_revoke_editorial_learning(
  '91e00000-0000-4000-8000-000000000001',
  '90e00000-0000-4000-8000-000000000001',
  'all', 'user_request'
);
select ok(
  (select not memory_enabled and not training_enabled and revoked_at is not null from public.editorial_learning_policies),
  'revocation disables both consent scopes and timestamps policy revocation'
);
select ok(
  (select not memory_eligible and not training_eligible and revoked_at is not null from public.editorial_feedback_deltas),
  'revocation makes captured deltas permanently ineligible'
);
select ok(
  (select status = 'revoked' and revoked_at is not null from public.editorial_training_datasets),
  'revocation invalidates frozen datasets'
);
select ok(
  (select active_dataset_id is null and state = 'disabled' from public.editorial_learning_deployments),
  'revocation removes deployment from serving'
);

select * from finish();
rollback;
