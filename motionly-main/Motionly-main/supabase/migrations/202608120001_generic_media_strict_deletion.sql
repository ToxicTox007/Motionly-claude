-- Task 2: make generic media lifecycle persistence and physical deletion recoverable.
-- PostgreSQL cannot include object storage in its transaction, so this migration records
-- cleanup intent durably before external deletion and moves quota only from the fenced
-- acknowledgement that follows a successful physical delete.

begin;

alter table public.generic_media_upload_sessions
  add column if not exists deletion_requested_at timestamptz;

-- This deliberately stores immutable cleanup evidence without project/workspace foreign
-- keys: project and workspace cascades must not erase a pending external storage delete.
create table public.generic_media_deletion_jobs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  session_id uuid not null unique,
  quota_reservation_id uuid not null,
  quota_action text not null check (quota_action in ('release_reserved', 'delete_committed')),
  final_storage_bucket text not null check (final_storage_bucket <> ''),
  final_storage_path text not null check (
    final_storage_path <> ''
    and final_storage_path !~ E'(^/|//|(^|/)\\.\\.?(/|$)|\\\\)'
  ),
  status text not null default 'pending' check (status in ('pending', 'completed')),
  requested_at timestamptz not null default now(),
  quota_released_at timestamptz,
  completed_at timestamptz,
  check ((status = 'completed') = (completed_at is not null)),
  check (quota_released_at is null or status = 'completed')
);

create index generic_media_deletion_jobs_pending_idx
  on public.generic_media_deletion_jobs (requested_at, id)
  where status = 'pending';

alter table public.storage_deletion_queue
  add column if not exists generic_media_deletion_job_id uuid;

alter table public.storage_deletion_queue
  drop constraint if exists storage_deletion_queue_generic_media_deletion_job_fk,
  add constraint storage_deletion_queue_generic_media_deletion_job_fk
    foreign key (generic_media_deletion_job_id)
    references public.generic_media_deletion_jobs (id)
    on delete restrict;

create index if not exists storage_deletion_queue_generic_media_job_idx
  on public.storage_deletion_queue (generic_media_deletion_job_id)
  where generic_media_deletion_job_id is not null;

