begin;
select plan(12);

select has_column(
  'public', 'provider_slo_events', 'model',
  'provider telemetry records the routed model'
);
select has_column(
  'public', 'provider_slo_events', 'model_revision',
  'provider telemetry records immutable model revision'
);
select has_column(
  'public', 'provider_slo_events', 'route_id',
  'provider telemetry records the stable route'
);
select has_column(
  'public', 'provider_slo_events', 'request_id',
  'provider telemetry records the gateway request'
);
select has_column(
  'public', 'provider_slo_events', 'fallback_count',
  'provider telemetry records fallback count'
);
select has_column(
  'public', 'provider_slo_events', 'estimated_cost_usd',
  'provider telemetry records preflight cost'
);
select has_column(
  'public', 'provider_slo_events', 'actual_cost_usd',
  'provider telemetry records settled cost'
);
select has_column(
  'public', 'provider_slo_events', 'usage',
  'provider telemetry records typed usage only'
);

set local role service_role;

select lives_ok(
  $$insert into public.provider_slo_events (
      provider, operation, success, latency_ms, model, model_revision,
      route_id, request_id, fallback_count, estimated_cost_usd,
      actual_cost_usd, usage
    ) values (
      'azure', 'planning', true, 125, 'planner-deployment', 'planner-2026-07-14',
      'azure-planning-primary', 'task10-request', 1, 0.03, 0.02,
      '{"input_units":100,"output_units":50}'::jsonb
    )$$,
  'service role can persist complete gateway provenance'
);

select is(
  (select model_revision from public.provider_slo_events where request_id = 'task10-request'),
  'planner-2026-07-14',
  'model revision provenance is stored verbatim'
);

select throws_ok(
  $$insert into public.provider_slo_events (
      provider, operation, success, latency_ms, fallback_count
    ) values ('custom', 'vision', false, 1, -1)$$,
  '23514', null,
  'negative fallback counts are rejected'
);

select throws_ok(
  $$insert into public.provider_slo_events (
      provider, operation, success, latency_ms, model_revision
    ) values ('custom', 'audio', true, 1, 'revision-without-route')$$,
  '23514', null,
  'revision provenance requires model and route identity'
);

select * from finish();
rollback;
