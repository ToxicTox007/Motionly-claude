-- Task 22: the mock payment gateway schema is isolated from the application.
-- The gateway role can touch only `mock_gateway`; it has no effective reach
-- into the application's `public`, `auth` or `storage` schemas. The
-- application/browser roles cannot touch `mock_gateway`.

begin;
select plan(14);

-- Schema and tables exist.
select has_schema('mock_gateway', 'the gateway schema exists');
select has_table('mock_gateway', 'orders', 'orders are persisted');
select has_table('mock_gateway', 'subscriptions', 'subscriptions are persisted');
select has_table('mock_gateway', 'refunds', 'refunds are persisted');
select has_table('mock_gateway', 'webhook_events', 'webhook events are persisted');

-- The gateway role exists and can use its own schema.
select ok(
  exists (select 1 from pg_roles where rolname = 'mock_gateway_role'),
  'the dedicated mock_gateway_role exists'
);
select ok(
  has_schema_privilege('mock_gateway_role', 'mock_gateway', 'USAGE'),
  'the gateway role may use its own schema'
);

-- TEST 8 (fixed truthfully): the gateway role has NO effective privilege in the
-- application schemas. PostgreSQL privileges are additive, so bare USAGE on
-- `public` cannot be revoked from a single role (it is granted to PUBLIC) — but
-- USAGE alone conveys nothing. We assert the *effective* privileges with the
-- catalog `has_*_privilege` helpers (which do not depend on which role runs the
-- test, unlike information_schema views): no table or sequence privilege in
-- public/auth/storage, and no function EXECUTE in `public` (where the role DOES
-- have schema USAGE, so EXECUTE would be the real risk — migration 202610190802
-- revoked PUBLIC's ambient grant there).
select is(
  (select count(*)::int
   from pg_class c
   join pg_namespace n on n.oid = c.relnamespace
   cross join lateral (values ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),
                              ('TRUNCATE'),('REFERENCES'),('TRIGGER')) as priv(p)
   where n.nspname in ('public','auth','storage')
     and c.relkind in ('r','p','v','m','f')
     and has_table_privilege('mock_gateway_role', c.oid, priv.p)),
  0,
  'the gateway role has no table privilege in public, auth or storage'
);
select is(
  (select count(*)::int
   from pg_class c
   join pg_namespace n on n.oid = c.relnamespace
   cross join lateral (values ('USAGE'),('SELECT'),('UPDATE')) as priv(p)
   where n.nspname in ('public','auth','storage')
     and c.relkind = 'S'
     and has_sequence_privilege('mock_gateway_role', c.oid, priv.p)),
  0,
  'the gateway role has no sequence privilege in public, auth or storage'
);
select is(
  (select count(*)::int
   from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'public'
     and has_function_privilege('mock_gateway_role', p.oid, 'EXECUTE')),
  0,
  'the gateway role cannot EXECUTE any function in public'
);

-- The gateway role has no USAGE on auth or storage, so it cannot resolve — let
-- alone call — anything in them, even functions that still carry PUBLIC''s
-- ambient EXECUTE grant (those grants are inert without schema USAGE).
select ok(
  not has_schema_privilege('mock_gateway_role', 'auth', 'USAGE')
  and not has_schema_privilege('mock_gateway_role', 'storage', 'USAGE'),
  'the gateway role cannot use the auth or storage schemas'
);

-- Application + browser roles cannot use the gateway schema.
select ok(
  not has_schema_privilege('service_role', 'mock_gateway', 'USAGE')
  and not has_schema_privilege('authenticated', 'mock_gateway', 'USAGE')
  and not has_schema_privilege('anon', 'mock_gateway', 'USAGE'),
  'application and browser roles cannot use the mock_gateway schema'
);

-- Full card numbers are never stored: last four is a fixed-width digest and
-- there is no column that could hold a full PAN.
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'mock_gateway.orders'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) like '%[0-9]{4}%'
  ),
  'stored card_last4 is constrained to four digits'
);
select ok(
  (select count(*) from information_schema.columns
   where table_schema = 'mock_gateway'
     and table_name = 'orders'
     and column_name in ('card_number', 'pan', 'cvv')) = 0,
  'no column can hold a full card number'
);

select * from finish();
rollback;
