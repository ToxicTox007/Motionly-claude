-- Phase 1 API performance: keep edit-plan versioning and active selection atomic.

create or replace function public.motionly_create_active_edit_plan(
  p_workspace_id uuid,
  p_project_id uuid,
  p_transcript_id uuid,
  p_schema_version integer,
  p_status public.edit_plan_status,
  p_style public.edit_plan_style,
  p_target_duration_s integer,
  p_broll_density public.broll_density,
  p_prompt text,
  p_feedback text,
  p_provider text,
  p_model text,
  p_planner_source text,
  p_model_name text,
  p_prompt_version text,
  p_retry_count integer,
  p_fallback_reason text,
  p_cache_key text,
  p_schema_hash text,
  p_latency_ms integer,
  p_cost_estimate_usd numeric,
  p_validation_errors jsonb,
  p_summary jsonb,
  p_plan jsonb
)
returns setof public.edit_plans
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  next_version integer;
begin
  perform pg_advisory_xact_lock(hashtext(p_project_id::text));

  select coalesce(max(version), 0) + 1
  into next_version
  from public.edit_plans
  where workspace_id = p_workspace_id
    and project_id = p_project_id;

  update public.edit_plans
  set is_active = false,
      updated_at = now()
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and is_active;

  return query
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
    feedback,
    provider,
    model,
    planner_source,
    model_name,
    prompt_version,
    retry_count,
    fallback_reason,
    cache_key,
    schema_hash,
    latency_ms,
    cost_estimate_usd,
    validation_errors,
    summary,
    plan
  )
  values (
    p_workspace_id,
    p_project_id,
    p_transcript_id,
    next_version,
    p_schema_version,
    p_status,
    true,
    p_style,
    p_target_duration_s,
    p_broll_density,
    p_prompt,
    p_feedback,
    p_provider,
    p_model,
    p_planner_source,
    p_model_name,
    p_prompt_version,
    p_retry_count,
    p_fallback_reason,
    p_cache_key,
    p_schema_hash,
    p_latency_ms,
    p_cost_estimate_usd,
    coalesce(p_validation_errors, '[]'::jsonb),
    p_summary,
    p_plan
  )
  returning *;
end;
$$;

create or replace function public.motionly_activate_edit_plan(
  p_workspace_id uuid,
  p_project_id uuid,
  p_plan_id uuid
)
returns setof public.edit_plans
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform pg_advisory_xact_lock(hashtext(p_project_id::text));

  if not exists (
    select 1
    from public.edit_plans
    where id = p_plan_id
      and workspace_id = p_workspace_id
      and project_id = p_project_id
  ) then
    return;
  end if;

  update public.edit_plans
  set is_active = false,
      updated_at = now()
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and is_active
    and id <> p_plan_id;

  return query
  update public.edit_plans
  set is_active = true,
      updated_at = now()
  where id = p_plan_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  returning *;
end;
$$;
