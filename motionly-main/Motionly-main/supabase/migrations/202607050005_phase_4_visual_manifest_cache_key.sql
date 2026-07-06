-- Phase 4 visual manifest cache keys.
-- Adds a stable key for source/model/sampling reuse without hashing video bytes.

alter table public.video_manifests
  add column if not exists cache_key text;

create index if not exists video_manifests_cache_key_idx
on public.video_manifests (workspace_id, project_id, uploaded_video_id, cache_key, updated_at desc)
where cache_key is not null;
