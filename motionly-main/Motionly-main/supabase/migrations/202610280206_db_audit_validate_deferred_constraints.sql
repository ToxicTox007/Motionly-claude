-- Database audit DB-17: validate the constraints that were added NOT VALID.
--
-- 39 constraints in public (the task3_* tenant-consistency foreign keys and a
-- few CHECKs) were added NOT VALID so old rows could not block a deploy.
-- PostgreSQL enforces them for every new or updated row, but the rows that
-- existed before were never proven. The operator procedure
-- (motionly_validate_task3_tenant_integrity) was never run.
--
-- This migration validates each one that the existing rows already satisfy.
-- A constraint that existing rows violate is left NOT VALID and reported
-- (RAISE WARNING), so a deploy is never blocked by old data; those rows are
-- found and fixed with motionly_audit_task3_tenant_integrity() and the
-- runbook in docs/ops/database-operations.md, and this file's loop can be
-- rerun by hand. VALIDATE takes a SHARE UPDATE EXCLUSIVE lock: reads and
-- writes continue while it scans.
--
-- Forward-only. Covered by
-- supabase/tests/db_audit_validate_deferred_constraints_test.sql.

begin;

set local lock_timeout = '10s';

do $$
declare
  target record;
  validated integer := 0;
  skipped integer := 0;
begin
  for target in
    select c.conrelid::regclass as table_name, c.conname
    from pg_constraint c
    where c.connamespace = 'public'::regnamespace
      and c.contype in ('f', 'c')
      and not c.convalidated
    order by c.conrelid::regclass::text, c.conname
  loop
    begin
      execute format('alter table %s validate constraint %I', target.table_name, target.conname);
      validated := validated + 1;
    exception
      when foreign_key_violation or check_violation or lock_not_available then
        skipped := skipped + 1;
        raise warning 'db-audit: % on % stays NOT VALID (%): existing rows need repair',
          target.conname, target.table_name, sqlerrm;
    end;
  end loop;
  raise notice 'db-audit: validated % deferred constraints, % left NOT VALID', validated, skipped;
end
$$;

commit;
