-- Task 10 / M1 (forward-only): feature-keyed quota usage counting.
--
-- Decision B-1 counts plan quotas per Motionly feature/category, summed across
-- every provider label, so the hot query filters by (workspace_id, category,
-- created_at) without a provider. The existing indexes lead with
-- (workspace_id, provider, category, ...) (202606220008_phase_8_operations.sql,
-- 202607050003_phase_2_upload_cost_indexes.sql), so a provider-less count cannot
-- use them efficiently. This index leads with category so the feature-scope
-- count is index-only. No category constraint change is needed: the `tokens`
-- category is already allowed by 202610220101_remote_ai_feature_quota_categories.sql.

create index if not exists cost_attribution_workspace_category_created_idx
  on public.cost_attribution_events(workspace_id, category, created_at desc);
