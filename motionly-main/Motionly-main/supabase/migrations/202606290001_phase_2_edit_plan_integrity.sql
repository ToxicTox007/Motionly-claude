-- Phase 2 AI plan data integrity metadata.
-- Adds persisted retry/fallback audit fields for render-safe edit plans.

alter table public.edit_plans
  add column if not exists planner_source text not null default 'unknown',
  add column if not exists model_name text not null default '',
  add column if not exists prompt_version text not null default 'edit-planner-prompt-v2',
  add column if not exists retry_count integer not null default 0,
  add column if not exists fallback_reason text;

alter table public.edit_plan_snapshots
  add column if not exists planner_source text not null default 'unknown',
  add column if not exists model_name text not null default '',
  add column if not exists prompt_version text not null default 'edit-planner-prompt-v2',
  add column if not exists retry_count integer not null default 0,
  add column if not exists fallback_reason text;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'edit_plans_retry_count_nonnegative'
  ) then
    alter table public.edit_plans
      add constraint edit_plans_retry_count_nonnegative check (retry_count >= 0);
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'edit_plan_snapshots_retry_count_nonnegative'
  ) then
    alter table public.edit_plan_snapshots
      add constraint edit_plan_snapshots_retry_count_nonnegative check (retry_count >= 0);
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'edit_plans_planner_source_known'
  ) then
    alter table public.edit_plans
      add constraint edit_plans_planner_source_known check (
        planner_source in (
          'unknown',
          'llm',
          'llm_retry',
          'llm_minimal',
          'deterministic_fixture',
          'deterministic_fallback'
        )
      );
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'edit_plan_snapshots_planner_source_known'
  ) then
    alter table public.edit_plan_snapshots
      add constraint edit_plan_snapshots_planner_source_known check (
        planner_source in (
          'unknown',
          'llm',
          'llm_retry',
          'llm_minimal',
          'deterministic_fixture',
          'deterministic_fallback'
        )
      );
  end if;
end $$;
