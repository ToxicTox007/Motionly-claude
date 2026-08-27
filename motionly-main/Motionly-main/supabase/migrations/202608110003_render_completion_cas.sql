-- Audit finding 7: render completion is a single compare-and-set transaction.
-- A cancellation owns the authoritative render_jobs row and cannot be overwritten
-- by a late worker that has already rendered or uploaded an attempt artifact.

create or replace function public.motionly_guard_render_terminal_state()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if old.status::text in ('failed', 'canceled')
     and new.status is distinct from old.status
  then
    raise exception 'RENDER_TERMINAL_STATE' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

drop trigger if exists task25_guard_render_terminal_state on public.render_jobs;
create trigger task25_guard_render_terminal_state
before update of status on public.render_jobs
for each row execute function public.motionly_guard_render_terminal_state();

revoke all on function public.motionly_guard_render_terminal_state()
from public, anon, authenticated, service_role;

create or replace function public.motionly_complete_render(
  p_workspace_id uuid,
  p_project_id uuid,
  p_render_job_id uuid,
  p_storage_bucket text,
  p_storage_path text,
  p_format text,
  p_width integer,
  p_height integer,
  p_fps integer,
  p_duration_ms integer,
  p_file_size_bytes bigint,
  p_thumbnail_url text,
  p_settings_summary jsonb,
  p_benchmark_ms integer
)
returns table (
  render_job_id uuid,
  export_id uuid,
  status public.render_job_status,
  artifact_path text,
  benchmark_ms integer
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  render public.render_jobs;
  exported public.exports;
  run public.workflow_runs;
  plan public.edit_plans;
  next_version integer;
  canonical_prefix text;
  previous_status public.render_job_status;
  export_exists boolean;
begin
  if p_workspace_id is null
     or p_project_id is null
     or p_render_job_id is null
     or nullif(btrim(p_storage_bucket), '') is null
     or nullif(btrim(p_storage_path), '') is null
     or p_format <> 'mp4'
     or p_width is null or p_width < 1
     or p_height is null or p_height < 1
     or p_fps is null or p_fps < 1
     or p_duration_ms is not null and p_duration_ms < 0
     or p_file_size_bytes is null or p_file_size_bytes < 0
     or p_benchmark_ms is null or p_benchmark_ms < 0
     or jsonb_typeof(coalesce(p_settings_summary, '{}'::jsonb)) <> 'object'
  then
    raise exception 'RENDER_COMPLETION_PAYLOAD_INVALID' using errcode = '22023';
  end if;

  canonical_prefix := format(
    'workspaces/%s/projects/%s/exports/%s/attempts/',
    p_workspace_id, p_project_id, p_render_job_id
  );
  if p_storage_path not like canonical_prefix || '%' then
    raise exception 'RENDER_COMPLETION_ARTIFACT_SCOPE_INVALID' using errcode = 'P0001';
  end if;

  -- This is deliberately acquired before the row lock to match the legacy
  -- export version allocator's lock ordering and avoid lock-order deadlocks.
  perform pg_advisory_xact_lock(hashtextextended(
    p_workspace_id || ':' || p_project_id || ':base-export', 0
  ));

  select * into render
  from public.render_jobs
  where id = p_render_job_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    raise exception 'RENDER_NOT_FOUND' using errcode = 'P0002';
  end if;

  if p_width is distinct from (render.specs->>'width')::integer
     or p_height is distinct from (render.specs->>'height')::integer
     or p_fps is distinct from (render.specs->>'fps')::integer
     or p_format is distinct from coalesce(render.specs->>'format', 'mp4')
  then
    raise exception 'RENDER_OUTPUT_SPECS_MISMATCH' using errcode = 'P0001';
  end if;

  select * into exported
  from public.exports exported_row
  where exported_row.workspace_id = p_workspace_id
    and exported_row.project_id = p_project_id
    and exported_row.render_job_id = p_render_job_id
    and exported_row.restored_from_export_id is null
  for update;
  export_exists := found;

  -- Exact replay is successful only after all canonical output details match.
  if render.status::text in ('complete', 'succeeded') then
    if not export_exists
       or exported.storage_bucket is distinct from p_storage_bucket
       or exported.storage_path is distinct from p_storage_path
       or exported.format::text is distinct from p_format
       or exported.width is distinct from p_width
       or exported.height is distinct from p_height
       or exported.fps is distinct from p_fps
       or exported.duration_ms is distinct from p_duration_ms
       or exported.file_size_bytes is distinct from p_file_size_bytes
       or exported.thumbnail_url is distinct from p_thumbnail_url
       or exported.settings_summary is distinct from coalesce(p_settings_summary, '{}'::jsonb)
       or render.artifact_path is distinct from p_storage_path
       or render.benchmark_ms is distinct from p_benchmark_ms
    then
      raise exception 'RENDER_COMPLETION_IDEMPOTENCY_CONFLICT' using errcode = 'P0001';
    end if;
    return query
    select render.id, exported.id, render.status, render.artifact_path, render.benchmark_ms;
    return;
  end if;

  if render.status::text in ('canceled', 'failed') then
    raise exception 'RENDER_TERMINAL_STATE' using errcode = 'P0001';
  end if;
  if render.status::text not in ('queued', 'preparing', 'rendering', 'uploading', 'processing') then
    raise exception 'RENDER_COMPLETION_STATUS_INVALID' using errcode = 'P0001';
  end if;
  previous_status := render.status;

  -- Revalidate the workflow/plan/timeline lineage under the same transaction
  -- that publishes the output. Compatibility renders have no workflow lineage.
  if render.workflow_run_id is not null then
    select * into run
    from public.workflow_runs
    where id = render.workflow_run_id
      and workspace_id = p_workspace_id
      and project_id = p_project_id
    for share;
    if not found
       or run.state <> 'draft_rendering'
       or run.uploaded_video_id is distinct from render.uploaded_video_id
       or run.source_media_revision is distinct from render.source_media_revision
       or run.active_plan_id is distinct from render.edit_plan_id
       or run.active_plan_revision is distinct from render.source_plan_revision
    then
      raise exception 'RENDER_WORKFLOW_LINEAGE_INVALID' using errcode = 'P0001';
    end if;

    select * into plan
    from public.edit_plans
    where id = render.edit_plan_id
      and workspace_id = p_workspace_id
      and project_id = p_project_id
    for share;
    if not found
       or not plan.is_active
       or plan.status <> 'ready'
       or plan.revision is distinct from render.source_plan_revision
       or not exists (
         select 1
         from public.timeline_revisions timeline
         join public.workflow_run_artifacts artifact
           on artifact.workspace_id = timeline.workspace_id
          and artifact.project_id = timeline.project_id
          and artifact.workflow_run_id = render.workflow_run_id
          and artifact.artifact_type = 'timeline_revision'
          and artifact.timeline_revision_id = timeline.id
          and artifact.artifact_revision = timeline.revision
         where timeline.id = render.timeline_revision_id
           and timeline.workspace_id = p_workspace_id
           and timeline.project_id = p_project_id
           and timeline.edit_plan_id = render.edit_plan_id
           and timeline.revision = render.source_plan_revision
           and timeline.plan_snapshot is not distinct from plan.plan
       )
    then
      raise exception 'RENDER_WORKFLOW_PLAN_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
  end if;

  if export_exists then
    if exported.workflow_run_id is distinct from render.workflow_run_id
       or exported.source_plan_revision is distinct from render.source_plan_revision
    then
      raise exception 'EXPORT_WORKFLOW_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    update public.exports set
      storage_bucket = p_storage_bucket,
      storage_path = p_storage_path,
      format = p_format::public.export_format,
      width = p_width,
      height = p_height,
      fps = p_fps,
      duration_ms = p_duration_ms,
      file_size_bytes = p_file_size_bytes,
      thumbnail_url = p_thumbnail_url,
      settings_summary = coalesce(p_settings_summary, '{}'::jsonb)
    where id = exported.id
    returning * into exported;
  else
    select coalesce(max(version), 0) + 1 into next_version
    from public.exports
    where workspace_id = p_workspace_id
      and project_id = p_project_id;
    insert into public.exports (
      workspace_id, project_id, render_job_id, workflow_run_id,
      source_plan_revision, version, storage_bucket, storage_path, format,
      width, height, fps, duration_ms, file_size_bytes, thumbnail_url,
      settings_summary
    ) values (
      p_workspace_id, p_project_id, p_render_job_id, render.workflow_run_id,
      render.source_plan_revision, next_version, p_storage_bucket, p_storage_path,
      p_format::public.export_format, p_width, p_height, p_fps, p_duration_ms,
      p_file_size_bytes, p_thumbnail_url, coalesce(p_settings_summary, '{}'::jsonb)
    ) returning * into exported;
  end if;

  update public.render_jobs set
    status = 'complete',
    progress_pct = 100,
    artifact_path = p_storage_path,
    benchmark_ms = p_benchmark_ms,
    error_code = null,
    error_message = null,
    updated_at = clock_timestamp()
  where id = render.id
  returning * into render;

  insert into public.render_job_events (
    render_job_id, workspace_id, project_id, event_type, from_status,
    to_status, progress_pct, message, latency_ms, metadata
  ) values (
    render.id, p_workspace_id, p_project_id, 'complete',
    previous_status, 'complete', 100,
    'Render completed through the transactional completion RPC.', p_benchmark_ms,
    jsonb_build_object('export_id', exported.id, 'completion_rpc', 'motionly_complete_render')
  );

  return query
  select render.id, exported.id, render.status, render.artifact_path, render.benchmark_ms;
end;
$$;

revoke all on function public.motionly_complete_render(
  uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb,integer
) from public, anon, authenticated;
grant execute on function public.motionly_complete_render(
  uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb,integer
) to service_role;

-- The legacy export RPC remains for pre-existing preparation paths only. It
-- cannot touch a terminal render, so it is not a completion bypass.
create or replace function public.motionly_upsert_base_export(
  p_workspace_id uuid,
  p_project_id uuid,
  p_render_job_id uuid,
  p_storage_bucket text,
  p_storage_path text,
  p_format text,
  p_width integer,
  p_height integer,
  p_fps integer,
  p_duration_ms integer,
  p_file_size_bytes bigint,
  p_thumbnail_url text,
  p_settings_summary jsonb
)
returns setof public.exports
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  render public.render_jobs;
  exported public.exports;
  next_version integer;
begin
  perform pg_advisory_xact_lock(hashtextextended(
    p_workspace_id || ':' || p_project_id || ':base-export', 0
  ));
  select * into render from public.render_jobs
  where id = p_render_job_id and workspace_id = p_workspace_id and project_id = p_project_id
  for update;
  if not found then
    raise exception 'RENDER_NOT_FOUND' using errcode = 'P0002';
  end if;
  if render.status::text in ('canceled', 'failed', 'complete', 'succeeded') then
    raise exception 'RENDER_COMPLETION_RPC_REQUIRED' using errcode = 'P0001';
  end if;
  if p_width is distinct from (render.specs->>'width')::integer
     or p_height is distinct from (render.specs->>'height')::integer
     or p_fps is distinct from (render.specs->>'fps')::integer then
    raise exception 'RENDER_OUTPUT_SPECS_MISMATCH' using errcode = 'P0001';
  end if;
  select * into exported from public.exports
  where workspace_id = p_workspace_id and project_id = p_project_id
    and render_job_id = p_render_job_id and restored_from_export_id is null
  for update;
  if found then
    if exported.workflow_run_id is distinct from render.workflow_run_id
       or exported.source_plan_revision is distinct from render.source_plan_revision
    then
      raise exception 'EXPORT_WORKFLOW_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    update public.exports set
      storage_bucket = p_storage_bucket,
      storage_path = p_storage_path,
      format = p_format::public.export_format,
      width = p_width,
      height = p_height,
      fps = p_fps,
      duration_ms = p_duration_ms,
      file_size_bytes = p_file_size_bytes,
      thumbnail_url = p_thumbnail_url,
      settings_summary = coalesce(p_settings_summary, '{}'::jsonb)
    where id = exported.id returning * into exported;
    return next exported;
    return;
  end if;
  select coalesce(max(version), 0) + 1 into next_version
  from public.exports where workspace_id = p_workspace_id and project_id = p_project_id;
  insert into public.exports (
    workspace_id, project_id, render_job_id, workflow_run_id,
    source_plan_revision, version, storage_bucket, storage_path, format,
    width, height, fps, duration_ms, file_size_bytes, thumbnail_url,
    settings_summary
  ) values (
    p_workspace_id, p_project_id, p_render_job_id, render.workflow_run_id,
    render.source_plan_revision, next_version, p_storage_bucket, p_storage_path,
    p_format::public.export_format, p_width, p_height, p_fps, p_duration_ms,
    p_file_size_bytes, p_thumbnail_url, coalesce(p_settings_summary, '{}'::jsonb)
  ) returning * into exported;
  return next exported;
end;
$$;

revoke all on function public.motionly_upsert_base_export(
  uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb
) from public, anon, authenticated;
grant execute on function public.motionly_upsert_base_export(
  uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb
) to service_role;
