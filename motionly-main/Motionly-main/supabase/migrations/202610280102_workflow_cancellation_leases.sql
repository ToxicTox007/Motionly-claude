-- Worker audit WJ-05: a project-delete cancellation was marked dispatched when
-- it was claimed, before Temporal confirmed it. One failed Temporal call (the
-- dispatcher logs and moves on) therefore lost the cancellation for good, and
-- the orphaned workflow waited out its 30-day execution timeout.
--
-- Claims are now leases: motionly_claim_workflow_cancellations hands out rows
-- that are not completed and whose lease is free or expired, counting attempts.
-- The dispatcher acknowledges success with motionly_complete_workflow_cancellation
-- and reports a failure with motionly_release_workflow_cancellation (backoff).
-- After 20 failed attempts a request is dead-lettered and kept for operators.
-- Rows dispatched under the old semantics carry no completion record, so they
-- are re-sent once; Temporal cancellation is idempotent (not found = done).

begin;

alter table public.workflow_cancellation_requests
  add column if not exists lease_expires_at timestamptz,
  add column if not exists attempt_count integer not null default 0,
  add column if not exists completed_at timestamptz,
  add column if not exists dead_lettered_at timestamptz,
  add column if not exists last_error_code text;

alter table public.workflow_cancellation_requests
  drop constraint if exists workflow_cancellation_requests_attempt_count_check;
alter table public.workflow_cancellation_requests
  add constraint workflow_cancellation_requests_attempt_count_check check (attempt_count >= 0);
alter table public.workflow_cancellation_requests
  drop constraint if exists workflow_cancellation_requests_last_error_code_check;
alter table public.workflow_cancellation_requests
  add constraint workflow_cancellation_requests_last_error_code_check
  check (last_error_code is null or char_length(last_error_code) between 1 and 120);

drop index if exists public.workflow_cancellation_requests_pending_idx;
create index if not exists workflow_cancellation_requests_open_idx
  on public.workflow_cancellation_requests (created_at)
  where completed_at is null and dead_lettered_at is null;

create or replace function public.motionly_claim_workflow_cancellations(
  p_limit integer default 50
)
returns setof public.workflow_cancellation_requests
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_max_attempts constant integer := 20;
  v_lease constant interval := interval '5 minutes';
begin
  if p_limit is null or p_limit < 1 or p_limit > 200 then
    raise exception 'WORKFLOW_CANCELLATION_LIMIT_INVALID' using errcode = '22023';
  end if;

  update public.workflow_cancellation_requests request
  set dead_lettered_at = now(),
      lease_expires_at = null
  where request.completed_at is null
    and request.dead_lettered_at is null
    and request.attempt_count >= v_max_attempts
    and (request.lease_expires_at is null or request.lease_expires_at <= now());

  return query
  update public.workflow_cancellation_requests r
  set dispatched_at = now(),
      lease_expires_at = now() + v_lease,
      attempt_count = r.attempt_count + 1
  where r.id in (
    select c.id
    from public.workflow_cancellation_requests c
    where c.completed_at is null
      and c.dead_lettered_at is null
      and (c.lease_expires_at is null or c.lease_expires_at <= now())
    order by c.created_at
    for update skip locked
    limit p_limit
  )
  returning r.*;
end;
$$;

revoke all on function public.motionly_claim_workflow_cancellations(integer) from public, anon, authenticated;
grant execute on function public.motionly_claim_workflow_cancellations(integer) to service_role;

-- Temporal confirmed the cancellation (or the workflow no longer exists).
create or replace function public.motionly_complete_workflow_cancellation(
  p_request_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  update public.workflow_cancellation_requests
  set completed_at = coalesce(completed_at, now()),
      lease_expires_at = null,
      last_error_code = null
  where id = p_request_id;
  return found;
end;
$$;

revoke all on function public.motionly_complete_workflow_cancellation(uuid) from public, anon, authenticated;
grant execute on function public.motionly_complete_workflow_cancellation(uuid) to service_role;

-- The Temporal call failed: make the request claimable again after a backoff.
create or replace function public.motionly_release_workflow_cancellation(
  p_request_id uuid,
  p_error_code text,
  p_retry_after_seconds integer default 30
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if p_retry_after_seconds is null or p_retry_after_seconds < 1 or p_retry_after_seconds > 3600 then
    raise exception 'WORKFLOW_CANCELLATION_BACKOFF_INVALID' using errcode = '22023';
  end if;
  update public.workflow_cancellation_requests
  set lease_expires_at = now() + make_interval(secs => p_retry_after_seconds),
      last_error_code = left(coalesce(nullif(btrim(p_error_code), ''), 'temporal_cancel_failed'), 120)
  where id = p_request_id
    and completed_at is null;
  return found;
end;
$$;

revoke all on function public.motionly_release_workflow_cancellation(uuid, text, integer)
  from public, anon, authenticated;
grant execute on function public.motionly_release_workflow_cancellation(uuid, text, integer)
  to service_role;

commit;
