-- Phase 2 database performance: bounded plan status and publish connection reads.

create index if not exists edit_plans_workspace_project_transcript_updated_idx
  on public.edit_plans(workspace_id, project_id, transcript_id, updated_at desc);

create index if not exists publish_connections_workspace_created_idx
  on public.publish_connections(workspace_id, created_at desc);
