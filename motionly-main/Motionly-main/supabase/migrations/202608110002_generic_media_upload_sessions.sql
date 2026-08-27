-- Audit finding 4: generic, server-verified multipart media sessions for B-roll
-- and style-reference uploads.  Source upload sessions remain unchanged.

begin;

-- These are domain-owned media references, not client-controlled locations.
alter table public.object_references
  drop constraint if exists object_references_owner_type_check,
  add constraint object_references_owner_type_check check (owner_type in (
    'uploaded_video', 'export', 'stock_asset', 'music_track',
    'segmentation_matte', 'video_manifest', 'project', 'style_profile'
  )),
  drop constraint if exists object_references_object_type_check,
  add constraint object_references_object_type_check check (object_type in (
    'source_video', 'thumbnail', 'export', 'broll_asset', 'music_asset',
    'matte', 'temp_preview', 'audio_stem', 'style_reference'
  ));

create table public.generic_media_upload_sessions (
  id uuid primary key,
  workspace_id uuid not null,
  project_id uuid not null,
  owner_type text not null check (owner_type in ('stock_asset', 'style_profile')),
  owner_id uuid not null,
  object_type text not null check (object_type in ('broll_asset', 'style_reference')),
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
  object_reference_id uuid,
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
  constraint generic_media_sessions_project_tenant_fk
    foreign key (workspace_id, project_id)
    references public.projects (workspace_id, id)
    on delete cascade,
  constraint generic_media_sessions_reservation_tenant_fk
    foreign key (workspace_id, quota_reservation_id)
    references public.storage_quota_reservations (workspace_id, id)
    on delete restrict,
  constraint generic_media_sessions_reference_tenant_fk
    foreign key (workspace_id, project_id, object_reference_id)
    references public.object_references (workspace_id, project_id, id)
    on delete restrict,
  check (verified_size_bytes is null or verified_size_bytes = declared_size_bytes),
  check (status <> 'completed' or (
    completed_at is not null
    and quota_committed_at is not null
    and verified_size_bytes is not null
    and verified_sha256 is not null
    and object_reference_id is not null
  ))
);

create table public.generic_media_upload_session_parts (
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
  constraint generic_media_parts_session_tenant_fk
    foreign key (workspace_id, project_id, session_id)
    references public.generic_media_upload_sessions (workspace_id, project_id, id)
    on delete cascade,
  check ((status = 'uploaded') = (uploaded_at is not null)),
  check (verified_size_bytes is null or verified_size_bytes = expected_size_bytes)
);

create index generic_media_sessions_owner_idx
  on public.generic_media_upload_sessions (workspace_id, project_id, owner_type, owner_id);
create index generic_media_sessions_active_expiry_idx
  on public.generic_media_upload_sessions (expires_at)
  where status in ('active', 'completing') and quota_committed_at is null;
create index generic_media_parts_session_status_idx
  on public.generic_media_upload_session_parts (session_id, status, part_number);

-- B-roll domain state points only to the generic session and ObjectReference.
-- Legacy raw_metadata remains diagnostic-only and is never used for signing,
-- completion, deletion, or readiness.
alter table public.stock_assets
  add column if not exists media_upload_session_id uuid,
  add column if not exists object_reference_id uuid,
  add column if not exists media_state text not null default 'not_applicable',
  add column if not exists verified_size_bytes bigint,
  add column if not exists verified_sha256 text,
  add column if not exists storage_quota_reservation_id uuid;

alter table public.stock_assets
  drop constraint if exists stock_assets_media_state_check,
  add constraint stock_assets_media_state_check
    check (media_state in ('not_applicable', 'uploading', 'verified', 'failed', 'aborted', 'expired')),
  drop constraint if exists stock_assets_verified_size_check,
  add constraint stock_assets_verified_size_check
    check (verified_size_bytes is null or verified_size_bytes > 0),
  drop constraint if exists stock_assets_verified_hash_check,
  add constraint stock_assets_verified_hash_check
    check (verified_sha256 is null or verified_sha256 ~ '^[0-9a-f]{64}$');

alter table public.stock_assets
  drop constraint if exists stock_assets_media_session_tenant_fk,
  add constraint stock_assets_media_session_tenant_fk
    foreign key (workspace_id, project_id, media_upload_session_id)
    references public.generic_media_upload_sessions (workspace_id, project_id, id)
    on delete restrict,
  drop constraint if exists stock_assets_media_reference_tenant_fk,
  add constraint stock_assets_media_reference_tenant_fk
    foreign key (workspace_id, project_id, object_reference_id)
    references public.object_references (workspace_id, project_id, id)
    on delete restrict,
  drop constraint if exists stock_assets_media_reservation_tenant_fk,
  add constraint stock_assets_media_reservation_tenant_fk
    foreign key (workspace_id, storage_quota_reservation_id)
    references public.storage_quota_reservations (workspace_id, id)
    on delete restrict;

