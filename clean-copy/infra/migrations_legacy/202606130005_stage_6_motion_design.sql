alter table public.visual_presets
  add column if not exists motion jsonb not null default '{}'::jsonb;
