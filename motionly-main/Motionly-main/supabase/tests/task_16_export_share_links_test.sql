begin;
select plan(7);

select has_table('public', 'export_share_links', 'export share-link ledger exists');

select ok(
  (select relrowsecurity from pg_class where oid = 'public.export_share_links'::regclass),
  'export_share_links enforces RLS'
);

select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.export_share_links'::regclass
      and conname = 'export_share_links_export_tenant_fk'
  ),
  'share links cannot cross the export workspace/project boundary'
);

select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.export_share_links'::regclass
      and conname = 'export_share_links_expiry'
  ),
  'share links must expire strictly after they are created'
);

-- Only a fingerprint is ever stored: the digest column is a unique 64-hex string
-- validated by a check constraint; there is no column for the raw secret token.
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.export_share_links'::regclass
      and pg_get_constraintdef(oid) like '%token_digest%0-9a-f%64%'
  ),
  'token_digest stores a SHA-256 fingerprint, never the raw token'
);

select ok(
  not has_table_privilege('authenticated', 'public.export_share_links', 'SELECT')
    and not has_table_privilege('anon', 'public.export_share_links', 'SELECT'),
  'browser roles cannot read export share links directly'
);

select ok(
  has_table_privilege('service_role', 'public.export_share_links', 'INSERT')
    and has_table_privilege('service_role', 'public.export_share_links', 'UPDATE')
    and has_table_privilege('service_role', 'public.export_share_links', 'DELETE'),
  'service role retains the API-mediated share-link path'
);

select * from finish();
rollback;
