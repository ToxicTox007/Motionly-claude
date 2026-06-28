-- Extend Week 9 effects with background removal, Gradient Ramp, B-roll slide-ins,
-- and After Effects correction handoff metadata.

alter table public.visual_presets
add column if not exists background_removal jsonb not null
default '{"enabled":false,"apply_to_broll":true,"feather_px":18,"spill_suppression":0.45,"replacement":"gradient_ramp"}'::jsonb,
add column if not exists gradient_ramp jsonb not null
default '{"enabled":false,"start_color":"#0f172a","end_color":"#f59e0b","angle_deg":135,"opacity":0.28,"blend_mode":"soft-light"}'::jsonb,
add column if not exists after_effects_presets jsonb not null default '[]'::jsonb,
add column if not exists after_effects_project_url text,
add column if not exists correction_manifest_url text;
