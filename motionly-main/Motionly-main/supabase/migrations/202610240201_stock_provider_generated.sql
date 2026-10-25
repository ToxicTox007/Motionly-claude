-- Task 18 / M3: add the neutral 'generated' value to public.stock_provider.
--
-- Generated B-roll moves off the legacy vendor provider label onto the neutral
-- 'generated' key (plan Task 18, Section 2.5). A new enum value cannot be used
-- in the same transaction that adds it, so this is its own migration and is NOT
-- wrapped in begin/commit (matching 202606210003_phase_5_scale.sql, which added
-- the legacy value the same way). The backfill and the CHECK constraints that
-- forbid the legacy value live in the next migration
-- (202610240202_neutral_generated_broll.sql), which runs in its own
-- transaction.
--
-- Idempotent: ``add value if not exists`` is a no-op when the value is present.
-- Forward-only. Not edited after it is applied (scripts/check_forward_migrations.sh).

alter type public.stock_provider add value if not exists 'generated';
