-- Phase 2 database performance: evidence-backed hot-path indexes.

create index if not exists uploaded_videos_workspace_project_updated_idx
  on public.uploaded_videos(workspace_id, project_id, updated_at desc);

create index if not exists cost_attribution_workspace_provider_category_created_idx
  on public.cost_attribution_events(workspace_id, provider, category, created_at desc);
