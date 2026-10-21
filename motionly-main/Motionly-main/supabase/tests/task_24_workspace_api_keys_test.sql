begin;
select plan(9);

select has_table('public', 'workspace_api_keys', 'workspace API key ledger exists');

select ok(
  (select relrowsecurity from pg_class where oid = 'public.workspace_api_keys'::regclass),
  'workspace_api_keys enforces RLS'
);

-- API keys belong to a workspace and vanish with it.
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.workspace_api_keys'::regclass
      and contype = 'f'
      and confrelid = 'public.workspaces'::regclass
  ),
  'API keys cascade from their owning workspace'
);

-- Only a fingerprint is ever stored: a unique 64-hex SHA-256 string validated
-- by a check constraint; there is no column for the raw key secret.
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.workspace_api_keys'::regclass
      and pg_get_constraintdef(oid) like '%fingerprint%0-9a-f%64%'
  ),
  'fingerprint stores a SHA-256 digest, never the raw key'
);

-- The raw secret is never persisted: there is no column that could hold it.
select ok(
  not exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'workspace_api_keys'
      and column_name in ('key', 'secret', 'raw_key', 'token')
  ),
  'no column stores the raw key secret'
);

-- Display hints exist so a key is recognisable without revealing it.
select has_column('public', 'workspace_api_keys', 'prefix', 'display prefix is stored');
select has_column('public', 'workspace_api_keys', 'last4', 'display last-4 is stored');

select ok(
  not has_table_privilege('authenticated', 'public.workspace_api_keys', 'SELECT')
    and not has_table_privilege('anon', 'public.workspace_api_keys', 'SELECT'),
  'browser roles cannot read API keys directly'
);

select ok(
  has_table_privilege('service_role', 'public.workspace_api_keys', 'INSERT')
    and has_table_privilege('service_role', 'public.workspace_api_keys', 'UPDATE')
    and has_table_privilege('service_role', 'public.workspace_api_keys', 'DELETE'),
  'service role retains the API-mediated key path'
);

select * from finish();
rollback;
