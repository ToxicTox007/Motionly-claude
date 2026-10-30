-- Worker audit fixes for the daily retention job.
--
-- WJ-14  storage_deletion_queue rows that fail forever were re-claimed forever,
--        with no attempt count, no error record and no terminal state. Claims
--        now count attempts and dead-letter a row after 20 failed claims; the
--        row is kept (with its last error) for an operator to inspect/replay.
-- WJ-11  Two retention runs could execute at once (a client timeout followed by
--        a BullMQ retry), and a crashed run left its row 'running' forever.
--        motionly_begin_retention_run() admits one live run at a time and
--        closes runs abandoned for longer than the stale window.
-- WJ-15  The 30-day transcript redaction replaced raw_payload with {}, which
--        also erased the derived audio_intelligence that planning, proposals and
--        style classification read, and rewrote every old transcript daily.
--        motionly_redact_transcript_raw_payloads() strips only provider keys,
--        keeps the derived ones, and skips rows that are already redacted.

begin;

-- ---------------------------------------------------------------------------
-- WJ-14: bounded retries and a dead-letter state for storage deletions.
-- ---------------------------------------------------------------------------
alter table public.storage_deletion_queue
  add column if not exists attempt_count integer not null default 0,
  add column if not exists last_error_code text,
  add column if not exists last_attempted_at timestamptz,
  add column if not exists dead_lettered_at timestamptz;

alter table public.storage_deletion_queue
  drop constraint if exists storage_deletion_queue_attempt_count_check;
alter table public.storage_deletion_queue
  add constraint storage_deletion_queue_attempt_count_check check (attempt_count >= 0);
alter table public.storage_deletion_queue
  drop constraint if exists storage_deletion_queue_last_error_code_check;
alter table public.storage_deletion_queue
  add constraint storage_deletion_queue_last_error_code_check
  check (last_error_code is null or char_length(last_error_code) between 1 and 120);

create index if not exists storage_deletion_queue_dead_letter_idx
  on public.storage_deletion_queue (dead_lettered_at)
  where dead_lettered_at is not null;

create or replace function public.motionly_claim_storage_deletion_queue(
  p_lease_owner text,
  p_limit integer default 100,
  p_source_table text default null,
  p_before timestamptz default null,
  p_lease_seconds integer default 300
)
returns setof public.storage_deletion_queue
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  -- A row whose lease expired this many times without an acknowledgement is
  -- failing permanently (missing bucket, revoked permission, poisoned path).
  v_max_attempts constant integer := 20;
begin
  if p_lease_owner is null or char_length(p_lease_owner) < 8 or char_length(p_lease_owner) > 160 then
    raise exception 'STORAGE_DELETE_LEASE_OWNER_INVALID' using errcode = '22023';
  end if;
  if p_limit < 1 or p_limit > 250 then
    raise exception 'STORAGE_DELETE_CLAIM_LIMIT_INVALID' using errcode = '22023';
  end if;
  if p_lease_seconds < 30 or p_lease_seconds > 3600 then
    raise exception 'STORAGE_DELETE_LEASE_SECONDS_INVALID' using errcode = '22023';
  end if;
  if p_source_table is not null and (char_length(p_source_table) < 1 or char_length(p_source_table) > 120) then
    raise exception 'STORAGE_DELETE_SOURCE_TABLE_INVALID' using errcode = '22023';
  end if;

  -- Dead-letter exhausted rows that are not currently leased, so they stop
  -- occupying the head of the queue (claims are ordered by queued_at).
  update public.storage_deletion_queue queue
  set dead_lettered_at = clock_timestamp(),
      lease_owner = null,
      lease_token = null,
      lease_expires_at = null
  where queue.dead_lettered_at is null
    and queue.attempt_count >= v_max_attempts
    and (queue.lease_expires_at is null or queue.lease_expires_at <= clock_timestamp())
    and (p_source_table is null or queue.source_table = p_source_table);

  return query
  with candidates as (
    select queue.id
    from public.storage_deletion_queue queue
    where queue.dead_lettered_at is null
      and (p_source_table is null or queue.source_table = p_source_table)
      and (p_before is null or queue.queued_at < p_before)
      and (queue.lease_expires_at is null or queue.lease_expires_at <= clock_timestamp())
    order by queue.queued_at, queue.id
    for update skip locked
    limit p_limit
  )
  update public.storage_deletion_queue queue
  set lease_owner = p_lease_owner,
      lease_token = gen_random_uuid(),
      lease_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds),
      attempt_count = queue.attempt_count + 1,
      last_attempted_at = clock_timestamp()
  from candidates
  where queue.id = candidates.id
  returning queue.*;
