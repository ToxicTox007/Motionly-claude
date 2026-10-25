-- Task 18 / M3 pgTAP: public.stock_provider gained the neutral 'generated'
-- value in its own migration (a new enum value cannot be used in the same
-- transaction that adds it). Forward-only guarantee for
-- 202610240201_stock_provider_generated.sql.
--
-- NOTE: Postgres cannot run in this sandbox (docker exec is denied), so this
-- file was validated statically (SQL review + the migration-convention pytest
-- suites). pgTAP did not run here.
begin;
select plan(2);

select has_type('public', 'stock_provider', 'the stock_provider enum exists');

-- The neutral 'generated' label is a member of the enum.
select ok(
  'generated' = any (enum_range(null::public.stock_provider)::text[]),
  'the generated value is a member of public.stock_provider'
);

select * from finish();
rollback;
