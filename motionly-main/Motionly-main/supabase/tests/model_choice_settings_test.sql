-- pgTAP coverage for the Task 30 (M6) model choice settings migration
-- (supabase/migrations/202610250201_model_choice_settings.sql).
--
-- Postgres cannot run in the implementation sandbox, so this contract is
-- validated statically by apps/api/tests/test_migration_contract_coverage.py
-- (the required tokens must appear in both files) and executed in CI where a
-- database is available. It asserts both tables exist with their keys, the
-- capability check covers the 14 ids, RLS is on, the service role manages both
-- tables, the worker reads both but never writes, and a worker row policy lets
-- the worker read a row the API created.
begin;
select plan(13);

-- Tables exist.
select has_table('public', 'workspace_model_settings', 'workspace_model_settings exists');
select has_table('public', 'project_model_settings', 'project_model_settings exists');

-- Composite primary keys per Section 2.11 (workspace+capability, project+capability).
select col_is_pk(
  'public', 'workspace_model_settings', array['workspace_id', 'capability'],
  'workspace_model_settings is keyed by (workspace_id, capability)'
);
select col_is_pk(
  'public', 'project_model_settings', array['project_id', 'capability'],
  'project_model_settings is keyed by (project_id, capability)'
);

-- The allow-list and lock columns exist on the workspace settings.
select has_column(
  'public', 'workspace_model_settings', 'allowed_option_ids',
  'workspace_model_settings has allowed_option_ids'
);
select has_column(
  'public', 'workspace_model_settings', 'locked',
  'workspace_model_settings has locked'
);

-- The project override references projects on the composite tenant key.
select col_is_fk(
  'public', 'project_model_settings', array['workspace_id', 'project_id'],
  'project_model_settings has a composite tenant foreign key to projects'
);

-- RLS is enabled on both tables.
select is(
  (
    select coalesce(bool_and(c.relrowsecurity), false)
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relname in ('workspace_model_settings', 'project_model_settings')
  ),
  true,
  'row level security is enabled on both model choice settings tables'
);

-- The capability check rejects an unknown id and accepts a Section 2.1 id.
insert into auth.users (id, email, raw_user_meta_data) values
  ('3e000000-0000-4000-8000-0000000000bb', 'model-choice@test.local', '{}');
insert into public.workspaces (id, owner_user_id, name)
  values ('3e000000-0000-4000-8000-0000000000cc',
          '3e000000-0000-4000-8000-0000000000bb', 'Model choice ws');
select throws_ok(
  $$insert into public.workspace_model_settings (workspace_id, capability, default_option_id)
    values ('3e000000-0000-4000-8000-0000000000cc', 'not.a.capability', 'route-x')$$,
  '23514',
  null,
  'the capability check rejects an unknown capability id'
);
select lives_ok(
  $$insert into public.workspace_model_settings (workspace_id, capability, default_option_id, locked)
    values ('3e000000-0000-4000-8000-0000000000cc', 'planning.chat', 'route-high', true)$$,
  'a Section 2.1 capability id and a route option id are accepted'
);

-- The service role manages both tables.
select ok(
  has_table_privilege('service_role', 'public.workspace_model_settings', 'INSERT')
  and has_table_privilege('service_role', 'public.project_model_settings', 'UPDATE'),
  'the service role manages both model choice settings tables'
);

-- The worker reads both tables but is never granted a write.
select ok(
  has_table_privilege('motionly_worker', 'public.workspace_model_settings', 'SELECT')
  and has_table_privilege('motionly_worker', 'public.project_model_settings', 'SELECT')
  and not has_table_privilege('motionly_worker', 'public.workspace_model_settings', 'INSERT')
  and not has_table_privilege('motionly_worker', 'public.workspace_model_settings', 'UPDATE')
  and not has_table_privilege('motionly_worker', 'public.project_model_settings', 'INSERT')
  and not has_table_privilege('motionly_worker', 'public.project_model_settings', 'UPDATE'),
  'the worker reads the model choice settings but never writes them'
);

-- A worker row policy lets the worker read a workspace choice the API created.
set local role motionly_worker;
select set_config('motionly_test.worker_sees_choice', (
  select count(*)::text from public.workspace_model_settings
  where workspace_id = '3e000000-0000-4000-8000-0000000000cc'
    and capability = 'planning.chat'
), true);
reset role;
select is(
  current_setting('motionly_test.worker_sees_choice')::integer,
  1,
  'the worker can read a workspace model choice the API created'
);

select * from finish();
rollback;
