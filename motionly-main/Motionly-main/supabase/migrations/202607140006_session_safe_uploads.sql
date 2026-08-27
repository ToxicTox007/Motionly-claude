-- Task 6: session-safe multipart uploads, atomic storage quotas, and durable media refs.
-- This is a forward-only migration. Task 7 workflow/outbox concerns are intentionally excluded.

-- Storage accounting is split between durable usage and reservations held by active work.
create table public.workspace_storage_quotas (
  workspace_id uuid primary key references public.workspaces(id) on delete cascade,
  used_bytes bigint not null default 0 check (used_bytes >= 0),
  reserved_bytes bigint not null default 0 check (reserved_bytes >= 0),
  used_object_count bigint not null default 0 check (used_object_count >= 0),
  reserved_object_count bigint not null default 0 check (reserved_object_count >= 0),
  max_bytes bigint not null default 5368709120 check (max_bytes > 0),
  max_objects bigint not null default 100 check (max_objects > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (used_bytes + reserved_bytes <= max_bytes),
  check (used_object_count + reserved_object_count <= max_objects)
);

create table public.storage_quota_reservations (
  id uuid primary key,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  byte_amount bigint not null check (byte_amount > 0),
  object_amount integer not null check (object_amount > 0),
  state text not null default 'reserved'
    check (state in ('reserved', 'committed', 'released', 'deleted')),
  expires_at timestamptz not null,
  committed_at timestamptz,
  released_at timestamptz,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, id),
  check ((state in ('committed', 'deleted')) = (committed_at is not null)),
  check ((state = 'released') = (released_at is not null)),
  check ((state = 'deleted') = (deleted_at is not null))
);

create index storage_quota_reservations_workspace_state_idx
  on public.storage_quota_reservations (workspace_id, state, expires_at);

