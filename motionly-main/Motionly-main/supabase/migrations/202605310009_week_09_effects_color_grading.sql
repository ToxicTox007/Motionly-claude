-- Week 9 Effects and Color Grading.
-- Scope: visual presets, LUT intensity, texture settings, motion effects, previews, and RLS.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'visual_preset_key') then
    create type public.visual_preset_key as enum (
      'vogue_luxury',
      'champagne_gold',
      'dubai_influencer',
      'old_money',
      'clean_founder',
      'cinematic_hotel'
    );
  end if;
end $$;

create table if not exists public.visual_presets (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  edit_plan_id uuid references public.edit_plans(id) on delete set null,
  version integer not null,
  schema_version integer not null default 1,
  status text not null,
  preset public.visual_preset_key not null,
  lut_intensity numeric(4,3) not null default 0.75 check (lut_intensity >= 0.5 and lut_intensity <= 1),
  texture jsonb not null default '{"grain":0.18,"vignette":0.16,"light_leak":0.08,"anamorphic_flare":false}'::jsonb,
  motion_effects text[] not null default array[]::text[],
  effect_count_limit_per_30s integer not null default 4 check (effect_count_limit_per_30s >= 0),
  render_time_impact text not null default 'medium',
  preview_before_url text,
  preview_after_url text,
  cache_key text,
  error_code text,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists visual_presets_project_version_idx
on public.visual_presets (workspace_id, project_id, version desc, created_at desc);

create index if not exists visual_presets_project_cache_idx
on public.visual_presets (workspace_id, project_id, cache_key)
where cache_key is not null;

alter table public.visual_presets enable row level security;

drop policy if exists "members read visual presets" on public.visual_presets;
create policy "members read visual presets"
on public.visual_presets for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members manage visual presets" on public.visual_presets;
create policy "members manage visual presets"
on public.visual_presets for all
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));
