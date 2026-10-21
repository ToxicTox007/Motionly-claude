-- Task 22 (follow-up): let PostgREST serve the gateway's own role.
--
-- The payment gateway persists to its private `mock_gateway` schema through the
-- shared PostgREST, presenting a JWT whose `role` claim is `mock_gateway_role`
-- (generated in scripts/release/write_compose_test_env.py, mounted only into the
-- payment-gateway service). PostgREST runs as `authenticator` and `set role`s
-- into the JWT's role, so `authenticator` must be a member of
-- `mock_gateway_role`. This grant adds exactly that membership and nothing else:
-- the gateway role still has NO privilege in public/auth/storage (migrations
-- 202610190801 and 202610190802), and anon/authenticated/service_role still
-- cannot touch `mock_gateway` (revoked in 202610190801).
--
-- Forward-only. Reserved timestamp 2026101908xx (Task 22).

begin;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'authenticator')
     and exists (select 1 from pg_roles where rolname = 'mock_gateway_role') then
    execute 'grant mock_gateway_role to authenticator';
  end if;
end
$$;

commit;
