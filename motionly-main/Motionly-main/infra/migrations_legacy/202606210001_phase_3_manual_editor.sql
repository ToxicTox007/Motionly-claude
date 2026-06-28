-- Phase 3 manual editor persistence additions.
-- Manual spatial/temporal elements live inside edit_plans.plan JSONB so plan
-- snapshots, restores, and render manifests remain atomic.

alter type public.stock_provider add value if not exists 'user_library';
