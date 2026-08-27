begin;
select plan(13);

select has_table('public', 'tenant_integrity_findings', 'tenant-integrity finding ledger exists');
select has_function('public', 'motionly_audit_task3_tenant_integrity', array[]::text[], 'Task 3 audit RPC exists');
select has_function('public', 'motionly_validate_task3_tenant_integrity', array['text'], 'guarded Task 3 validation RPC exists');
select ok(
  not has_function_privilege('anon', 'public.motionly_audit_task3_tenant_integrity()', 'EXECUTE')
  and has_function_privilege('service_role', 'public.motionly_audit_task3_tenant_integrity()', 'EXECUTE'),
  'tenant-integrity audit remains service-role-only'
);

-- Model a deployed pre-Task-3 relationship: the inconsistent row is created
-- before adding the exact composite relationship as NOT VALID. This avoids
-- privileged trigger manipulation while preserving PostgreSQL legacy-row semantics.
create table public.tenant_integrity_audit_parent_fixture (
  id uuid primary key,
  workspace_id uuid not null,
  project_id uuid not null,
  unique (workspace_id, project_id, id)
);
create table public.tenant_integrity_audit_child_fixture (
  id uuid primary key,
  workspace_id uuid not null,
  project_id uuid not null,
  parent_id uuid not null
);
insert into public.tenant_integrity_audit_parent_fixture (id, workspace_id, project_id) values
  ('e4000000-0000-4000-8000-000000000001', 'e2000000-0000-4000-8000-000000000002', 'e3000000-0000-4000-8000-000000000002');
insert into public.tenant_integrity_audit_child_fixture (id, workspace_id, project_id, parent_id) values
  ('e5000000-0000-4000-8000-000000000001', 'e2000000-0000-4000-8000-000000000001', 'e3000000-0000-4000-8000-000000000001', 'e4000000-0000-4000-8000-000000000001');
alter table public.tenant_integrity_audit_child_fixture
  add constraint task3_audit_fixture_parent_tenant_fk
  foreign key (workspace_id, project_id, parent_id)
  references public.tenant_integrity_audit_parent_fixture (workspace_id, project_id, id)
  not valid;
insert into public.tenant_integrity_constraint_manifest (
  constraint_name, table_name, constraint_kind, validation_order
) values (
  'task3_audit_fixture_parent_tenant_fk', 'tenant_integrity_audit_child_fixture', 'foreign_key', 999
);
grant select on public.tenant_integrity_audit_child_fixture to service_role;

set local role service_role;
create temporary table task3_audit_run as
  select public.motionly_audit_task3_tenant_integrity() as id;

select is(
  (select count(*)::integer from public.tenant_integrity_findings
   where audit_run_id = (select id from task3_audit_run)
     and constraint_name = 'task3_audit_fixture_parent_tenant_fk'
     and state = 'open'),
  1,
  'audit finds a legacy cross-tenant composite relationship'
);
select is(
  (select count(*)::integer from public.tenant_integrity_audit_child_fixture
   where id = 'e5000000-0000-4000-8000-000000000001'),
  1,
  'audit does not delete a historical tenant-integrity row'
);
select throws_ok(
  $$select public.motionly_assert_task3_tenant_integrity_clean((select id from task3_audit_run))$$,
  'P0001',
  'TASK3_TENANT_INTEGRITY_FINDINGS_OPEN: 1',
  'validation precondition fails closed while a finding is open'
);
select lives_ok(
  $$select public.motionly_record_tenant_integrity_remediation(
    (select id from public.tenant_integrity_findings where constraint_name = 'task3_audit_fixture_parent_tenant_fk'),
    'repair_planned',
    'workspace and project are corrected to the referenced parent after operator review'
  )$$,
  'operator-reviewed remediation is recorded without mutating the row'
);
select is(
  (select count(*)::integer from public.tenant_integrity_remediation_events),
  1,
  'remediation audit event is retained'
);

reset role;
update public.tenant_integrity_audit_child_fixture
set workspace_id = 'e2000000-0000-4000-8000-000000000002',
    project_id = 'e3000000-0000-4000-8000-000000000002'
where id = 'e5000000-0000-4000-8000-000000000001';
set local role service_role;
create temporary table task3_clean_audit_run as
  select public.motionly_audit_task3_tenant_integrity() as id;

select lives_ok(
  $$select public.motionly_assert_task3_tenant_integrity_clean((select id from task3_clean_audit_run))$$,
  'clean historical data satisfies the validation precondition'
);
select is(
  (select state from public.tenant_integrity_findings
   where constraint_name = 'task3_audit_fixture_parent_tenant_fk'
     and child_id = 'e5000000-0000-4000-8000-000000000001'),
  'resolved',
  'a clean rescan resolves the prior finding without deleting its audit history'
);
select lives_ok(
  $$select * from public.motionly_validate_task3_tenant_integrity('validate-task3-tenant-integrity')$$,
  'explicitly acknowledged validation succeeds only after a fresh clean audit'
);
select is(
  (
    select count(*)::integer
    from public.tenant_integrity_constraint_manifest manifest
    join pg_constraint catalog_constraint
      on catalog_constraint.conname = manifest.constraint_name
     and catalog_constraint.conrelid = format('public.%I', manifest.table_name)::regclass
    where not catalog_constraint.convalidated
  ),
  0,
  'all reviewed Task 3 foreign-key and check constraints validate after cleanup'
);

select * from finish();
rollback;
