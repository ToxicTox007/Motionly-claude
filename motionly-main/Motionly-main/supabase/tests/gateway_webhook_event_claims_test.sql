-- Worker audit WJ-23: billing webhook events are claimed before they are
-- applied, so concurrent or crash-replayed deliveries apply an event once.
begin;
select plan(9);

select has_function('public', 'motionly_claim_gateway_webhook_event',
  array['text', 'text', 'integer'], 'webhook claim RPC exists');
select has_function('public', 'motionly_finish_gateway_webhook_event',
  array['text', 'boolean'], 'webhook finish RPC exists');

set local role service_role;

select is(public.motionly_claim_gateway_webhook_event('evt_wj23_a', 'payment.captured', 300),
  'claimed', 'the first delivery claims the event');
select is(public.motionly_claim_gateway_webhook_event('evt_wj23_a', 'payment.captured', 300),
  'in_progress', 'a concurrent delivery is told to retry later, not applied twice');
select ok(public.motionly_finish_gateway_webhook_event('evt_wj23_a', true),
  'a successful apply marks the event processed');
select is(public.motionly_claim_gateway_webhook_event('evt_wj23_a', 'payment.captured', 300),
  'duplicate', 'a redelivery after success is a duplicate');

select is(public.motionly_claim_gateway_webhook_event('evt_wj23_b', 'refund.created', 300),
  'claimed', 'another event is claimed');
select ok(public.motionly_finish_gateway_webhook_event('evt_wj23_b', false),
  'a failed apply releases the claim');
-- A claim abandoned by a crashed process is taken over after the window.
select public.motionly_claim_gateway_webhook_event('evt_wj23_c', 'refund.created', 300);
update public.gateway_webhook_events set claimed_at = now() - interval '10 minutes'
where id = 'evt_wj23_c';
select is(
  array[
    public.motionly_claim_gateway_webhook_event('evt_wj23_b', 'refund.created', 300),
    public.motionly_claim_gateway_webhook_event('evt_wj23_c', 'refund.created', 300)
  ],
  array['claimed', 'claimed'],
  'released and abandoned claims can be applied by a redelivery'
);

select * from finish();
rollback;
