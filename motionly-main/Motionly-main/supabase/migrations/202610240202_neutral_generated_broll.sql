-- Task 18 / M4 (Path B, confirmed): move generated B-roll off the legacy vendor
-- provider label onto the neutral 'generated' key, create the durable async
-- operation records, rewrite the resource payload keys, and forbid the legacy
-- value from then on.
--
-- Scanner exception (plan Changelog rev 4 item 7): this migration names the
-- legacy enum value only to move rows off it and forbid it afterwards. It is
-- the single new migration allowlisted by exact filename in
-- scripts/verify/vendor-neutrality-allowlist.txt. Any other new migration with
-- a vendor token still fails the scanner.
--
-- Idempotent and forward-only (scripts/check_forward_migrations.sh):
--   * the provider backfills only touch rows still on the legacy value;
--   * the inference_operations insert uses ON CONFLICT DO NOTHING on the
--     per-workspace idempotency key, so a re-run inserts nothing new;
--   * the payload rewrite is keyed on rows that still carry the old keys;
--   * each CHECK is added only when pg_constraint does not already have it
--     (a DO block), so a re-run adds nothing.
--
-- Path B has no dual-read: new code writes 'generated' and reads it; the legacy
-- value is only read here, during this one-release backfill. Historical
-- cost_attribution_events rows keep their recorded provider label as cost
-- history and are not rewritten.

begin;

-- 1. Backfill the provider labels (only legacy rows are touched). -------------
update public.stock_assets
  set provider = 'generated'
  where provider = 'runway';

update public.broll_slots
  set provider = 'generated'
  where provider = 'runway';

-- 2. Create one inference_operations row per generated_broll resource, copying
--    the legacy payload identifiers onto the neutral provider_* columns.
--    capability is the async submit capability; the route_snapshot carries only
--    non-secret identifiers; the idempotency key is derived from the resource
--    id, matching the application key ('generated_broll:<id>').
insert into public.inference_operations (
  workspace_id,
  project_id,
  capability,
  idempotency_key,
  provider_route_id,
  provider_model,
  provider_model_revision,
  route_snapshot,
  provider_operation_id,
  state
)
select
  r.workspace_id,
  r.project_id,
  'video.generation.submit',
  'generated_broll:' || r.id::text,
  coalesce(nullif(r.payload->>'model_gateway_route_id', ''), 'legacy-video-generation'),
  coalesce(nullif(r.payload->>'model', ''), 'legacy-video-generation-model'),
  coalesce(nullif(r.payload->>'model_revision', ''), 'legacy'),
  jsonb_build_object(
    'adapter_id', coalesce(nullif(r.payload->>'model_gateway_route_id', ''), 'legacy-video-generation'),
    'adapter_type', 'async_submit',
    'model', coalesce(nullif(r.payload->>'model', ''), 'legacy-video-generation-model'),
    'model_revision', coalesce(nullif(r.payload->>'model_revision', ''), 'legacy'),
    'mapping_version', '1'
  ),
  nullif(r.payload->>'runway_task_id', ''),
  case r.status
    when 'ready' then 'succeeded'
    when 'completed' then 'succeeded'
    when 'failed' then 'failed'
    else 'submitted'
  end
from public.phase5_resources r
where r.kind = 'generated_broll'
on conflict (workspace_id, idempotency_key) do nothing;

-- 3. Replace the legacy payload keys with a single inference_operation_id that
--    points at the row just created. Only rows that still carry the old keys
--    are rewritten, so a re-run is a no-op.
update public.phase5_resources r
  set payload = (r.payload - 'runway_task_id' - 'model_gateway_route_id' - 'model_revision')
                || jsonb_build_object('inference_operation_id', op.id::text)
  from public.inference_operations op
  where r.kind = 'generated_broll'
    and op.workspace_id = r.workspace_id
    and op.idempotency_key = 'generated_broll:' || r.id::text
    and (
      r.payload ? 'runway_task_id'
      or r.payload ? 'model_gateway_route_id'
      or r.payload ? 'model_revision'
    );

-- 4. Forbid the legacy value from here on (both tables). Idempotent: only add
--    the CHECK when it is not already present (pattern: 202606220001).
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.stock_assets'::regclass
      and conname = 'stock_assets_provider_not_legacy_check'
  ) then
    alter table public.stock_assets
      add constraint stock_assets_provider_not_legacy_check
      check (provider <> 'runway');
  end if;

  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.broll_slots'::regclass
      and conname = 'broll_slots_provider_not_legacy_check'
  ) then
    alter table public.broll_slots
      add constraint broll_slots_provider_not_legacy_check
      check (provider <> 'runway');
  end if;
end $$;

commit;
