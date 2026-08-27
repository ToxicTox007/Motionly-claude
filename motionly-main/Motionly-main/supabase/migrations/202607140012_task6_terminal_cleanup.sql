-- Forward compatibility fixes for Task 6 terminal cleanup definitions that were
-- expanded after some environments had already recorded 202607140006 as applied.
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
    for update of reservation skip locked
  loop
    if public.motionly_release_storage_quota(v_reservation.id) then
      v_count := v_count + 1;
    end if;
  end loop;
  return v_count;
end;
$$;

revoke all on function public.motionly_terminate_upload_session(
  uuid, uuid, uuid, text, text, text, boolean
) from public, anon, authenticated;
revoke all on function public.motionly_expire_storage_quota_reservations()
  from public, anon, authenticated;
grant execute on function public.motionly_terminate_upload_session(
  uuid, uuid, uuid, text, text, text, boolean
) to service_role;
grant execute on function public.motionly_expire_storage_quota_reservations()
  to service_role;
