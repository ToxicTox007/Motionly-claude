-- pgTAP coverage for the Task 20 (M5) inference registry migration
-- (supabase/migrations/202610250101_inference_registry.sql).
--
-- Postgres cannot run in the implementation sandbox, so this contract is
-- validated statically by apps/api/tests/test_migration_contract_coverage.py
-- (the required tokens must appear in both files) and executed in CI where a
-- database is available. It asserts the five tables exist, the offering column
-- is present on ai_routes, the singleton version counter is bumped by a write,
-- RLS is on for every table, the worker may read the catalog tables but never
-- ai_secrets, and the service role manages every table.
begin;
select plan(13);

-- Tables exist.
select has_table('public', 'ai_adapters', 'ai_adapters table exists');
select has_table('public', 'ai_models', 'ai_models table exists');
select has_table('public', 'ai_routes', 'ai_routes table exists');
select has_table('public', 'ai_secrets', 'ai_secrets table exists');
select has_table('public', 'ai_registry_version', 'ai_registry_version table exists');

-- ai_routes carries the Section 2.11 offering jsonb, and ai_secrets has no
-- plaintext column.
select has_column('public', 'ai_routes', 'offering', 'ai_routes has an offering column');
select hasnt_column('public', 'ai_secrets', 'plaintext', 'ai_secrets has no plaintext column');

-- RLS is enabled on every registry table.
select is(
  (
    select coalesce(bool_and(c.relrowsecurity), false)
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relname in (
        'ai_adapters', 'ai_models', 'ai_routes', 'ai_secrets', 'ai_registry_version'
      )
  ),
  true,
  'row level security is enabled on every registry table'
);

-- The singleton version counter is bumped in the same transaction as a write.
select set_config('motionly_test.version_before', (
  select version::text from public.ai_registry_version where id = 1
), true);
insert into public.ai_adapters (id, type, base_url, network, auth)
  values ('pgtap-adapter', 'openai_compatible', 'https://example.invalid/v1', 'public',
          '{"type":"bearer","secret_ref":"db:pgtap"}'::jsonb);
select ok(
  (select version from public.ai_registry_version where id = 1)
    > current_setting('motionly_test.version_before')::bigint,
  'a registry write bumps ai_registry_version via motionly_bump_ai_registry_version'
);

-- The worker may read the catalog tables and the version counter.
select ok(
  has_table_privilege('motionly_worker', 'public.ai_adapters', 'SELECT')
  and has_table_privilege('motionly_worker', 'public.ai_models', 'SELECT')
  and has_table_privilege('motionly_worker', 'public.ai_routes', 'SELECT')
  and has_table_privilege('motionly_worker', 'public.ai_registry_version', 'SELECT'),
  'the worker may read the catalog tables and the version counter'
);

-- The worker is never granted ai_secrets in any form.
select ok(
  not has_table_privilege('motionly_worker', 'public.ai_secrets', 'SELECT')
  and not has_table_privilege('motionly_worker', 'public.ai_secrets', 'INSERT')
  and not has_table_privilege('motionly_worker', 'public.ai_secrets', 'UPDATE')
  and not has_table_privilege('motionly_worker', 'public.ai_secrets', 'DELETE'),
  'the worker is never granted ai_secrets'
);

-- ai_secrets has a service_role policy but no worker policy.
select ok(
  exists (
    select 1 from pg_policy p
    join pg_class c on c.oid = p.polrelid
    where c.relname = 'ai_secrets'
      and p.polroles @> array['service_role'::regrole::oid]
  )
  and not exists (
    select 1 from pg_policy p
    join pg_class c on c.oid = p.polrelid
    where c.relname = 'ai_secrets'
      and p.polroles @> array['motionly_worker'::regrole::oid]
  ),
  'ai_secrets is managed by the service role and carries no worker policy'
);

-- The service role manages every registry table.
select ok(
  has_table_privilege('service_role', 'public.ai_secrets', 'INSERT')
  and has_table_privilege('service_role', 'public.ai_routes', 'UPDATE'),
  'the service role manages the registry tables'
);

select * from finish();
rollback;
