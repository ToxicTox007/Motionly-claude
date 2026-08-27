-- Normalize worker error codes before they enter canonical workflow attempts/events/outbox.
-- The existing Task 7 implementation remains the sole transactional mutation body;
-- this wrapper adds a fail-closed identifier grammar at its service-role boundary.

create or replace function public.motionly_normalize_workflow_error_code(p_value text)
returns text
language plpgsql
immutable
strict
set search_path = public, pg_temp
as $$
declare
  normalized text;
begin
  normalized := regexp_replace(
    regexp_replace(trim(p_value), '([a-z0-9])([A-Z])', '\1_\2', 'g'),
    '[^A-Za-z0-9_.-]+', '_', 'g'
  );
  normalized := lower(regexp_replace(normalized, '^[_.-]+|[_.-]+$', '', 'g'));
  if normalized = '' or normalized !~ '^[a-z0-9]' then
    return 'worker_error';
  end if;
  return left(normalized, 120);
end;
$$;

alter function public.motionly_fail_workflow_step(
  uuid, uuid, uuid, text, uuid, text, text, boolean
) rename to motionly_fail_workflow_step_unnormalized;

revoke all on function public.motionly_fail_workflow_step_unnormalized(
  uuid, uuid, uuid, text, uuid, text, text, boolean
) from public, anon, authenticated, service_role;

create or replace function public.motionly_fail_workflow_step(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_step_key text,
  p_claim_token uuid,
  p_error_code text,
  p_error_message text,
  p_retryable boolean default true
)
returns setof jsonb
language sql
security definer
set search_path = public, pg_temp
as $$
  select *
  from public.motionly_fail_workflow_step_unnormalized(
    p_workspace_id,
    p_project_id,
    p_workflow_run_id,
    p_step_key,
    p_claim_token,
    public.motionly_normalize_workflow_error_code(
      coalesce(nullif(trim(p_error_code), ''), 'worker_error')
    ),
    p_error_message,
    p_retryable
  );
$$;

revoke all on function public.motionly_normalize_workflow_error_code(text)
from public, anon, authenticated;
grant execute on function public.motionly_normalize_workflow_error_code(text) to service_role;

revoke all on function public.motionly_fail_workflow_step(
  uuid, uuid, uuid, text, uuid, text, text, boolean
) from public, anon, authenticated;
grant execute on function public.motionly_fail_workflow_step(
  uuid, uuid, uuid, text, uuid, text, text, boolean
) to service_role;
