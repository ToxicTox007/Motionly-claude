-- Preserve the existing composite foreign-key boundary for missing or cross-tenant
-- render references. Task 5 lineage validation only runs after the referenced
-- tenant-scoped render row is visible; otherwise PostgreSQL emits 23503.

create or replace function public.motionly_validate_task5_export_lineage()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  render_run_id uuid;
begin
  select workflow_run_id into render_run_id
  from public.render_jobs
  where id = new.render_job_id
    and workspace_id = new.workspace_id
    and project_id = new.project_id;
  if not found then
    return new;
  end if;
  if new.workflow_run_id is distinct from render_run_id then
    raise exception 'EXPORT_WORKFLOW_LINEAGE_INVALID' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

revoke all on function public.motionly_validate_task5_export_lineage()
from public, anon, authenticated;
grant execute on function public.motionly_validate_task5_export_lineage()
to service_role;
