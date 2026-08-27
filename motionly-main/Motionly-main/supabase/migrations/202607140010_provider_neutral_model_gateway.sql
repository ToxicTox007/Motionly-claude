-- Task 10: durable provider-neutral model gateway telemetry and revision provenance.
-- Existing rows remain valid; every gateway attempt records the new fields.

alter table public.provider_slo_events
  add column if not exists model text,
  add column if not exists model_revision text,
  add column if not exists route_id text,
  add column if not exists request_id text,
  add column if not exists fallback_count integer not null default 0,
  add column if not exists estimated_cost_usd numeric(18,8) not null default 0,
  add column if not exists actual_cost_usd numeric(18,8) not null default 0,
  add column if not exists usage jsonb not null default '{}'::jsonb;

alter table public.provider_slo_events
  drop constraint if exists provider_slo_events_fallback_count_check,
  add constraint provider_slo_events_fallback_count_check
    check (fallback_count >= 0),
  drop constraint if exists provider_slo_events_estimated_cost_check,
  add constraint provider_slo_events_estimated_cost_check
    check (estimated_cost_usd >= 0),
  drop constraint if exists provider_slo_events_actual_cost_check,
  add constraint provider_slo_events_actual_cost_check
    check (actual_cost_usd >= 0),
  drop constraint if exists provider_slo_events_model_revision_pair_check,
  add constraint provider_slo_events_model_revision_pair_check
    check (model_revision is null or (model is not null and route_id is not null));

create index if not exists provider_slo_events_route_revision_created_idx
  on public.provider_slo_events(route_id, model_revision, created_at desc)
  where route_id is not null;

create index if not exists provider_slo_events_request_idx
  on public.provider_slo_events(request_id)
  where request_id is not null;

comment on column public.provider_slo_events.model_revision is
  'Immutable configured model/deployment revision used for this gateway attempt.';
comment on column public.provider_slo_events.route_id is
  'Stable provider-neutral gateway route identifier.';
comment on column public.provider_slo_events.usage is
  'Typed input/output unit counts; never stores prompts, media, or provider secrets.';
