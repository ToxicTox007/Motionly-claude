begin;
select plan(11);

-- Task 4: social publishing and performance insights were removed from the
-- product; their tables and functions are dropped by a forward migration.
select hasnt_table('public', 'publish_jobs', 'social publish jobs are dropped');
select hasnt_table('public', 'publish_connections', 'social account connections are dropped');
select hasnt_table('public', 'performance_feedback_metrics', 'performance metrics are dropped');
select hasnt_table('public', 'performance_feedback_policies', 'performance policies are dropped');
select hasnt_table('public', 'performance_feedback_policy_events', 'performance policy events are dropped');
select is(
  (select count(*)::integer from pg_proc p
     join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname in (
        'motionly_capture_performance_metric',
        'motionly_revoke_performance_feedback',
        'motionly_set_performance_feedback_policy'
      )),
  0,
  'performance feedback functions are dropped'
);
select is(
  (select count(*)::integer from public.tenant_integrity_constraint_manifest
    where table_name in ('publish_jobs', 'publish_connections')),
  0,
  'the tenant-integrity manifest no longer lists constraints of dropped tables'
);
select is(
  (select count(*)::integer from public.tenant_integrity_constraint_manifest manifest
    where not exists (select 1 from pg_constraint c where c.conname = manifest.constraint_name)),
  0,
  'every manifest constraint still exists, so tenant-integrity validation can run'
);
select lives_ok(
  $$select public.motionly_audit_task3_tenant_integrity()$$,
  'the tenant-integrity audit still runs'
);

-- The export feature that prepares translated captions is kept.
select has_table('public', 'localized_publish_packages', 'localized publish packages are kept');
select has_table('public', 'exports', 'exports are kept');

select * from finish();
rollback;
