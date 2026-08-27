-- Prevent persisted exports from claiming specs different from the requested render.

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
  for share;
  if not found then raise exception 'RENDER_NOT_FOUND' using errcode = 'P0002'; end if;
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

revoke all on function public.motionly_upsert_base_export(uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb) from public, anon, authenticated;
grant execute on function public.motionly_upsert_base_export(uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb) to service_role;
