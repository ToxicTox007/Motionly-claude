-- Worker audit WJ-23: the billing webhook checked "already processed?", then
-- applied the event, then recorded it. Two concurrent deliveries of one event
-- could both apply it, and a crash between apply and record re-applied it on
-- redelivery.
--
-- Webhook events are now claimed before they are applied: the first delivery
-- inserts a 'processing' row; a concurrent delivery sees 'processing' and is
-- told to retry later; a delivery after success sees 'processed' and is a
-- duplicate. A failed apply releases the claim, and a claim abandoned by a
-- crashed process is taken over after p_stale_after_seconds.

begin;

alter table public.gateway_webhook_events
  add column if not exists status text not null default 'processed',
  add column if not exists claimed_at timestamptz;

alter table public.gateway_webhook_events
  drop constraint if exists gateway_webhook_events_status_check;
alter table public.gateway_webhook_events
  add constraint gateway_webhook_events_status_check
  check (status in ('processing', 'processed'));

-- Returns 'claimed', 'duplicate' or 'in_progress'.
create or replace function public.motionly_claim_gateway_webhook_event(
  p_event_id text,
  p_event_type text,
  p_stale_after_seconds integer default 300
)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_event public.gateway_webhook_events;
begin
  if p_event_id is null or char_length(p_event_id) < 1 or char_length(p_event_id) > 200 then
    raise exception 'GATEWAY_EVENT_ID_INVALID' using errcode = '22023';
  end if;
  if p_stale_after_seconds is null or p_stale_after_seconds < 30 or p_stale_after_seconds > 3600 then
    raise exception 'GATEWAY_EVENT_STALE_WINDOW_INVALID' using errcode = '22023';
  end if;

  insert into public.gateway_webhook_events (id, event_type, status, claimed_at, processed_at)
  values (p_event_id, coalesce(nullif(btrim(p_event_type), ''), 'unknown'), 'processing', now(), now())
  on conflict (id) do nothing;
  if found then
    return 'claimed';
  end if;

  select * into v_event
  from public.gateway_webhook_events
  where id = p_event_id
  for update;
  if v_event.status = 'processed' then
    return 'duplicate';
  end if;
  if v_event.claimed_at is null
     or v_event.claimed_at < now() - make_interval(secs => p_stale_after_seconds)
  then
    update public.gateway_webhook_events
    set claimed_at = now()
    where id = p_event_id;
    return 'claimed';
  end if;
  return 'in_progress';
end;
$$;

-- Finish a claim: success marks the event processed; failure releases it so a
-- redelivery can apply it.
create or replace function public.motionly_finish_gateway_webhook_event(
  p_event_id text,
  p_succeeded boolean
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if p_succeeded then
    update public.gateway_webhook_events
    set status = 'processed',
        processed_at = now()
    where id = p_event_id;
  else
    delete from public.gateway_webhook_events
    where id = p_event_id
      and status = 'processing';
  end if;
  return found;
end;
$$;

revoke all on function public.motionly_claim_gateway_webhook_event(text, text, integer)
  from public, anon, authenticated;
grant execute on function public.motionly_claim_gateway_webhook_event(text, text, integer)
  to service_role;
revoke all on function public.motionly_finish_gateway_webhook_event(text, boolean)
  from public, anon, authenticated;
grant execute on function public.motionly_finish_gateway_webhook_event(text, boolean)
  to service_role;

commit;
