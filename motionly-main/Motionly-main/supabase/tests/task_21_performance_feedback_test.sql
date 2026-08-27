begin;
select plan(9);

select has_table('public', 'performance_feedback_policies', 'performance feedback policy ledger exists');
select has_table('public', 'performance_feedback_policy_events', 'performance feedback event ledger exists');
select has_table('public', 'performance_feedback_metrics', 'aggregate performance metrics ledger exists');
select ok(
  to_regprocedure('public.motionly_set_performance_feedback_policy(uuid,uuid,boolean,boolean,text,integer,integer)') is not null,
  'policy compare-and-swap function exists'
);
select ok(
  to_regprocedure('public.motionly_capture_performance_metric(uuid,uuid,uuid,uuid,text,timestamp with time zone,bigint,double precision,double precision,double precision,text,text,text,double precision,text,boolean)') is not null,
  'consent-gated metric capture function exists'
);
select ok(
  to_regprocedure('public.motionly_revoke_performance_feedback(uuid,uuid,text)') is not null,
  'revocation and purge function exists'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.performance_feedback_policies'::regclass)
  and (select relrowsecurity from pg_class where oid = 'public.performance_feedback_policy_events'::regclass)
  and (select relrowsecurity from pg_class where oid = 'public.performance_feedback_metrics'::regclass),
  'performance feedback tables enforce RLS'
);
select ok(
  not has_function_privilege('authenticated', 'public.motionly_set_performance_feedback_policy(uuid,uuid,boolean,boolean,text,integer,integer)', 'EXECUTE')
    and not has_function_privilege('anon', 'public.motionly_revoke_performance_feedback(uuid,uuid,text)', 'EXECUTE'),
  'browser roles cannot mutate feedback consent or retention'
);
select ok(
  has_function_privilege('service_role', 'public.motionly_capture_performance_metric(uuid,uuid,uuid,uuid,text,timestamp with time zone,bigint,double precision,double precision,double precision,text,text,text,double precision,text,boolean)', 'EXECUTE')
    and has_function_privilege('service_role', 'public.motionly_revoke_performance_feedback(uuid,uuid,text)', 'EXECUTE'),
  'service role retains the audited capture and purge path'
);

select * from finish();
rollback;
