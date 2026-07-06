begin;

insert into auth.users (id, email, raw_user_meta_data)
values (
  '41000000-0000-4000-8000-000000000001',
  'phase2-explain@motionly.test',
  '{}'::jsonb
)
on conflict (id) do nothing;

insert into public.workspaces (id, name, owner_user_id)
values (
  '42000000-0000-4000-8000-000000000001',
  'Phase 2 EXPLAIN workspace',
  '41000000-0000-4000-8000-000000000001'
);

insert into public.projects (id, workspace_id, created_by, title)
values (
  '43000000-0000-4000-8000-000000000001',
  '42000000-0000-4000-8000-000000000001',
  '41000000-0000-4000-8000-000000000001',
  'Phase 2 EXPLAIN project'
);

insert into public.worker_heartbeats (
  worker_id,
  worker_role,
  queues,
  version,
  metadata,
  started_at,
  last_seen_at
)
select
  'phase2-explain-worker-' || gs::text,
  'render',
  array['render'],
  'test',
  '{}'::jsonb,
  now() - (gs || ' seconds')::interval,
  now() - (gs || ' seconds')::interval
from generate_series(1, 20000) as gs;

insert into public.uploaded_videos (
  id,
  workspace_id,
  project_id,
  storage_bucket,
  storage_path,
  original_filename,
  mime_type,
  byte_size,
  file_size_bytes,
  status,
  duration_ms,
  width,
  height,
  metadata,
  updated_at
)
select
  gen_random_uuid(),
  '42000000-0000-4000-8000-000000000001',
  '43000000-0000-4000-8000-000000000001',
  'raw-uploads',
  'phase2/video-' || gs::text || '.mp4',
  'video-' || gs::text || '.mp4',
  'video/mp4',
  1000,
  1000,
  'ready',
  10000,
  1080,
  1920,
  '{}'::jsonb,
  now() - (gs || ' seconds')::interval
from generate_series(1, 20000) as gs;

insert into public.transcripts (
  id,
  workspace_id,
  project_id,
  uploaded_video_id,
  status,
  language,
  language_hint,
  provider,
  model,
  duration_ms,
  raw_payload
)
select
  '44000000-0000-4000-8000-000000000001',
  '42000000-0000-4000-8000-000000000001',
  '43000000-0000-4000-8000-000000000001',
  id,
  'ready',
  'en',
  'auto',
  'fixture',
  'fixture',
  10000,
  '{}'::jsonb
from public.uploaded_videos
where workspace_id = '42000000-0000-4000-8000-000000000001'
limit 1;

insert into public.edit_plans (
  workspace_id,
  project_id,
  transcript_id,
  version,
  schema_version,
  status,
  is_active,
  style,
  target_duration_s,
  broll_density,
  prompt,
  provider,
  model,
  cache_key,
  schema_hash,
  plan,
  summary,
  updated_at
)
select
  '42000000-0000-4000-8000-000000000001',
  '43000000-0000-4000-8000-000000000001',
  '44000000-0000-4000-8000-000000000001',
  gs,
  1,
  'ready',
  false,
  'luxury',
  30,
  'medium',
  'phase2 explain',
  'fixture',
  'fixture',
  'phase2-explain-' || gs::text,
  'phase2-hash',
  '{}'::jsonb,
  '{}'::jsonb,
  now() - (gs || ' seconds')::interval
from generate_series(1, 20000) as gs;

insert into public.publish_connections (
  workspace_id,
  platform,
  access_token,
  external_account_id,
  account_name,
  created_at
)
select
  '42000000-0000-4000-8000-000000000001',
  'tiktok',
  'token-' || gs::text,
  'acct-' || gs::text,
  'Account ' || gs::text,
  now() - (gs || ' seconds')::interval
from generate_series(1, 20000) as gs;

analyze public.worker_heartbeats;
analyze public.uploaded_videos;
analyze public.edit_plans;
analyze public.publish_connections;

\echo 'worker_heartbeats_latest'
explain (analyze, buffers)
select worker_id, worker_role, queues, version, metadata, last_seen_at
from public.worker_heartbeats
order by last_seen_at desc
limit 100;

\echo 'uploaded_videos_active_lookup'
explain (analyze, buffers)
select id, project_id, workspace_id, storage_bucket, storage_path,
       original_filename, mime_type, file_size_bytes, byte_size, status,
       thumbnail_path, metadata, error_code, error_message, created_at, updated_at
from public.uploaded_videos
where project_id = '43000000-0000-4000-8000-000000000001'
  and workspace_id = '42000000-0000-4000-8000-000000000001'
order by updated_at desc
limit 1;

\echo 'edit_plans_latest_by_transcript'
explain (analyze, buffers)
select id, workspace_id, project_id, transcript_id, version, schema_version,
       status, is_active, style, target_duration_s, broll_density, prompt,
       feedback, provider, model, planner_source, model_name, prompt_version,
       retry_count, fallback_reason, cache_key, schema_hash, latency_ms,
       cost_estimate_usd, validation_errors, summary, plan, created_at, updated_at
from public.edit_plans
where workspace_id = '42000000-0000-4000-8000-000000000001'
  and project_id = '43000000-0000-4000-8000-000000000001'
  and transcript_id = '44000000-0000-4000-8000-000000000001'
order by updated_at desc
limit 1;

\echo 'publish_connections_workspace_list'
explain (analyze, buffers)
select id, workspace_id, platform, access_token, refresh_token,
       token_expires_at, refresh_token_expires_at, scopes, external_account_id,
       account_name, connection_status, last_refresh_at,
       last_refresh_failure_reason, created_at
from public.publish_connections
where workspace_id = '42000000-0000-4000-8000-000000000001'
order by created_at desc
limit 100;

rollback;
