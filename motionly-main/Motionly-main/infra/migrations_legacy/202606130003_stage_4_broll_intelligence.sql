-- Stage 4 B-roll intelligence.
-- Stores semantic matching and placement-rule diagnostics for generated B-roll slots.

alter table public.broll_slots
add column if not exists metadata jsonb not null default '{}'::jsonb;
