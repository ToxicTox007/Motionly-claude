-- Task 18: storage retention workers claim queue entries atomically before
-- touching object storage. Expired leases are reclaimable; acknowledgement is
-- fenced by the lease token so a stale worker cannot delete a newer claim.

begin;

alter table public.storage_deletion_queue
  add column lease_owner text,
  add column lease_token uuid,
  add column lease_expires_at timestamptz,
  add constraint task18_storage_deletion_queue_lease_shape check (
    (lease_owner is null and lease_token is null and lease_expires_at is null)
    or (lease_owner is not null and lease_token is not null and lease_expires_at is not null)
  );

create index task18_storage_deletion_queue_claim_idx
  on public.storage_deletion_queue (queued_at, source_table, lease_expires_at);

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

  return query
  with candidates as (
    select queue.id
    from public.storage_deletion_queue queue
    where (p_source_table is null or queue.source_table = p_source_table)
      and (p_before is null or queue.queued_at < p_before)
      and (queue.lease_expires_at is null or queue.lease_expires_at <= clock_timestamp())
    order by queue.queued_at, queue.id
    for update skip locked
    limit p_limit
  )
  update public.storage_deletion_queue queue
  set lease_owner = p_lease_owner,
      lease_token = gen_random_uuid(),
      lease_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds)
  from candidates
  where queue.id = candidates.id
  returning queue.*;
end;
$$;

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
    and lease_expires_at > clock_timestamp();
  return found;
end;
$$;

revoke all on function public.motionly_claim_storage_deletion_queue(text,integer,text,timestamptz,integer)
from public, anon, authenticated;
revoke all on function public.motionly_ack_storage_deletion_queue(uuid,uuid)
from public, anon, authenticated;
grant execute on function public.motionly_claim_storage_deletion_queue(text,integer,text,timestamptz,integer)
to service_role;
grant execute on function public.motionly_ack_storage_deletion_queue(uuid,uuid)
to service_role;

commit;