-- Creation used to reserve quota, insert the parent, and insert parts in separate REST
-- requests. Keep all three writes in one transaction so a failed request cannot expose a
-- session whose quota or part plan was only partially persisted.
create or replace function public.motionly_create_generic_media_upload_session(
  p_session_id uuid,
  p_reservation_id uuid,
  p_workspace_id uuid,
  p_project_id uuid,
  p_owner_type text,
  p_owner_id uuid,
  p_object_type text,
  p_final_storage_bucket text,
  p_final_storage_path text,
  p_original_filename text,
  p_mime_type text,
  p_declared_size_bytes bigint,
  p_expected_sha256 text,
  p_part_size_bytes bigint,
  p_expires_at timestamptz,
  p_parts jsonb
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_existing public.generic_media_upload_sessions%rowtype;
  v_part_count integer;
  v_expected_part_count bigint;
begin
  if p_session_id is null
     or p_reservation_id is null
     or p_workspace_id is null
     or p_project_id is null
     or p_owner_id is null then
    raise exception using errcode = '22023', message = 'generic media identity is required';
  end if;
  if p_owner_type is null
     or p_object_type is null
     or p_final_storage_bucket is null
     or p_final_storage_bucket = ''
     or p_final_storage_path is null
     or p_final_storage_path = ''
     or p_original_filename is null
     or char_length(p_original_filename) not between 1 and 255
     or p_mime_type is null
     or p_mime_type = '' then
    raise exception using errcode = '22023', message = 'generic media metadata is invalid';
  end if;
  if p_declared_size_bytes <= 0 or p_part_size_bytes <= 0 then
    raise exception using errcode = '22023', message = 'generic media sizes must be positive';
  end if;
  if p_expires_at is null or p_expires_at <= now() then
    raise exception using errcode = '22023', message = 'generic media expiry must be in the future';
  end if;
  if p_expected_sha256 is not null and p_expected_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception using errcode = '22023', message = 'generic media expected hash is invalid';
  end if;
  if p_final_storage_path !~ format('^workspaces/%s/projects/%s/', p_workspace_id, p_project_id)
     or p_final_storage_path ~ E'(^/|//|(^|/)\\.\\.?(/|$)|\\\\)' then
    raise exception using errcode = '22023', message = 'generic media path is outside its project';
  end if;
  if not (
    (p_owner_type = 'stock_asset' and p_object_type = 'broll_asset')
    or (p_owner_type = 'style_profile' and p_object_type = 'style_reference')
  ) then
    raise exception using errcode = '22023', message = 'generic media owner and object type are incompatible';
  end if;
  if p_parts is null or jsonb_typeof(p_parts) <> 'array' then
    raise exception using errcode = '22023', message = 'generic media parts are invalid';
  end if;

  v_part_count := jsonb_array_length(p_parts);
  v_expected_part_count := (p_declared_size_bytes + p_part_size_bytes - 1) / p_part_size_bytes;
  if v_part_count <= 0 or v_expected_part_count > 2147483647 or v_part_count <> v_expected_part_count then
    raise exception using errcode = '22023', message = 'generic media part count is invalid';
  end if;
  if (select count(distinct part.part_number)
      from jsonb_to_recordset(p_parts) as part(
        id uuid,
        part_number integer,
        expected_size_bytes bigint,
        object_path text,
        status text
      )) <> v_part_count
     or exists (
       select 1
       from jsonb_to_recordset(p_parts) as part(
         id uuid,
         part_number integer,
         expected_size_bytes bigint,
         object_path text,
         status text
       )
       where part.id is null
          or part.part_number is null
          or part.part_number < 1
          or part.part_number > v_part_count
          or part.expected_size_bytes is null
          or part.expected_size_bytes <> least(
            p_part_size_bytes,
            p_declared_size_bytes - ((part.part_number - 1)::bigint * p_part_size_bytes)
          )
          or part.object_path is null
          or part.object_path <> format(
            '%s.parts/%s/%s', p_final_storage_path, p_session_id, part.part_number
          )
          or part.status is distinct from 'pending'
     ) then
    raise exception using errcode = '22023', message = 'generic media part plan is invalid';
  end if;

  select * into v_existing
  from public.generic_media_upload_sessions
  where id = p_session_id
  for update;
  if found then
    if v_existing.workspace_id <> p_workspace_id
       or v_existing.project_id <> p_project_id
       or v_existing.owner_type <> p_owner_type
       or v_existing.owner_id <> p_owner_id
       or v_existing.object_type <> p_object_type
       or v_existing.final_storage_bucket <> p_final_storage_bucket
       or v_existing.final_storage_path <> p_final_storage_path
       or v_existing.original_filename <> p_original_filename
       or v_existing.mime_type <> p_mime_type
       or v_existing.declared_size_bytes <> p_declared_size_bytes
       or v_existing.expected_sha256 is distinct from p_expected_sha256
       or v_existing.part_size_bytes <> p_part_size_bytes
       or v_existing.part_count <> v_part_count
       or v_existing.quota_reservation_id <> p_reservation_id
       or v_existing.expires_at <> p_expires_at then
      raise exception using errcode = '22023', message = 'generic media session id was reused with different values';
    end if;
    return true;
  end if;

  perform 1
  from public.projects
  where id = p_project_id and workspace_id = p_workspace_id
  for share;
  if not found then
    raise exception using errcode = '22023', message = 'generic media project is invalid';
  end if;

  if p_owner_type = 'stock_asset' then
    perform 1
    from public.stock_assets
    where id = p_owner_id
      and workspace_id = p_workspace_id
      and project_id = p_project_id
      and provider = 'user_library'
    for update;
    if not found then
      raise exception using errcode = '22023', message = 'generic B-roll media owner is invalid';
    end if;
  else
    perform 1
    from public.phase5_resources
    where id = p_owner_id
      and workspace_id = p_workspace_id
      and project_id = p_project_id
      and kind = 'style_profile'
    for update;
    if not found then
      raise exception using errcode = '22023', message = 'generic style media owner is invalid';
    end if;
  end if;

  if not public.motionly_reserve_storage_quota(
    p_reservation_id,
    p_workspace_id,
    p_declared_size_bytes,
    1,
    p_expires_at
  ) then
    return false;
  end if;

  insert into public.generic_media_upload_sessions (
    id,
    workspace_id,
    project_id,
    owner_type,
    owner_id,
    object_type,
    final_storage_bucket,
    final_storage_path,
    original_filename,
    mime_type,
    declared_size_bytes,
    expected_sha256,
    part_size_bytes,
    part_count,
    quota_reservation_id,
    expires_at
  ) values (
    p_session_id,
    p_workspace_id,
    p_project_id,
    p_owner_type,
    p_owner_id,
    p_object_type,
    p_final_storage_bucket,
    p_final_storage_path,
    p_original_filename,
    p_mime_type,
    p_declared_size_bytes,
    p_expected_sha256,
    p_part_size_bytes,
    v_part_count,
    p_reservation_id,
    p_expires_at
  );

  insert into public.generic_media_upload_session_parts (
    id,
    workspace_id,
    project_id,
    session_id,
    part_number,
    expected_size_bytes,
    object_path,
    status
  )
  select
    part.id,
    p_workspace_id,
    p_project_id,
    p_session_id,
    part.part_number,
    part.expected_size_bytes,
    part.object_path,
    'pending'
  from jsonb_to_recordset(p_parts) as part(
    id uuid,
    part_number integer,
    expected_size_bytes bigint,
    object_path text,
    status text
  );

  return true;
end;
$$;

-- Create or reuse one cleanup job, then attach every temporary part and the
-- authoritative final object to it. The final object is the only row allowed
-- to transition the reservation after every other job object has been deleted.
create or replace function public.motionly_enqueue_generic_media_deletion(
  p_session_id uuid,
  p_workspace_id uuid,
  p_project_id uuid,
  p_quota_reservation_id uuid,
  p_final_storage_bucket text,
  p_final_storage_path text,
  p_quota_action text
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_job public.generic_media_deletion_jobs%rowtype;
begin
  if p_session_id is null
     or p_workspace_id is null
     or p_project_id is null
     or p_quota_reservation_id is null
     or p_final_storage_bucket is null
     or p_final_storage_bucket = ''
     or p_final_storage_path is null
     or p_final_storage_path = ''
     or p_quota_action not in ('release_reserved', 'delete_committed') then
    raise exception using errcode = '22023', message = 'generic media deletion job is invalid';
  end if;

  insert into public.generic_media_deletion_jobs (
    workspace_id,
    project_id,
    session_id,
    quota_reservation_id,
    quota_action,
    final_storage_bucket,
    final_storage_path
  ) values (
    p_workspace_id,
    p_project_id,
    p_session_id,
    p_quota_reservation_id,
    p_quota_action,
    p_final_storage_bucket,
    p_final_storage_path
  )
  on conflict (session_id) do nothing
  returning * into v_job;

  if not found then
    select * into v_job
    from public.generic_media_deletion_jobs
    where session_id = p_session_id
    for update;
  end if;
  if v_job.workspace_id <> p_workspace_id
     or v_job.project_id <> p_project_id
     or v_job.quota_reservation_id <> p_quota_reservation_id
     or v_job.quota_action <> p_quota_action
     or v_job.final_storage_bucket <> p_final_storage_bucket
     or v_job.final_storage_path <> p_final_storage_path then
    raise exception using errcode = '22023', message = 'generic media deletion job conflicts with existing cleanup';
  end if;

  insert into public.storage_deletion_queue (
    storage_bucket,
    storage_path,
    source_table,
    generic_media_deletion_job_id
  )
  select
    p_final_storage_bucket,
    part.object_path,
    'generic_media_deletion',
    v_job.id
  from public.generic_media_upload_session_parts as part
  where part.session_id = p_session_id
  on conflict (storage_bucket, storage_path) do update
  set source_table = excluded.source_table,
      generic_media_deletion_job_id = coalesce(
        public.storage_deletion_queue.generic_media_deletion_job_id,
        excluded.generic_media_deletion_job_id
      ),
      -- A shared queue row must never let an older dispatcher transition a
      -- generic job's reservation before its fenced final acknowledgement.
      quota_reservation_id = null;

  insert into public.storage_deletion_queue (
    storage_bucket,
    storage_path,
    source_table,
    generic_media_deletion_job_id
  ) values (
    p_final_storage_bucket,
    p_final_storage_path,
    'generic_media_deletion',
    v_job.id
  )
  on conflict (storage_bucket, storage_path) do update
  set source_table = excluded.source_table,
      generic_media_deletion_job_id = coalesce(
        public.storage_deletion_queue.generic_media_deletion_job_id,
        excluded.generic_media_deletion_job_id
      ),
      quota_reservation_id = null;

  return v_job.id;
end;
$$;

-- Preserve the established function signature for already deployed callers, but
-- never release quota here. Its old p_delete_final escape hatch is intentionally
-- retained only for wire compatibility: strict cleanup always queues the final path.
create or replace function public.motionly_terminate_generic_media_upload_session(
  p_session_id uuid,
  p_workspace_id uuid,
  p_project_id uuid,
  p_status text,
  p_failure_code text,
  p_delete_final boolean default true
)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_session public.generic_media_upload_sessions%rowtype;
  v_reservation_state text;
begin
  if p_status not in ('aborted', 'expired', 'failed') then
    raise exception using errcode = '22023', message = 'invalid generic media terminal status';
  end if;

  select * into v_session
  from public.generic_media_upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    return null;
  end if;
  if v_session.status = 'completed' or v_session.quota_committed_at is not null then
    raise exception using errcode = '55000', message = 'committed media must use durable deletion';
  end if;

  select state into v_reservation_state
  from public.storage_quota_reservations
  where id = v_session.quota_reservation_id
    and workspace_id = v_session.workspace_id
  for update;
  if not found or v_reservation_state not in ('reserved', 'released') then
    raise exception using errcode = '55000', message = 'generic media quota state is invalid';
  end if;

  if v_session.status in ('active', 'completing') then
    update public.generic_media_upload_sessions
    set status = p_status,
        aborted_at = case when p_status = 'aborted' then now() else aborted_at end,
        failed_at = case when p_status in ('expired', 'failed') then now() else failed_at end,
        failure_code = p_failure_code,
        deletion_requested_at = coalesce(deletion_requested_at, now()),
        updated_at = now()
    where id = v_session.id;
    v_session.status := p_status;
  else
    update public.generic_media_upload_sessions
    set deletion_requested_at = coalesce(deletion_requested_at, now()),
        updated_at = now()
    where id = v_session.id;
  end if;

  update public.object_references
  set status = 'deleted', deleted_at = now(), updated_at = now()
  where workspace_id = v_session.workspace_id
    and project_id = v_session.project_id
    and owner_type = v_session.owner_type
    and owner_id = v_session.owner_id
    and object_type = v_session.object_type
    and storage_bucket = v_session.final_storage_bucket
    and storage_path = v_session.final_storage_path
    and status <> 'deleted';

  perform public.motionly_enqueue_generic_media_deletion(
    v_session.id,
    v_session.workspace_id,
    v_session.project_id,
    v_session.quota_reservation_id,
    v_session.final_storage_bucket,
    v_session.final_storage_path,
    'release_reserved'
  );

  return v_session.status;
end;
$$;

-- Completed media becomes unavailable immediately, but its committed quota remains
-- accounted until retention confirms physical deletion through the acknowledgement RPC.
create or replace function public.motionly_request_generic_media_deletion(
  p_session_id uuid,
  p_workspace_id uuid,
  p_project_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_session public.generic_media_upload_sessions%rowtype;
  v_reservation_state text;
begin
  select * into v_session
  from public.generic_media_upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found
     or v_session.status <> 'completed'
     or v_session.object_reference_id is null
     or v_session.quota_committed_at is null then
    return false;
  end if;

  select state into v_reservation_state
  from public.storage_quota_reservations
  where id = v_session.quota_reservation_id
    and workspace_id = v_session.workspace_id
  for update;
  if not found or v_reservation_state not in ('committed', 'deleted') then
    return false;
  end if;

  update public.generic_media_upload_sessions
  set deletion_requested_at = coalesce(deletion_requested_at, now()),
      updated_at = now()
  where id = v_session.id;

  update public.object_references
  set status = 'deleted', deleted_at = coalesce(deleted_at, now()), updated_at = now()
  where id = v_session.object_reference_id
    and workspace_id = v_session.workspace_id
    and project_id = v_session.project_id
    and owner_type = v_session.owner_type
    and owner_id = v_session.owner_id
    and object_type = v_session.object_type
    and storage_bucket = v_session.final_storage_bucket
    and storage_path = v_session.final_storage_path
    and status <> 'deleted';

  perform public.motionly_enqueue_generic_media_deletion(
    v_session.id,
    v_session.workspace_id,
    v_session.project_id,
    v_session.quota_reservation_id,
    v_session.final_storage_bucket,
    v_session.final_storage_path,
    'delete_committed'
  );
  return true;
end;
$$;

-- A generic queue row is acknowledged only after its external delete succeeds.
-- Part rows are individually acknowledged; the final row waits for every part and
-- performs the single, idempotent quota transition inside the same database transaction.
create or replace function public.motionly_ack_generic_media_deletion_object(
  p_queue_id uuid,
  p_lease_token uuid
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_queue public.storage_deletion_queue%rowtype;
  v_job public.generic_media_deletion_jobs%rowtype;
  v_reservation_state text;
begin
  if p_queue_id is null or p_lease_token is null then
    raise exception using errcode = '22023', message = 'generic media deletion acknowledgement is invalid';
  end if;

  select * into v_queue
  from public.storage_deletion_queue
  where id = p_queue_id
    and lease_token = p_lease_token
    and lease_expires_at > clock_timestamp()
  for update;
  if not found or v_queue.generic_media_deletion_job_id is null then
    return false;
  end if;

  select * into v_job
  from public.generic_media_deletion_jobs
  where id = v_queue.generic_media_deletion_job_id
  for update;
  if not found then
    raise exception using errcode = '55000', message = 'generic media deletion job is missing';
  end if;

  if v_queue.storage_bucket <> v_job.final_storage_bucket
     or v_queue.storage_path <> v_job.final_storage_path then
    delete from public.storage_deletion_queue where id = v_queue.id;
    return true;
  end if;

  if exists (
    select 1
    from public.storage_deletion_queue as pending
    where pending.generic_media_deletion_job_id = v_job.id
      and pending.id <> v_queue.id
  ) then
    -- The physical final delete may already have succeeded. Make the row promptly
    -- reclaimable; retrying a delete is safe and avoids releasing quota early.
    update public.storage_deletion_queue
    set lease_owner = null,
        lease_token = null,
        lease_expires_at = null
    where id = v_queue.id;
    return false;
  end if;

  if v_job.status = 'pending' then
    if v_job.quota_action = 'release_reserved' then
      if not public.motionly_release_storage_quota(v_job.quota_reservation_id) then
        select state into v_reservation_state
        from public.storage_quota_reservations
        where id = v_job.quota_reservation_id;
        if v_reservation_state is null
           and not exists (
             select 1 from public.workspaces where id = v_job.workspace_id
           ) then
          null; -- Workspace cascade already removed accounting; external cleanup still completes.
        else
          raise exception using errcode = '55000', message = 'generic media reserved quota cleanup failed';
        end if;
      end if;
    else
      if not public.motionly_delete_committed_storage_quota(v_job.quota_reservation_id) then
        select state into v_reservation_state
        from public.storage_quota_reservations
        where id = v_job.quota_reservation_id;
        if v_reservation_state is null
           and not exists (
             select 1 from public.workspaces where id = v_job.workspace_id
           ) then
          null;
        else
          raise exception using errcode = '55000', message = 'generic media committed quota cleanup failed';
        end if;
      end if;
    end if;

    update public.generic_media_deletion_jobs
    set status = 'completed',
        quota_released_at = now(),
        completed_at = now()
    where id = v_job.id;
  end if;

  delete from public.storage_deletion_queue where id = v_queue.id;
  return true;
end;
$$;

-- Directly retiring a linked ObjectReference must enter the same durable cleanup
-- path rather than silently orphaning a completed generic media object.
create or replace function public.motionly_queue_generic_media_reference_retirement()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_session public.generic_media_upload_sessions%rowtype;
  v_action text;
begin
  if new.status = 'deleted' and old.status is distinct from 'deleted' then
    for v_session in
      select *
      from public.generic_media_upload_sessions
      where object_reference_id = new.id
      for update
    loop
      v_action := case
        when v_session.status = 'completed' or v_session.quota_committed_at is not null
          then 'delete_committed'
        else 'release_reserved'
      end;
      update public.generic_media_upload_sessions
      set deletion_requested_at = coalesce(deletion_requested_at, now()),
          updated_at = now()
      where id = v_session.id;
      perform public.motionly_enqueue_generic_media_deletion(
        v_session.id,
        v_session.workspace_id,
        v_session.project_id,
        v_session.quota_reservation_id,
        v_session.final_storage_bucket,
        v_session.final_storage_path,
        v_action
      );
    end loop;
  end if;
  return new;
end;
$$;

drop trigger if exists object_references_queue_generic_media_retirement on public.object_references;
create trigger object_references_queue_generic_media_retirement
after update of status on public.object_references
for each row execute function public.motionly_queue_generic_media_reference_retirement();

-- A project cascade invokes this BEFORE DELETE trigger while the session and parts
-- are still readable. The job/queue have no project foreign keys and therefore remain
-- available to retention after the relational owner is gone.
create or replace function public.motionly_queue_deleted_generic_media_session()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform public.motionly_enqueue_generic_media_deletion(
    old.id,
    old.workspace_id,
    old.project_id,
    old.quota_reservation_id,
    old.final_storage_bucket,
    old.final_storage_path,
    case
      when old.status = 'completed' or old.quota_committed_at is not null
        then 'delete_committed'
      else 'release_reserved'
    end
  );
  return old;
end;
$$;

drop trigger if exists generic_media_upload_sessions_queue_storage_delete on public.generic_media_upload_sessions;
create trigger generic_media_upload_sessions_queue_storage_delete
before delete on public.generic_media_upload_sessions
for each row execute function public.motionly_queue_deleted_generic_media_session();

-- Keep generic sessions and their pending durable cleanup jobs out of the generic
-- orphan-reservation sweeper. Their quota is released only by the final acknowledgement.
create or replace function public.motionly_expire_storage_quota_reservations()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_upload record;
  v_reservation record;
  v_count integer := 0;
begin
  for v_upload in
    select
      upload.id,
      upload.workspace_id,
      upload.project_id,
      upload.storage_bucket,
      upload.storage_path,
      upload.source_object_reference_id,
      upload.legacy_quota_reservation_id,
      reservation.state as reservation_state
    from public.uploaded_videos as upload
    left join public.storage_quota_reservations as reservation
      on reservation.id = upload.legacy_quota_reservation_id
     and reservation.workspace_id = upload.workspace_id
    where upload.legacy_quota_reservation_id is not null
      and upload.legacy_upload_expires_at <= now()
      and upload.status not in ('processing', 'ready')
      and coalesce(reservation.state, 'released') <> 'committed'
    for update of upload skip locked
  loop
    if v_upload.reservation_state = 'reserved' then
      perform public.motionly_release_storage_quota(
        v_upload.legacy_quota_reservation_id
      );
    end if;

    update public.object_references
    set status = 'deleted', deleted_at = now(), updated_at = now()
    where workspace_id = v_upload.workspace_id
      and project_id = v_upload.project_id
      and owner_type = 'uploaded_video'
      and owner_id = v_upload.id
      and storage_bucket = v_upload.storage_bucket
      and storage_path = v_upload.storage_path
      and status <> 'deleted';

    update public.uploaded_videos
    set status = 'failed',
        source_object_reference_id = null,
        error_code = coalesce(error_code, 'legacy_upload_expired'),
        error_message = coalesce(
          error_message,
          'Legacy upload completion expired before it was finalized.'
        ),
        legacy_quota_reservation_id = null,
        legacy_upload_expires_at = null,
        updated_at = now()
    where id = v_upload.id;

    insert into public.storage_deletion_queue (
      storage_bucket,
      storage_path,
      source_table
    ) values (
      v_upload.storage_bucket,
      v_upload.storage_path,
      'legacy_upload_expiry'
    )
    on conflict (storage_bucket, storage_path) do nothing;

    v_count := v_count + 1;
  end loop;

  for v_reservation in
    select reservation.id
    from public.storage_quota_reservations as reservation
    where reservation.state = 'reserved'
      and reservation.expires_at <= now()
      and not exists (
        select 1
        from public.upload_sessions as session
        where session.quota_reservation_id = reservation.id
      )
      and not exists (
        select 1
        from public.uploaded_videos as upload
        where upload.legacy_quota_reservation_id = reservation.id
      )
      and not exists (
        select 1
        from public.generic_media_upload_sessions as session
        where session.quota_reservation_id = reservation.id
      )
      and not exists (
        select 1
        from public.generic_media_deletion_jobs as job
        where job.quota_reservation_id = reservation.id
          and job.status = 'pending'
      )
    for update of reservation skip locked
  loop
    if public.motionly_release_storage_quota(v_reservation.id) then
      v_count := v_count + 1;
    end if;
  end loop;
  return v_count;
end;
$$;

alter table public.generic_media_deletion_jobs enable row level security;
revoke all privileges on table public.generic_media_deletion_jobs from public, anon, authenticated;
grant select on table public.generic_media_deletion_jobs to authenticated;
grant all privileges on table public.generic_media_deletion_jobs to service_role;
create policy "tenant members read generic media deletion jobs"
  on public.generic_media_deletion_jobs for select to authenticated
  using (public.is_workspace_member(workspace_id));

revoke all on function public.motionly_create_generic_media_upload_session(
  uuid, uuid, uuid, uuid, text, uuid, text, text, text, text, text, bigint, text, bigint, timestamptz, jsonb
) from public, anon, authenticated;
revoke all on function public.motionly_enqueue_generic_media_deletion(
  uuid, uuid, uuid, uuid, text, text, text
) from public, anon, authenticated;
revoke all on function public.motionly_request_generic_media_deletion(uuid, uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_ack_generic_media_deletion_object(uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_queue_generic_media_reference_retirement()
  from public, anon, authenticated;
revoke all on function public.motionly_queue_deleted_generic_media_session()
  from public, anon, authenticated;
revoke all on function public.motionly_terminate_generic_media_upload_session(
  uuid, uuid, uuid, text, text, boolean
) from public, anon, authenticated;
revoke all on function public.motionly_expire_generic_media_upload_sessions()
  from public, anon, authenticated;
revoke all on function public.motionly_expire_storage_quota_reservations()
  from public, anon, authenticated;

grant execute on function public.motionly_create_generic_media_upload_session(
  uuid, uuid, uuid, uuid, text, uuid, text, text, text, text, text, bigint, text, bigint, timestamptz, jsonb
) to service_role;
grant execute on function public.motionly_request_generic_media_deletion(uuid, uuid, uuid)
  to service_role;
grant execute on function public.motionly_ack_generic_media_deletion_object(uuid, uuid)
  to service_role;
grant execute on function public.motionly_terminate_generic_media_upload_session(
  uuid, uuid, uuid, text, text, boolean
) to service_role;
grant execute on function public.motionly_expire_generic_media_upload_sessions()
  to service_role;
grant execute on function public.motionly_expire_storage_quota_reservations()
  to service_role;

-- Generic cleanup rows are claimed by the shared lease RPC, but must only be
-- acknowledged by the fenced generic-media RPC. This also protects a rolling
-- deployment if an older retention process runs briefly after this migration.
create or replace function public.motionly_ack_storage_deletion_queue(
  p_queue_id uuid,
  p_lease_token uuid
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if p_queue_id is null or p_lease_token is null then
    raise exception 'STORAGE_DELETE_ACK_ARGUMENT_INVALID' using errcode = '22023';
  end if;

  delete from public.storage_deletion_queue
  where id = p_queue_id
    and lease_token = p_lease_token
    and lease_expires_at > clock_timestamp()
    and generic_media_deletion_job_id is null;
  return found;
end;
$$;

revoke all on function public.motionly_ack_storage_deletion_queue(uuid, uuid)
  from public, anon, authenticated;
grant execute on function public.motionly_ack_storage_deletion_queue(uuid, uuid)
  to service_role;

-- ObjectReference retirement locks its reference before the linked session in
-- its AFTER trigger. All session-driven reference mutations use that same
-- order, preventing reference/session lock-order cycles.
create or replace function public.motionly_finalize_generic_media_upload_session(
  p_session_id uuid,
  p_workspace_id uuid,
  p_project_id uuid,
  p_reference_id uuid,
  p_verified_size_bytes bigint,
  p_verified_sha256 text
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_session public.generic_media_upload_sessions%rowtype;
  v_reservation public.storage_quota_reservations%rowtype;
begin
  -- Establish the session identity without holding its row lock while the
  -- durable ObjectReference is locked first.
  select * into v_session
  from public.generic_media_upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id;
  if not found then
    return false;
  end if;

  perform 1
  from public.object_references as reference
  where reference.id = p_reference_id
    and reference.workspace_id = p_workspace_id
    and reference.project_id = p_project_id
  for update;
  if not found then
    return false;
  end if;

  select * into v_session
  from public.generic_media_upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    return false;
  end if;
  if v_session.status = 'completed' then
    return v_session.object_reference_id = p_reference_id;
  end if;
  if v_session.status <> 'completing'
     or p_verified_size_bytes is null
     or p_verified_size_bytes <> v_session.declared_size_bytes
     or p_verified_sha256 is null
     or p_verified_sha256 !~ '^[0-9a-f]{64}$'
     or not exists (
       select 1
       from public.object_references as reference
       where reference.id = p_reference_id
         and reference.workspace_id = v_session.workspace_id
         and reference.project_id = v_session.project_id
         and reference.owner_type = v_session.owner_type
         and reference.owner_id = v_session.owner_id
         and reference.object_type = v_session.object_type
         and reference.storage_bucket = v_session.final_storage_bucket
         and reference.storage_path = v_session.final_storage_path
         and reference.size_bytes = p_verified_size_bytes
         and reference.sha256 = p_verified_sha256
         and reference.status = 'active'
     ) then
    return false;
  end if;

  select * into v_reservation
  from public.storage_quota_reservations
  where id = v_session.quota_reservation_id
    and workspace_id = v_session.workspace_id
  for update;
  if not found or v_reservation.state in ('released', 'deleted') then
    return false;
  end if;
  if v_reservation.state = 'reserved' and v_reservation.expires_at <= now() then
    return false;
  end if;
  if not public.motionly_commit_storage_quota(v_session.quota_reservation_id) then
    return false;
  end if;

  update public.generic_media_upload_sessions
  set status = 'completed',
      quota_committed_at = coalesce(quota_committed_at, now()),
      object_reference_id = p_reference_id,
      verified_size_bytes = p_verified_size_bytes,
      verified_sha256 = p_verified_sha256,
      completed_at = coalesce(completed_at, now()),
      failure_code = null,
      updated_at = now()
  where id = v_session.id;

  insert into public.storage_deletion_queue (storage_bucket, storage_path, source_table)
  select v_session.final_storage_bucket, part.object_path, 'generic_media_upload_session_parts'
  from public.generic_media_upload_session_parts as part
  where part.session_id = v_session.id
  on conflict (storage_bucket, storage_path) do nothing;
  return true;
end;
$$;

create or replace function public.motionly_terminate_generic_media_upload_session(
  p_session_id uuid,
  p_workspace_id uuid,
  p_project_id uuid,
  p_status text,
  p_failure_code text,
  p_delete_final boolean default true
)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_session public.generic_media_upload_sessions%rowtype;
  v_reservation_state text;
begin
  if p_status not in ('aborted', 'expired', 'failed') then
    raise exception using errcode = '22023', message = 'invalid generic media terminal status';
  end if;

  -- Read immutable owner/path data first, then lock matching references before
  -- acquiring the session row to match direct ObjectReference retirement.
  select * into v_session
  from public.generic_media_upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id;
  if not found then
    return null;
  end if;

  perform 1
  from public.object_references
  where workspace_id = v_session.workspace_id
    and project_id = v_session.project_id
    and owner_type = v_session.owner_type
    and owner_id = v_session.owner_id
    and object_type = v_session.object_type
    and storage_bucket = v_session.final_storage_bucket
    and storage_path = v_session.final_storage_path
  for update;

  select * into v_session
  from public.generic_media_upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    return null;
  end if;
  if v_session.status = 'completed' or v_session.quota_committed_at is not null then
    raise exception using errcode = '55000', message = 'committed media must use durable deletion';
  end if;

  select state into v_reservation_state
  from public.storage_quota_reservations
  where id = v_session.quota_reservation_id
    and workspace_id = v_session.workspace_id
  for update;
  if not found or v_reservation_state not in ('reserved', 'released') then
    raise exception using errcode = '55000', message = 'generic media quota state is invalid';
  end if;

  if v_session.status in ('active', 'completing') then
    update public.generic_media_upload_sessions
    set status = p_status,
        aborted_at = case when p_status = 'aborted' then now() else aborted_at end,
        failed_at = case when p_status in ('expired', 'failed') then now() else failed_at end,
        failure_code = p_failure_code,
        deletion_requested_at = coalesce(deletion_requested_at, now()),
        updated_at = now()
    where id = v_session.id;
    v_session.status := p_status;
  else
    update public.generic_media_upload_sessions
    set deletion_requested_at = coalesce(deletion_requested_at, now()),
        updated_at = now()
    where id = v_session.id;
  end if;

  update public.object_references
  set status = 'deleted', deleted_at = now(), updated_at = now()
  where workspace_id = v_session.workspace_id
    and project_id = v_session.project_id
    and owner_type = v_session.owner_type
    and owner_id = v_session.owner_id
    and object_type = v_session.object_type
    and storage_bucket = v_session.final_storage_bucket
    and storage_path = v_session.final_storage_path
    and status <> 'deleted';

  perform public.motionly_enqueue_generic_media_deletion(
    v_session.id,
    v_session.workspace_id,
    v_session.project_id,
    v_session.quota_reservation_id,
    v_session.final_storage_bucket,
    v_session.final_storage_path,
    'release_reserved'
  );

  return v_session.status;
end;
$$;

create or replace function public.motionly_request_generic_media_deletion(
  p_session_id uuid,
  p_workspace_id uuid,
  p_project_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_session public.generic_media_upload_sessions%rowtype;
  v_object_reference_id uuid;
  v_reservation_state text;
begin
  -- Read the immutable linked reference id without holding the session row;
  -- then lock the reference before re-locking and re-validating the session.
  select * into v_session
  from public.generic_media_upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id;
  if not found
     or v_session.status <> 'completed'
     or v_session.object_reference_id is null
     or v_session.quota_committed_at is null then
    return false;
  end if;
  v_object_reference_id := v_session.object_reference_id;

  perform 1
  from public.object_references
  where id = v_object_reference_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    return false;
  end if;

  select * into v_session
  from public.generic_media_upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found
     or v_session.status <> 'completed'
     or v_session.object_reference_id is distinct from v_object_reference_id
     or v_session.quota_committed_at is null then
    return false;
  end if;

  select state into v_reservation_state
  from public.storage_quota_reservations
  where id = v_session.quota_reservation_id
    and workspace_id = v_session.workspace_id
  for update;
  if not found or v_reservation_state not in ('committed', 'deleted') then
    return false;
  end if;

  update public.generic_media_upload_sessions
  set deletion_requested_at = coalesce(deletion_requested_at, now()),
      updated_at = now()
  where id = v_session.id;

  update public.object_references
  set status = 'deleted', deleted_at = coalesce(deleted_at, now()), updated_at = now()
  where id = v_session.object_reference_id
    and workspace_id = v_session.workspace_id
    and project_id = v_session.project_id
    and owner_type = v_session.owner_type
    and owner_id = v_session.owner_id
    and object_type = v_session.object_type
    and storage_bucket = v_session.final_storage_bucket
    and storage_path = v_session.final_storage_path
    and status <> 'deleted';

  perform public.motionly_enqueue_generic_media_deletion(
    v_session.id,
    v_session.workspace_id,
    v_session.project_id,
    v_session.quota_reservation_id,
    v_session.final_storage_bucket,
    v_session.final_storage_path,
    'delete_committed'
  );
  return true;
end;
$$;

create or replace function public.motionly_expire_generic_media_upload_sessions()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_session record;
  v_count integer := 0;
begin
  -- Do not pre-lock candidate sessions here: terminate() must lock an existing
  -- ObjectReference first to preserve the lifecycle-wide lock order.
  for v_session in
    select id, workspace_id, project_id
    from public.generic_media_upload_sessions
    where status in ('active', 'completing')
      and quota_committed_at is null
      and expires_at <= now()
  loop
    if public.motionly_terminate_generic_media_upload_session(
      v_session.id,
      v_session.workspace_id,
      v_session.project_id,
      'expired',
      'media_upload_session_expired',
      true
    ) = 'expired' then
      v_count := v_count + 1;
    end if;
  end loop;
  return v_count;
end;
$$;

commit;
