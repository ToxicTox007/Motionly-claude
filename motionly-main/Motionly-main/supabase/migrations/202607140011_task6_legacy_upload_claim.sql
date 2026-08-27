-- Forward compatibility fix for Task 6 deployments that applied 202607140006
-- before the atomic legacy-upload claim RPC was added to that migration source.
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

revoke all on function public.motionly_claim_legacy_upload(
  uuid, uuid, uuid, uuid, bigint, timestamptz
) from public, anon, authenticated;
grant execute on function public.motionly_claim_legacy_upload(
  uuid, uuid, uuid, uuid, bigint, timestamptz
) to service_role;
