-- Task 22 (follow-up): real isolation for the mock payment gateway role.
--
-- Background measured on the migrated DB (tests/schema_usage.py inventory +
-- live pg_proc): 19 functions in schema `public` were EXECUTEable by PUBLIC
-- because the default PUBLIC EXECUTE grant on new functions was never revoked.
-- Every one of those 19 already carries an *explicit* EXECUTE grant to the
-- roles that actually call them (service_role, authenticated, anon,
-- motionly_worker and postgres) — the PUBLIC grant was pure surface area. Any
-- role that merely has USAGE on `public` (including the gateway's own
-- `mock_gateway_role`, which inherits USAGE from PUBLIC's un-revokable default)
-- could therefore EXECUTE them.
--
-- This forward-only migration revokes EXECUTE from PUBLIC on every public
-- function. The explicit per-role grants are left untouched, so the application
-- roles keep exactly the access they already had; only the ambient PUBLIC reach
-- is removed. After this, `mock_gateway_role` has NO function EXECUTE in
-- `public` (and never had any table/sequence privilege or USAGE on
-- `auth`/`storage`). New functions in later migrations carry their own explicit
-- grants to the roles that call them (see the existing migrations' grant
-- blocks), so removing PUBLIC's ambient grant does not change any caller.
--
-- Note on schema USAGE: PostgreSQL privileges are additive and a privilege
-- granted to PUBLIC cannot be revoked from a single role, so USAGE on schema
-- `public` cannot be stripped from `mock_gateway_role` alone without revoking
-- it from PUBLIC for the whole cluster (which the Supabase roles and PostgREST
-- rely on). Bare schema USAGE conveys nothing on its own — without table,
-- sequence or function privileges the role can resolve names but touch nothing.
-- The pgTAP test asserts these *effective* privileges (no table/sequence priv,
-- no function EXECUTE) rather than the unremovable USAGE bit.
--
-- Forward-only. Reserved timestamp 2026101908xx (Task 22).

begin;

-- Revoke the ambient PUBLIC EXECUTE grant on every function in `public`. The
-- explicit grants recorded on each function (postgres/anon/authenticated/
-- service_role/motionly_worker) remain, so callers are unaffected.
do $$
declare
  fn record;
begin
  for fn in
    select p.oid::regprocedure as signature
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.prokind in ('f', 'p', 'w')  -- functions, procedures, window fns (not aggregates)
      and has_function_privilege('public', p.oid, 'EXECUTE')
  loop
    execute format('revoke execute on function %s from public', fn.signature);
  end loop;
end
$$;

commit;