create index if not exists stock_assets_verified_media_idx
  on public.stock_assets (workspace_id, project_id, object_reference_id)
  where media_state = 'verified';

-- Existing client-owned rows are deliberately not elevated to verified status;
-- they require re-upload or a later proof-only migration.
update public.stock_assets
set media_state = 'failed'
where provider = 'user_library'
  and media_state = 'not_applicable';

-- Generic finalization serializes quota commit and durable reference linkage.
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
     or p_verified_size_bytes <> v_session.declared_size_bytes
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

-- Abort, expiry, and pre-commit failure all use one idempotent cleanup state
-- transition.  Queueing avoids relying on a client process for production object
-- cleanup; the API also attempts immediate deletion for local/test behavior.
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
    raise exception using errcode = '55000', message = 'committed media must not be terminally cleaned';
  end if;

  select state into v_reservation_state
  from public.storage_quota_reservations
  where id = v_session.quota_reservation_id
    and workspace_id = v_session.workspace_id
  for update;
  if v_reservation_state = 'reserved'
     and not public.motionly_release_storage_quota(v_session.quota_reservation_id) then
    raise exception using errcode = '55000', message = 'generic media quota cleanup failed';
  end if;

  if v_session.status in ('active', 'completing') then
    update public.generic_media_upload_sessions
    set status = p_status,
        aborted_at = case when p_status = 'aborted' then now() else aborted_at end,
        failed_at = case when p_status in ('expired', 'failed') then now() else failed_at end,
        failure_code = p_failure_code,
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

  insert into public.storage_deletion_queue (storage_bucket, storage_path, source_table)
  select v_session.final_storage_bucket, part.object_path, 'generic_media_upload_session_parts'
  from public.generic_media_upload_session_parts as part
  where part.session_id = v_session.id
  on conflict (storage_bucket, storage_path) do nothing;
  if p_delete_final then
    insert into public.storage_deletion_queue (storage_bucket, storage_path, source_table)
    values (v_session.final_storage_bucket, v_session.final_storage_path, 'generic_media_upload_sessions')
    on conflict (storage_bucket, storage_path) do nothing;
  end if;
  return p_status;
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
  for v_session in
    select id, workspace_id, project_id
    from public.generic_media_upload_sessions
    where status in ('active', 'completing')
      and quota_committed_at is null
      and expires_at <= now()
    for update skip locked
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

create trigger generic_media_upload_sessions_touch_updated_at
before update on public.generic_media_upload_sessions
for each row execute function public.touch_updated_at();
create trigger generic_media_upload_session_parts_touch_updated_at
before update on public.generic_media_upload_session_parts
for each row execute function public.touch_updated_at();

-- Browser roles can inspect only their tenant rows.  Mutations and lifecycle
-- RPCs stay service-role only, as for source upload sessions.
alter table public.generic_media_upload_sessions enable row level security;
alter table public.generic_media_upload_session_parts enable row level security;
revoke all privileges on table public.generic_media_upload_sessions from public, anon, authenticated;
revoke all privileges on table public.generic_media_upload_session_parts from public, anon, authenticated;
grant select on table public.generic_media_upload_sessions to authenticated;
grant select on table public.generic_media_upload_session_parts to authenticated;
grant all privileges on table public.generic_media_upload_sessions to service_role;
grant all privileges on table public.generic_media_upload_session_parts to service_role;
create policy "tenant members read generic media sessions"
  on public.generic_media_upload_sessions for select to authenticated
  using (public.is_workspace_member(workspace_id));
create policy "tenant members read generic media parts"
  on public.generic_media_upload_session_parts for select to authenticated
  using (public.is_workspace_member(workspace_id));

revoke all on function public.motionly_finalize_generic_media_upload_session(
  uuid, uuid, uuid, uuid, bigint, text
) from public, anon, authenticated;
revoke all on function public.motionly_terminate_generic_media_upload_session(
  uuid, uuid, uuid, text, text, boolean
) from public, anon, authenticated;
revoke all on function public.motionly_expire_generic_media_upload_sessions()
  from public, anon, authenticated;
grant execute on function public.motionly_finalize_generic_media_upload_session(
  uuid, uuid, uuid, uuid, bigint, text
) to service_role;
grant execute on function public.motionly_terminate_generic_media_upload_session(
  uuid, uuid, uuid, text, text, boolean
) to service_role;
grant execute on function public.motionly_expire_generic_media_upload_sessions()
  to service_role;

commit;
