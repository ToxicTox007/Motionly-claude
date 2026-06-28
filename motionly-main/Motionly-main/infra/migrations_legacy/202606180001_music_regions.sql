-- Stage 6 follow-up: editable music regions on the timeline.
alter table public.project_music_selections
  add column if not exists regions jsonb not null default '[]'::jsonb;

comment on column public.project_music_selections.regions is
  'Timeline regions for the selected music track. Each region has id, start_ms, end_ms and optional disabled flag.';