-- A quota reservation is created only when both byte and object capacity are available.
-- Retrying the same id and immutable payload returns the original outcome without double-counting.
create or replace function public.motionly_reserve_storage_quota(
  p_reservation_id uuid,
  p_workspace_id uuid,
  p_byte_amount bigint,
  p_object_amount integer,
  p_expires_at timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_existing public.storage_quota_reservations%rowtype;
  v_quota public.workspace_storage_quotas%rowtype;
begin
  if p_reservation_id is null or p_workspace_id is null then
    raise exception using errcode = '22023', message = 'reservation and workspace ids are required';
  end if;
  if p_byte_amount <= 0 or p_object_amount <= 0 then
    raise exception using errcode = '22023', message = 'quota amounts must be positive';
  end if;
  if p_expires_at is null or p_expires_at <= now() then
    raise exception using errcode = '22023', message = 'reservation expiry must be in the future';
  end if;

  insert into public.workspace_storage_quotas (workspace_id)
  values (p_workspace_id)
  on conflict (workspace_id) do nothing;

  select * into v_quota
  from public.workspace_storage_quotas
  where workspace_id = p_workspace_id
  for update;

  select * into v_existing
  from public.storage_quota_reservations
  where id = p_reservation_id;

  if found then
    if v_existing.workspace_id <> p_workspace_id
       or v_existing.byte_amount <> p_byte_amount
       or v_existing.object_amount <> p_object_amount
       or v_existing.expires_at <> p_expires_at then
      raise exception using errcode = '22023', message = 'reservation id was reused with different values';
    end if;
    return v_existing.state in ('reserved', 'committed');
  end if;

  if v_quota.used_bytes + v_quota.reserved_bytes + p_byte_amount > v_quota.max_bytes
     or v_quota.used_object_count + v_quota.reserved_object_count + p_object_amount
        > v_quota.max_objects then
    return false;
  end if;

  insert into public.storage_quota_reservations (
    id, workspace_id, byte_amount, object_amount, expires_at
  ) values (
    p_reservation_id, p_workspace_id, p_byte_amount, p_object_amount, p_expires_at
  );

  update public.workspace_storage_quotas
  set reserved_bytes = reserved_bytes + p_byte_amount,
      reserved_object_count = reserved_object_count + p_object_amount,
      updated_at = now()
  where workspace_id = p_workspace_id;

  return true;
end;
$$;

-- Commit moves one reservation from reserved to used. A committed retry is a no-op.
create or replace function public.motionly_commit_storage_quota(p_reservation_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_reservation public.storage_quota_reservations%rowtype;
begin
  if p_reservation_id is null then
    raise exception using errcode = '22023', message = 'reservation id is required';
  end if;

  select * into v_reservation
  from public.storage_quota_reservations
  where id = p_reservation_id
  for update;

  if not found then
    return false;
  end if;
  if v_reservation.state = 'committed' then
    return true;
  end if;
  if v_reservation.state in ('released', 'deleted') then
    return false;
  end if;

  update public.workspace_storage_quotas
  set reserved_bytes = reserved_bytes - v_reservation.byte_amount,
      reserved_object_count = reserved_object_count - v_reservation.object_amount,
      used_bytes = used_bytes + v_reservation.byte_amount,
      used_object_count = used_object_count + v_reservation.object_amount,
      updated_at = now()
  where workspace_id = v_reservation.workspace_id
    and reserved_bytes >= v_reservation.byte_amount
    and reserved_object_count >= v_reservation.object_amount;

  if not found then
    raise exception using errcode = '23514', message = 'quota reservation accounting is inconsistent';
  end if;

  update public.storage_quota_reservations
  set state = 'committed', committed_at = now(), updated_at = now()
  where id = p_reservation_id;

  return true;
end;
$$;

-- Release removes only a held reservation. A released retry is a no-op; committed usage
-- cannot accidentally be released through this reservation transition.
create or replace function public.motionly_release_storage_quota(p_reservation_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_reservation public.storage_quota_reservations%rowtype;
begin
  if p_reservation_id is null then
    raise exception using errcode = '22023', message = 'reservation id is required';
  end if;

  select * into v_reservation
  from public.storage_quota_reservations
  where id = p_reservation_id
  for update;

  if not found then
    return false;
  end if;
  if v_reservation.state = 'released' then
    return true;
  end if;
  if v_reservation.state in ('committed', 'deleted') then
    return false;
  end if;

  update public.workspace_storage_quotas
  set reserved_bytes = reserved_bytes - v_reservation.byte_amount,
      reserved_object_count = reserved_object_count - v_reservation.object_amount,
      updated_at = now()
  where workspace_id = v_reservation.workspace_id
    and reserved_bytes >= v_reservation.byte_amount
    and reserved_object_count >= v_reservation.object_amount;

  if not found then
    raise exception using errcode = '23514', message = 'quota reservation accounting is inconsistent';
  end if;

  update public.storage_quota_reservations
  set state = 'released', released_at = now(), updated_at = now()
  where id = p_reservation_id;

  return true;
end;
$$;

-- Physical deletion decrements committed byte/object usage exactly once while
-- preserving an auditable terminal reservation state.
create or replace function public.motionly_delete_committed_storage_quota(
  p_reservation_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_reservation public.storage_quota_reservations%rowtype;
begin
  select * into v_reservation
  from public.storage_quota_reservations
  where id = p_reservation_id
  for update;

  if not found then
    return false;
  end if;
  if v_reservation.state = 'deleted' then
    return true;
  end if;
  if v_reservation.state <> 'committed' then
    return false;
  end if;

  update public.workspace_storage_quotas
  set used_bytes = used_bytes - v_reservation.byte_amount,
      used_object_count = used_object_count - v_reservation.object_amount,
      updated_at = now()
  where workspace_id = v_reservation.workspace_id
    and used_bytes >= v_reservation.byte_amount
    and used_object_count >= v_reservation.object_amount;

  if not found then
    raise exception using errcode = '23514', message = 'committed quota accounting is inconsistent';
  end if;

  update public.storage_quota_reservations
  set state = 'deleted', deleted_at = now(), updated_at = now()
  where id = p_reservation_id;
  return true;
end;
$$;

create table public.upload_sessions (
  id uuid primary key,
  workspace_id uuid not null,
  project_id uuid not null,
  uploaded_video_id uuid not null,
  final_storage_bucket text not null check (final_storage_bucket <> ''),
  final_storage_path text not null check (
    final_storage_path <> ''
    and final_storage_path !~ E'(^/|//|(^|/)\\.\\.?(/|$)|\\\\)'
  ),
  original_filename text not null check (char_length(original_filename) between 1 and 255),
  mime_type text not null check (mime_type <> ''),
  declared_size_bytes bigint not null check (declared_size_bytes > 0),
  expected_sha256 text check (expected_sha256 ~ '^[0-9a-f]{64}$'),
  verified_size_bytes bigint check (verified_size_bytes > 0),
  verified_sha256 text check (verified_sha256 ~ '^[0-9a-f]{64}$'),
  part_size_bytes bigint not null check (part_size_bytes > 0),
  part_count integer not null check (part_count > 0),
  status text not null default 'active'
    check (status in ('active', 'completing', 'completed', 'aborted', 'expired', 'failed')),
  quota_reservation_id uuid not null,
  quota_committed_at timestamptz,
  expires_at timestamptz not null,
  completed_at timestamptz,
  aborted_at timestamptz,
  failed_at timestamptz,
  failure_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, project_id, id),
  constraint task6_upload_sessions_video_tenant_fk
    foreign key (workspace_id, project_id, uploaded_video_id)
    references public.uploaded_videos (workspace_id, project_id, id)
    on delete cascade,
  constraint task6_upload_sessions_reservation_tenant_fk
    foreign key (workspace_id, quota_reservation_id)
    references public.storage_quota_reservations (workspace_id, id)
    on delete restrict,
  check (verified_size_bytes is null or verified_size_bytes = declared_size_bytes),
  check (status <> 'completed' or (
    completed_at is not null and verified_size_bytes is not null and verified_sha256 is not null
  ))
);

create index upload_sessions_video_idx
  on public.upload_sessions (workspace_id, project_id, uploaded_video_id);
create index upload_sessions_active_expiry_idx
  on public.upload_sessions (expires_at) where status = 'active';

create table public.upload_session_parts (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  session_id uuid not null,
  part_number integer not null check (part_number > 0),
  expected_size_bytes bigint not null check (expected_size_bytes > 0),
  object_path text not null check (
    object_path <> '' and object_path !~ E'(^/|//|(^|/)\\.\\.?(/|$)|\\\\)'
  ),
  status text not null default 'pending' check (status in ('pending', 'uploaded')),
  verified_size_bytes bigint check (verified_size_bytes > 0),
  etag text check (etag is null or char_length(etag) between 1 and 256),
  uploaded_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (session_id, part_number),
  unique (session_id, object_path),
  constraint task6_upload_parts_session_tenant_fk
    foreign key (workspace_id, project_id, session_id)
    references public.upload_sessions (workspace_id, project_id, id)
    on delete cascade,
  check ((status = 'uploaded') = (uploaded_at is not null)),
  check (verified_size_bytes is null or verified_size_bytes = expected_size_bytes)
);

create index upload_session_parts_session_status_idx
  on public.upload_session_parts (session_id, status, part_number);

create table public.object_references (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  storage_bucket text not null check (
    storage_bucket ~ '^[a-z0-9][a-z0-9._-]{0,62}$'
  ),
  storage_path text not null check (
    storage_path <> '' and storage_path !~ E'(^/|//|(^|/)\\.\\.?(/|$)|\\\\)'
  ),
  owner_type text not null check (owner_type in (
    'uploaded_video', 'export', 'stock_asset', 'music_track',
    'segmentation_matte', 'video_manifest', 'project'
  )),
  owner_id uuid not null,
  object_type text not null check (object_type in (
    'source_video', 'thumbnail', 'export', 'broll_asset', 'music_asset',
    'matte', 'temp_preview', 'audio_stem'
  )),
  mime_type text,
  size_bytes bigint check (size_bytes is null or size_bytes > 0),
  sha256 text check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$'),
  status text not null default 'active'
    check (status in ('active', 'pending_delete', 'deleted')),
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (storage_bucket, storage_path),
  unique (workspace_id, project_id, id),
  constraint task6_object_references_project_tenant_fk
    foreign key (workspace_id, project_id)
    references public.projects (workspace_id, id)
    on delete cascade,
  check ((status = 'deleted') = (deleted_at is not null))
);

create index object_references_owner_idx
  on public.object_references (workspace_id, project_id, owner_type, owner_id);
create index object_references_project_type_idx
  on public.object_references (workspace_id, project_id, object_type, status);

create table public.signed_url_grants (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  object_reference_id uuid not null,
  purpose text not null check (purpose in (
    'browser_preview', 'browser_download', 'worker_processing', 'export_download'
  )),
  principal_type text not null check (principal_type in ('user', 'worker')),
  principal_id uuid,
  issued_at timestamptz not null default now(),
  expires_at timestamptz not null,
  ttl_seconds integer not null check (ttl_seconds > 0 and ttl_seconds <= 7200),
  request_context jsonb not null default '{}'::jsonb,
  constraint task6_signed_grants_reference_tenant_fk
    foreign key (workspace_id, project_id, object_reference_id)
    references public.object_references (workspace_id, project_id, id)
    on delete cascade,
  check (expires_at > issued_at),
  check ((principal_type = 'user') = (principal_id is not null))
);

create index signed_url_grants_reference_issued_idx
  on public.signed_url_grants (object_reference_id, issued_at desc);
create index signed_url_grants_expiry_idx
  on public.signed_url_grants (expires_at);

-- Keep verified source identity on the domain row; never persist an expiring URL there.
alter table public.storage_deletion_queue
  add column quota_reservation_id uuid,
  add column quota_released_at timestamptz;

alter table public.uploaded_videos
  add column source_object_reference_id uuid,
  add column verified_size_bytes bigint check (verified_size_bytes is null or verified_size_bytes > 0),
  add column verified_sha256 text check (
    verified_sha256 is null or verified_sha256 ~ '^[0-9a-f]{64}$'
  ),
  add column storage_quota_reservation_id uuid,
  add column legacy_quota_reservation_id uuid,
  add column legacy_upload_expires_at timestamptz,
  add constraint task6_legacy_upload_reservation_pair check (
    (legacy_quota_reservation_id is null) = (legacy_upload_expires_at is null)
  ),
  add constraint task6_upload_source_reference_tenant_fk
    foreign key (workspace_id, project_id, source_object_reference_id)
    references public.object_references (workspace_id, project_id, id)
    on delete set null (source_object_reference_id);

create index uploaded_videos_legacy_expiry_idx
  on public.uploaded_videos (legacy_upload_expires_at)
  where legacy_quota_reservation_id is not null;

create or replace function public.queue_deleted_uploaded_video_object()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  insert into public.storage_deletion_queue (
    storage_bucket,
    storage_path,
    source_table,
    quota_reservation_id
  ) values (
    old.storage_bucket,
    old.storage_path,
    tg_table_name,
    old.storage_quota_reservation_id
  )
  on conflict (storage_bucket, storage_path) do update
  set quota_reservation_id = coalesce(
    public.storage_deletion_queue.quota_reservation_id,
    excluded.quota_reservation_id
  );
  return old;
end;
$$;

drop trigger if exists uploaded_videos_queue_storage_delete on public.uploaded_videos;
create trigger uploaded_videos_queue_storage_delete
before delete on public.uploaded_videos
for each row execute function public.queue_deleted_uploaded_video_object();

-- Serialize workspace admission so concurrent upload initialization cannot exceed
-- the configured active-upload semaphore.
create or replace function public.motionly_initialize_upload(
  p_upload_id uuid,
  p_workspace_id uuid,
  p_project_id uuid,
  p_storage_bucket text,
  p_storage_path text,
  p_original_filename text,
  p_mime_type text,
  p_file_size_bytes bigint,
  p_max_active_uploads integer
)
returns setof public.uploaded_videos
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if p_max_active_uploads <= 0 then
    raise exception using errcode = '22023', message = 'active upload limit must be positive';
  end if;

  perform 1 from public.workspaces where id = p_workspace_id for update;
  if not found or not exists (
    select 1 from public.projects
    where id = p_project_id and workspace_id = p_workspace_id
  ) then
    return;
  end if;
  if (
    select count(*)
    from public.uploaded_videos
    where workspace_id = p_workspace_id
      and status in ('created', 'uploading', 'uploaded')
  ) >= p_max_active_uploads then
    return;
  end if;

  return query
  insert into public.uploaded_videos (
    id, workspace_id, project_id, storage_bucket, storage_path,
    original_filename, mime_type, byte_size, file_size_bytes, status
  ) values (
    p_upload_id, p_workspace_id, p_project_id, p_storage_bucket, p_storage_path,
    p_original_filename, p_mime_type, p_file_size_bytes, p_file_size_bytes, 'uploading'
  )
  returning *;
end;
$$;

-- Claiming legacy completion locks the upload before creating or observing quota,
-- preventing cancellation from interleaving with linkage/reservation setup.
create or replace function public.motionly_claim_legacy_upload(
  p_upload_id uuid,
  p_workspace_id uuid,
  p_project_id uuid,
  p_reservation_id uuid,
  p_byte_amount bigint,
  p_expires_at timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_upload public.uploaded_videos%rowtype;
  v_reservation public.storage_quota_reservations%rowtype;
begin
  select * into v_upload
  from public.uploaded_videos
  where id = p_upload_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;

  if not found then
    return false;
  end if;
  if v_upload.status in ('processing', 'ready') then
    return v_upload.storage_quota_reservation_id = p_reservation_id;
  end if;
  if v_upload.status not in ('created', 'uploading', 'uploaded')
     or (
       v_upload.legacy_quota_reservation_id is not null
       and v_upload.legacy_quota_reservation_id <> p_reservation_id
     ) then
    return false;
  end if;

  select * into v_reservation
  from public.storage_quota_reservations
  where id = p_reservation_id
  for update;
  if found then
    if v_reservation.workspace_id <> p_workspace_id
       or v_reservation.byte_amount <> p_byte_amount
       or v_reservation.object_amount <> 1
       or v_reservation.expires_at <> p_expires_at
       or v_reservation.state not in ('reserved', 'committed') then
      return false;
    end if;
  else
    if p_expires_at <= now() or not public.motionly_reserve_storage_quota(
      p_reservation_id,
      p_workspace_id,
      p_byte_amount,
      1,
      p_expires_at
    ) then
      return false;
    end if;
  end if;

  update public.uploaded_videos
  set legacy_quota_reservation_id = p_reservation_id,
      legacy_upload_expires_at = p_expires_at,
      updated_at = now()
  where id = p_upload_id;
  return true;
end;
$$;

-- Finalization and cancellation lock the same upload row before reservation state,
-- yielding only fully accepted or fully canceled outcomes under concurrency.
create or replace function public.motionly_finalize_legacy_upload(
  p_upload_id uuid,
  p_workspace_id uuid,
  p_project_id uuid,
  p_reservation_id uuid,
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
  v_upload public.uploaded_videos%rowtype;
  v_reservation public.storage_quota_reservations%rowtype;
begin
  select * into v_upload
  from public.uploaded_videos
  where id = p_upload_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;

  if not found then
    return false;
  end if;
  if v_upload.status in ('processing', 'ready') then
    return v_upload.storage_quota_reservation_id = p_reservation_id;
  end if;
  if v_upload.status not in ('created', 'uploading', 'uploaded')
     or v_upload.legacy_quota_reservation_id <> p_reservation_id then
    return false;
  end if;
  if not exists (
    select 1 from public.object_references
    where id = p_reference_id
      and workspace_id = p_workspace_id
      and project_id = p_project_id
      and owner_type = 'uploaded_video'
      and owner_id = p_upload_id
      and storage_bucket = v_upload.storage_bucket
      and storage_path = v_upload.storage_path
      and status = 'active'
  ) then
    return false;
  end if;

  select * into v_reservation
  from public.storage_quota_reservations
  where id = p_reservation_id and workspace_id = p_workspace_id
  for update;
  if not found or v_reservation.state in ('released', 'deleted') then
    return false;
  end if;
  if v_reservation.state = 'reserved' and v_reservation.expires_at <= now() then
    return false;
  end if;
  if not public.motionly_commit_storage_quota(p_reservation_id) then
    return false;
  end if;

  update public.uploaded_videos
  set status = 'processing',
      source_object_reference_id = p_reference_id,
      verified_size_bytes = p_verified_size_bytes,
      verified_sha256 = p_verified_sha256,
      storage_quota_reservation_id = p_reservation_id,
      updated_at = now()
  where id = p_upload_id;
  update public.projects
  set active_video_id = p_upload_id, updated_at = now()
  where id = p_project_id and workspace_id = p_workspace_id;
  return true;
end;
$$;

create or replace function public.motionly_cancel_legacy_upload(
  p_upload_id uuid,
  p_workspace_id uuid,
  p_project_id uuid,
  p_reservation_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_upload public.uploaded_videos%rowtype;
  v_reservation_state text;
begin
  select * into v_upload
  from public.uploaded_videos
  where id = p_upload_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;

  if not found or v_upload.status in ('processing', 'ready') then
    return false;
  end if;
  if v_upload.legacy_quota_reservation_id is not null
     and v_upload.legacy_quota_reservation_id <> p_reservation_id then
    return false;
  end if;

  select state into v_reservation_state
  from public.storage_quota_reservations
  where id = p_reservation_id and workspace_id = p_workspace_id
  for update;
  if v_reservation_state in ('committed', 'deleted') then
    return false;
  end if;
  if v_reservation_state = 'reserved'
     and not public.motionly_release_storage_quota(p_reservation_id) then
    return false;
  end if;

  update public.object_references
  set status = 'deleted', deleted_at = now(), updated_at = now()
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and owner_type = 'uploaded_video'
    and owner_id = p_upload_id
    and status <> 'deleted';
  update public.uploaded_videos
  set status = 'failed',
      source_object_reference_id = null,
      error_code = 'upload_canceled',
      error_message = 'Upload canceled before the source video was completed.',
      legacy_quota_reservation_id = null,
      legacy_upload_expires_at = null,
      updated_at = now()
  where id = p_upload_id;
  insert into public.storage_deletion_queue (
    storage_bucket, storage_path, source_table
  ) values (
    v_upload.storage_bucket, v_upload.storage_path, 'legacy_upload_cancellation'
  )
  on conflict (storage_bucket, storage_path) do nothing;
  return true;
end;
$$;

-- Commit quota and persist the completion milestone in the same transaction.
create or replace function public.motionly_commit_upload_session_quota(
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
  v_session public.upload_sessions%rowtype;
begin
  select * into v_session
  from public.upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;

  if not found then
    return false;
  end if;
  if v_session.status = 'completed' or v_session.quota_committed_at is not null then
    return true;
  end if;
  if v_session.status <> 'completing' then
    return false;
  end if;
  if not public.motionly_commit_storage_quota(v_session.quota_reservation_id) then
    return false;
  end if;

  update public.upload_sessions
  set quota_committed_at = now(), updated_at = now()
  where id = v_session.id;
  return true;
end;
$$;

-- Finalize a verified multipart object in one transaction. Terminal cleanup locks the
-- same session row, so it cannot overtake or partially interleave this transition.
create or replace function public.motionly_finalize_upload_session(
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
  v_session public.upload_sessions%rowtype;
  v_reservation public.storage_quota_reservations%rowtype;
begin
  select * into v_session
  from public.upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;

  if not found then
    return false;
  end if;
  if v_session.status = 'completed' then
    return true;
  end if;
  if v_session.status <> 'completing' then
    return false;
  end if;
  if p_verified_size_bytes <> v_session.declared_size_bytes
     or p_verified_sha256 !~ '^[0-9a-f]{64}$'
     or not exists (
       select 1 from public.object_references
       where id = p_reference_id
         and workspace_id = p_workspace_id
         and project_id = p_project_id
         and owner_type = 'uploaded_video'
         and owner_id = v_session.uploaded_video_id
         and storage_bucket = v_session.final_storage_bucket
         and storage_path = v_session.final_storage_path
         and status = 'active'
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

  update public.uploaded_videos
  set status = 'processing',
      source_object_reference_id = p_reference_id,
      verified_size_bytes = p_verified_size_bytes,
      verified_sha256 = p_verified_sha256,
      storage_quota_reservation_id = v_session.quota_reservation_id,
      updated_at = now()
  where id = v_session.uploaded_video_id
    and workspace_id = v_session.workspace_id
    and project_id = v_session.project_id
    and status in ('created', 'uploading', 'uploaded', 'processing');
  if not found then
    raise exception using errcode = '55000', message = 'upload row cannot be finalized';
  end if;

  update public.projects
  set active_video_id = v_session.uploaded_video_id, updated_at = now()
  where id = v_session.project_id and workspace_id = v_session.workspace_id;
  update public.upload_sessions
  set status = 'completed',
      quota_committed_at = coalesce(quota_committed_at, now()),
      verified_size_bytes = p_verified_size_bytes,
      verified_sha256 = p_verified_sha256,
      completed_at = coalesce(completed_at, now()),
      failure_code = null,
      updated_at = now()
  where id = v_session.id;

  insert into public.storage_deletion_queue (
    storage_bucket, storage_path, source_table
  )
  select v_session.final_storage_bucket, part.object_path, 'upload_session_parts'
  from public.upload_session_parts as part
  where part.session_id = v_session.id
  on conflict (storage_bucket, storage_path) do nothing;
  return true;
end;
$$;

-- Terminal cleanup is one idempotent transaction: transition the session, release
-- held quota, fail the domain row, and durably queue every storage deletion.
create or replace function public.motionly_terminate_upload_session(
  p_session_id uuid,
  p_workspace_id uuid,
  p_project_id uuid,
  p_status text,
  p_failure_code text,
  p_error_message text,
  p_delete_final boolean default false
)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_session public.upload_sessions%rowtype;
  v_reservation_state text;
  v_was_completing boolean;
begin
  if p_status not in ('aborted', 'expired', 'failed') then
    raise exception using errcode = '22023', message = 'invalid terminal upload status';
  end if;

  select * into v_session
  from public.upload_sessions
  where id = p_session_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;

  if not found then
    return null;
  end if;
  v_was_completing := v_session.status = 'completing';
  if v_session.status = 'completed' then
    return v_session.status;
  end if;
  if v_session.quota_committed_at is not null then
    raise exception using errcode = '55000', message = 'committed upload must resume completion';
  end if;

  select state into v_reservation_state
  from public.storage_quota_reservations
  where id = v_session.quota_reservation_id
    and workspace_id = v_session.workspace_id
  for update;

  if v_reservation_state = 'committed' then
    raise exception using errcode = '55000', message = 'committed upload must resume completion';
  end if;
  if v_reservation_state = 'reserved'
     and not public.motionly_release_storage_quota(v_session.quota_reservation_id) then
    raise exception using errcode = '55000', message = 'upload quota cleanup failed';
  end if;

  if v_session.status in ('active', 'completing') then
    update public.upload_sessions
    set status = p_status,
        aborted_at = case when p_status = 'aborted' then now() else aborted_at end,
        failed_at = case when p_status in ('expired', 'failed') then now() else failed_at end,
        failure_code = p_failure_code,
        updated_at = now()
    where id = v_session.id;
    v_session.status := p_status;
  end if;

  update public.uploaded_videos
  set status = 'failed',
      error_code = p_failure_code,
      error_message = p_error_message,
      updated_at = now()
  where id = v_session.uploaded_video_id
    and workspace_id = v_session.workspace_id
    and project_id = v_session.project_id
    and status not in ('processing', 'ready');

  insert into public.storage_deletion_queue (
    storage_bucket,
    storage_path,
    source_table
  )
  select
    v_session.final_storage_bucket,
    part.object_path,
    'upload_session_parts'
  from public.upload_session_parts as part
  where part.session_id = v_session.id
  on conflict (storage_bucket, storage_path) do nothing;

  if p_delete_final or v_was_completing then
    update public.object_references
    set status = 'deleted', deleted_at = now(), updated_at = now()
    where workspace_id = v_session.workspace_id
      and project_id = v_session.project_id
      and owner_type = 'uploaded_video'
      and owner_id = v_session.uploaded_video_id
      and storage_bucket = v_session.final_storage_bucket
      and storage_path = v_session.final_storage_path
      and status <> 'deleted';

    update public.uploaded_videos
    set source_object_reference_id = null, updated_at = now()
    where id = v_session.uploaded_video_id;

    insert into public.storage_deletion_queue (
      storage_bucket,
      storage_path,
      source_table
    ) values (
      v_session.final_storage_bucket,
      v_session.final_storage_path,
      'upload_sessions'
    )
    on conflict (storage_bucket, storage_path) do nothing;
  end if;

  return v_session.status;
end;
$$;

-- Expire active work and stale pre-commit completion through the same durable
-- terminal transition. Post-commit completion is never expired; it is resumed.
create or replace function public.motionly_expire_upload_sessions()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_session record;
  v_count integer := 0;
begin
  for v_session in
    select id, workspace_id, project_id
    from public.upload_sessions
    where status in ('active', 'completing')
      and quota_committed_at is null
      and expires_at <= now()
    for update skip locked
  loop
    if public.motionly_terminate_upload_session(
      v_session.id,
      v_session.workspace_id,
      v_session.project_id,
      'expired',
      'upload_session_expired',
      'Multipart upload session expired.',
      true
    ) = 'expired' then
      v_count := v_count + 1;
    end if;
  end loop;
  return v_count;
end;
$$;

-- Legacy single-PUT rows carry their deterministic reservation and deadline.
-- Expiry releases only uncommitted quota, fails the row, and durably queues the
-- uploaded object. Committed finalization is preserved for idempotent recovery.
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

  -- Release any older orphan reservation that predates durable upload linkage.
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
    for update of reservation skip locked
  loop
    if public.motionly_release_storage_quota(v_reservation.id) then
      v_count := v_count + 1;
    end if;
  end loop;
  return v_count;
end;
$$;

-- Standard timestamp maintenance.
create trigger workspace_storage_quotas_touch_updated_at
before update on public.workspace_storage_quotas
for each row execute function public.touch_updated_at();
create trigger storage_quota_reservations_touch_updated_at
before update on public.storage_quota_reservations
for each row execute function public.touch_updated_at();
create trigger upload_sessions_touch_updated_at
before update on public.upload_sessions
for each row execute function public.touch_updated_at();
create trigger upload_session_parts_touch_updated_at
before update on public.upload_session_parts
for each row execute function public.touch_updated_at();
create trigger object_references_touch_updated_at
before update on public.object_references
for each row execute function public.touch_updated_at();

-- Browser clients may select rows for their tenant but never mutate Task 6 state.
do $task6_rls$
declare
  target_table text;
begin
  foreach target_table in array array[
    'workspace_storage_quotas', 'storage_quota_reservations', 'upload_sessions',
    'upload_session_parts', 'object_references', 'signed_url_grants'
  ] loop
    execute format('alter table public.%I enable row level security', target_table);
    execute format('revoke all privileges on table public.%I from public, anon, authenticated', target_table);
    execute format('grant select on table public.%I to authenticated', target_table);
    execute format('grant all privileges on table public.%I to service_role', target_table);
    execute format(
      'create policy %I on public.%I for select to authenticated using (public.is_workspace_member(workspace_id))',
      'tenant members read ' || target_table,
      target_table
    );
  end loop;
end
$task6_rls$;

revoke all on function public.motionly_reserve_storage_quota(uuid, uuid, bigint, integer, timestamptz)
  from public, anon, authenticated;
revoke all on function public.motionly_commit_storage_quota(uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_release_storage_quota(uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_delete_committed_storage_quota(uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_initialize_upload(
  uuid, uuid, uuid, text, text, text, text, bigint, integer
) from public, anon, authenticated;
revoke all on function public.motionly_claim_legacy_upload(
  uuid, uuid, uuid, uuid, bigint, timestamptz
) from public, anon, authenticated;
revoke all on function public.motionly_finalize_legacy_upload(
  uuid, uuid, uuid, uuid, uuid, bigint, text
) from public, anon, authenticated;
revoke all on function public.motionly_cancel_legacy_upload(uuid, uuid, uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_finalize_upload_session(
  uuid, uuid, uuid, uuid, bigint, text
) from public, anon, authenticated;
revoke all on function public.motionly_commit_upload_session_quota(uuid, uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.motionly_terminate_upload_session(
  uuid, uuid, uuid, text, text, text, boolean
) from public, anon, authenticated;
revoke all on function public.motionly_expire_upload_sessions()
  from public, anon, authenticated;
revoke all on function public.motionly_expire_storage_quota_reservations()
  from public, anon, authenticated;
grant execute on function public.motionly_reserve_storage_quota(uuid, uuid, bigint, integer, timestamptz)
  to service_role;
grant execute on function public.motionly_commit_storage_quota(uuid) to service_role;
grant execute on function public.motionly_release_storage_quota(uuid) to service_role;
grant execute on function public.motionly_delete_committed_storage_quota(uuid)
  to service_role;
grant execute on function public.motionly_initialize_upload(
  uuid, uuid, uuid, text, text, text, text, bigint, integer
) to service_role;
grant execute on function public.motionly_claim_legacy_upload(
  uuid, uuid, uuid, uuid, bigint, timestamptz
) to service_role;
grant execute on function public.motionly_finalize_legacy_upload(
  uuid, uuid, uuid, uuid, uuid, bigint, text
) to service_role;
grant execute on function public.motionly_cancel_legacy_upload(uuid, uuid, uuid, uuid)
  to service_role;
grant execute on function public.motionly_finalize_upload_session(
  uuid, uuid, uuid, uuid, bigint, text
) to service_role;
grant execute on function public.motionly_commit_upload_session_quota(uuid, uuid, uuid)
  to service_role;
grant execute on function public.motionly_terminate_upload_session(
  uuid, uuid, uuid, text, text, text, boolean
) to service_role;
grant execute on function public.motionly_expire_upload_sessions() to service_role;
grant execute on function public.motionly_expire_storage_quota_reservations()
  to service_role;
