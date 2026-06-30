-- Week 6 Remotion rendering engine.
-- Scope: premium template metadata, immutable props hash, artifacts, logs, and benchmarks.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'render_engine') then
    create type public.render_engine as enum ('ffmpeg_basic', 'remotion_premium');
  end if;

  if not exists (select 1 from pg_type where typname = 'render_template_id') then
    create type public.render_template_id as enum (
      'luxury_reel_v1',
      'founder_reel_v1',
      'travel_reel_v1',
      'podcast_reel_v1',
      'fashion_reel_v1'
    );
  end if;
end $$;

alter table public.render_jobs
  add column if not exists template_id public.render_template_id,
  add column if not exists render_engine public.render_engine not null default 'ffmpeg_basic',
  add column if not exists renderer_version text,
  add column if not exists props_hash text,
  add column if not exists artifact_path text,
  add column if not exists benchmark_ms integer,
  add column if not exists logs jsonb not null default '[]'::jsonb;

create index if not exists render_jobs_template_created_idx
on public.render_jobs (template_id, created_at desc);

create index if not exists render_jobs_props_hash_idx
on public.render_jobs (workspace_id, project_id, props_hash);