end;
$$;

revoke all on function public.motionly_claim_storage_deletion_queue(text, integer, text, timestamptz, integer)
  from public, anon, authenticated;
grant execute on function public.motionly_claim_storage_deletion_queue(text, integer, text, timestamptz, integer)
  to service_role;

-- Record why a claimed row could not be deleted (fenced by its lease token).
create or replace function public.motionly_note_storage_deletion_failure(
  p_queue_id uuid,
  p_lease_token uuid,
  p_error_code text
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  update public.storage_deletion_queue
  set last_error_code = left(coalesce(nullif(btrim(p_error_code), ''), 'storage_delete_failed'), 120)
  where id = p_queue_id
    and lease_token = p_lease_token;
  return found;
end;
$$;

revoke all on function public.motionly_note_storage_deletion_failure(uuid, uuid, text)
  from public, anon, authenticated;
grant execute on function public.motionly_note_storage_deletion_failure(uuid, uuid, text)
  to service_role;

-- ---------------------------------------------------------------------------
-- WJ-11: one live retention run at a time.
-- ---------------------------------------------------------------------------
-- Close the rows of runs that crashed while 'running' (keep only the newest
-- recent one), so the single-run index below can be created.
update public.retention_runs run
set status = 'failed',
    completed_at = coalesce(run.completed_at, now()),
    error_message = coalesce(run.error_message, 'abandoned_run')
where run.status = 'running'
  and (
    run.started_at < now() - interval '6 hours'
    or run.id <> (
      select newest.id
      from public.retention_runs newest
      where newest.status = 'running'
      order by newest.started_at desc, newest.id desc
      limit 1
    )
  );

create unique index if not exists retention_runs_single_running_idx
  on public.retention_runs ((true))
  where status = 'running';

-- Returns the new run id, or NULL when another run is live. A 'running' row
-- older than p_stale_after_seconds belongs to a run that died and is closed.
create or replace function public.motionly_begin_retention_run(
  p_stale_after_seconds integer default 21600
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_run_id uuid;
begin
  if p_stale_after_seconds < 600 or p_stale_after_seconds > 604800 then
    raise exception 'RETENTION_STALE_WINDOW_INVALID' using errcode = '22023';
  end if;
  update public.retention_runs
  set status = 'failed',
      completed_at = now(),
      error_message = coalesce(error_message, 'abandoned_run')
  where status = 'running'
    and started_at < now() - make_interval(secs => p_stale_after_seconds);

  insert into public.retention_runs (status, deleted_counts)
  values ('running', '{}'::jsonb)
  on conflict ((true)) where status = 'running' do nothing
  returning id into v_run_id;
  return v_run_id;
end;
$$;

revoke all on function public.motionly_begin_retention_run(integer) from public, anon, authenticated;
grant execute on function public.motionly_begin_retention_run(integer) to service_role;

-- ---------------------------------------------------------------------------
-- WJ-15: redact provider payloads, keep derived audio intelligence.
-- ---------------------------------------------------------------------------
create or replace function public.motionly_redact_transcript_raw_payloads(
  p_before timestamptz,
  p_limit integer default 500
)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  -- Derived by Motionly (not provider output) and read after the 30-day window
  -- by planning, proposals, style classification and transcript quality.
  v_keep constant text[] := array['audio_intelligence', 'audio_extraction_failed'];
  v_count integer;
begin
  if p_before is null then
    raise exception 'TRANSCRIPT_REDACTION_CUTOFF_REQUIRED' using errcode = '22023';
  end if;
  if p_limit < 1 or p_limit > 5000 then
    raise exception 'TRANSCRIPT_REDACTION_LIMIT_INVALID' using errcode = '22023';
  end if;

  with candidates as (
    select transcript.id
    from public.transcripts transcript
    where transcript.created_at < p_before
      and jsonb_typeof(transcript.raw_payload) = 'object'
      and exists (
        select 1
        from jsonb_object_keys(transcript.raw_payload) as payload_key
        where payload_key <> all (v_keep)
      )
    order by transcript.created_at, transcript.id
    for update skip locked
    limit p_limit
  )
  update public.transcripts transcript
  set raw_payload = (
    select coalesce(jsonb_object_agg(entry.key, entry.value), '{}'::jsonb)
    from jsonb_each(transcript.raw_payload) as entry
    where entry.key = any (v_keep)
  )
  from candidates
  where transcript.id = candidates.id;

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.motionly_redact_transcript_raw_payloads(timestamptz, integer)
  from public, anon, authenticated;
grant execute on function public.motionly_redact_transcript_raw_payloads(timestamptz, integer)
  to service_role;

commit;
