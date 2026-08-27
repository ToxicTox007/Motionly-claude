begin;
select plan(24);

select has_table('public', 'ai_edit_proposals', 'durable proposals exist');
select has_table('public', 'ai_edit_proposal_actions', 'typed proposal actions exist');
select has_table('public', 'ai_edit_proposal_decisions', 'immutable decisions exist');

select has_column('public', 'ai_edit_proposals', 'context_digest', 'canonical context digest is stored');
select has_column('public', 'ai_edit_proposals', 'context_snapshot', 'versioned context snapshot is stored');
select has_column('public', 'ai_edit_proposals', 'base_active_plan_revision', 'base plan revision is stored');
select has_column('public', 'ai_edit_proposals', 'media_revision', 'media revision is stored');
select has_column('public', 'ai_edit_proposals', 'model_revision', 'model revision provenance is stored');
select has_column('public', 'ai_edit_proposals', 'gateway_request_id', 'gateway request provenance is stored');
select has_column('public', 'ai_edit_proposals', 'actual_cost_usd', 'settled cost is stored');
select has_column('public', 'ai_edit_proposal_actions', 'dependency_ids', 'action dependencies are stored');
select has_column('public', 'ai_edit_proposal_actions', 'dry_run_hash', 'deterministic action hash is stored');
select has_column('public', 'ai_edit_proposal_decisions', 'idempotency_key', 'decision idempotency is stored');
select has_column('public', 'timeline_revisions', 'proposal_id', 'timeline links proposal');
select has_column('public', 'timeline_revisions', 'decision_id', 'timeline links decision');
select has_column('public', 'timeline_revisions', 'result_hash', 'timeline links deterministic result');

select has_function(
  'public', 'motionly_create_ai_edit_proposal',
  array['uuid','uuid','text','text','jsonb','uuid','integer','uuid','uuid','integer','uuid','integer','text','text','uuid'],
  'queued proposal creation RPC exists'
);
select has_function(
  'public', 'motionly_transition_ai_edit_proposal',
  array['uuid','uuid','uuid','text','jsonb'],
  'worker proposal transition RPC exists'
);
select has_function(
  'public', 'motionly_decide_ai_edit_proposal',
  array['uuid','uuid','uuid','text','integer','uuid','integer','text','text[]','uuid'],
  'atomic proposal decision RPC exists'
);

select ok(
  (select relrowsecurity from pg_class where oid = 'public.ai_edit_proposals'::regclass),
  'proposal RLS is enabled'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.ai_edit_proposal_actions'::regclass),
  'action RLS is enabled'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.ai_edit_proposal_decisions'::regclass),
  'decision RLS is enabled'
);

select ok(
  not has_function_privilege('authenticated', 'public.motionly_decide_ai_edit_proposal(uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid)', 'EXECUTE')
  and not has_function_privilege('anon', 'public.motionly_decide_ai_edit_proposal(uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid)', 'EXECUTE')
  and has_function_privilege('service_role', 'public.motionly_decide_ai_edit_proposal(uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid)', 'EXECUTE'),
  'decision RPC is service-role-only'
);

select ok(
  not has_function_privilege('authenticated', 'public.motionly_update_plan_with_revision(uuid,integer,jsonb)', 'EXECUTE')
  and not has_function_privilege('anon', 'public.motionly_update_plan_with_revision(uuid,integer,jsonb)', 'EXECUTE')
  and has_function_privilege('service_role', 'public.motionly_update_plan_with_revision(uuid,integer,jsonb)', 'EXECUTE'),
  'legacy revision RPC privilege is forward-fixed'
);

select * from finish();
rollback;
