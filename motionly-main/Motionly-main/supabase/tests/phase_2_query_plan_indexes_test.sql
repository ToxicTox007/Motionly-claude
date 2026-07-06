begin;
select plan(5);

select ok(
  to_regclass('public.worker_heartbeats_last_seen_at_desc_idx') is not null,
  'worker heartbeat freshness index exists'
);

select ok(
  to_regclass('public.uploaded_videos_workspace_project_updated_idx') is not null,
  'active upload lookup index exists'
);

select ok(
  to_regclass('public.cost_attribution_workspace_provider_category_created_idx') is not null,
  'cost usage workspace/provider/category/time index exists'
);

select ok(
  to_regclass('public.edit_plans_workspace_project_transcript_updated_idx') is not null,
  'latest plan by transcript index exists'
);

select ok(
  to_regclass('public.publish_connections_workspace_created_idx') is not null,
  'publish connection workspace list index exists'
);

select * from finish();
rollback;
