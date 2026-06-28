-- Store user-edited per-segment caption highlights.

alter table public.caption_segments
  add column if not exists highlight_words jsonb not null default '[]'::jsonb;
