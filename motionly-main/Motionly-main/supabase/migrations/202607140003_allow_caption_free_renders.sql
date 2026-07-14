-- Caption tracks are optional for exports that explicitly disable burned-in captions.

alter table public.render_jobs
  drop constraint if exists render_jobs_caption_track_required,
  alter column caption_track_id drop not null;
