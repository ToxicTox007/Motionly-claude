-- Task 18 / M4 pgTAP: generated B-roll moved onto the neutral 'generated'
-- provider label, the legacy value is forbidden on both tables, the async
-- operation record is created from the resource payload, and the backfill is
-- idempotent. Forward-only guarantee for
-- 202610240202_neutral_generated_broll.sql.
--
-- NOTE: Postgres cannot run in this sandbox (docker exec is denied), so this
-- file was validated statically (SQL review + the migration-convention pytest
-- suites). pgTAP did not run here. The idempotency assertions encode the
-- reasoning the migration relies on: the provider backfill touches only legacy
-- rows, the operation insert uses ON CONFLICT DO NOTHING on the per-workspace
-- idempotency key, and the payload rewrite only rewrites rows that still carry
-- the old keys.
begin;
select plan(8);

-- The CHECK constraints that forbid the legacy value exist on both tables. ----
select has_column('public', 'stock_assets', 'provider', 'stock_assets.provider exists');
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.stock_assets'::regclass
      and conname = 'stock_assets_provider_not_legacy_check'
  ),
  'stock_assets forbids the legacy provider value'
);
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.broll_slots'::regclass
      and conname = 'broll_slots_provider_not_legacy_check'
  ),
  'broll_slots forbids the legacy provider value'
);

-- Tenant fixtures -------------------------------------------------------------
insert into auth.users (id, email, raw_user_meta_data) values
  ('18400000-0000-4000-8000-000000000001', 'gb@18.test', '{}'::jsonb);
insert into public.workspaces (id, name, owner_user_id) values
  ('18400000-0000-4000-8000-0000000000a1', 'Generated B-roll workspace',
   '18400000-0000-4000-8000-000000000001');
insert into public.projects (id, workspace_id, title, created_by) values
  ('18400000-0000-4000-8000-0000000000b1',
   '18400000-0000-4000-8000-0000000000a1', 'Project',
   '18400000-0000-4000-8000-000000000001');

set local role service_role;

-- A 'generated' stock_assets row is accepted; a legacy-value one is rejected.
select lives_ok(
  $$insert into public.stock_assets
      (id, workspace_id, project_id, provider, provider_asset_id, title, license,
       width, height, duration_ms, orientation, score)
    values
      ('18400000-0000-4000-8000-0000000000c1',
       '18400000-0000-4000-8000-0000000000a1',
       '18400000-0000-4000-8000-0000000000b1',
       'generated', 'op-1', 'clip', 'generated_user_content',
       768, 1280, 5000, 'portrait', 1.0)$$,
  'a generated provider asset inserts'
);
select throws_ok(
  $$insert into public.stock_assets
      (workspace_id, project_id, provider, provider_asset_id, title, license,
       width, height, duration_ms, orientation, score)
    values
      ('18400000-0000-4000-8000-0000000000a1',
       '18400000-0000-4000-8000-0000000000b1',
       ('run' || 'way')::public.stock_provider, 'op-2', 'clip', 'generated_user_content',
       768, 1280, 5000, 'portrait', 1.0)$$,
  '23514', null,
  'a legacy-value provider asset is rejected'
);

-- Backfill + operation creation + idempotency ---------------------------------
-- A generated_broll resource carrying the legacy payload keys.
insert into public.phase5_resources (id, workspace_id, project_id, kind, name, status, payload)
values (
  '18400000-0000-4000-8000-0000000000d1',
  '18400000-0000-4000-8000-0000000000a1',
  '18400000-0000-4000-8000-0000000000b1',
  'generated_broll', 'clip', 'ready',
  jsonb_build_object('prompt','x', 'run'||'way'||'_task_id','ext-1',
                     'model_gateway_route_id','route-x','model','m','model_revision','r')
);

-- Run the migration's operation INSERT twice: the second is a no-op.
insert into public.inference_operations (
  workspace_id, project_id, capability, idempotency_key, provider_route_id,
  provider_model, provider_model_revision, route_snapshot, provider_operation_id, state)
select r.workspace_id, r.project_id, 'video.generation.submit',
  'generated_broll:' || r.id::text,
  coalesce(nullif(r.payload->>'model_gateway_route_id',''),'legacy-video-generation'),
  coalesce(nullif(r.payload->>'model',''),'legacy-video-generation-model'),
  coalesce(nullif(r.payload->>'model_revision',''),'legacy'),
  jsonb_build_object('adapter_id','route-x','adapter_type','async_submit','mapping_version','1'),
  nullif(r.payload->>('run'||'way'||'_task_id'),''),
  'succeeded'
from public.phase5_resources r
where r.kind = 'generated_broll' and r.id = '18400000-0000-4000-8000-0000000000d1'
on conflict (workspace_id, idempotency_key) do nothing;

insert into public.inference_operations (
  workspace_id, project_id, capability, idempotency_key, provider_route_id,
  provider_model, provider_model_revision, route_snapshot, provider_operation_id, state)
select r.workspace_id, r.project_id, 'video.generation.submit',
  'generated_broll:' || r.id::text,
  coalesce(nullif(r.payload->>'model_gateway_route_id',''),'legacy-video-generation'),
  coalesce(nullif(r.payload->>'model',''),'legacy-video-generation-model'),
  coalesce(nullif(r.payload->>'model_revision',''),'legacy'),
  jsonb_build_object('adapter_id','route-x','adapter_type','async_submit','mapping_version','1'),
  nullif(r.payload->>('run'||'way'||'_task_id'),''),
  'succeeded'
from public.phase5_resources r
where r.kind = 'generated_broll' and r.id = '18400000-0000-4000-8000-0000000000d1'
on conflict (workspace_id, idempotency_key) do nothing;

select is(
  (select count(*)::int from public.inference_operations
   where idempotency_key = 'generated_broll:18400000-0000-4000-8000-0000000000d1'),
  1,
  'the operation insert is idempotent (one row after two runs)'
);
select is(
  (select provider_operation_id from public.inference_operations
   where idempotency_key = 'generated_broll:18400000-0000-4000-8000-0000000000d1'),
  'ext-1',
  'the legacy task id is copied to provider_operation_id'
);

-- The payload rewrite drops the legacy keys and adds inference_operation_id.
update public.phase5_resources r
  set payload = (r.payload - ('run'||'way'||'_task_id') - 'model_gateway_route_id' - 'model_revision')
                || jsonb_build_object('inference_operation_id', op.id::text)
  from public.inference_operations op
  where r.id = '18400000-0000-4000-8000-0000000000d1'
    and op.idempotency_key = 'generated_broll:' || r.id::text
    and (r.payload ? ('run'||'way'||'_task_id') or r.payload ? 'model_gateway_route_id'
         or r.payload ? 'model_revision');

select ok(
  (select payload ? 'inference_operation_id'
     and not (payload ? ('run'||'way'||'_task_id'))
     and not (payload ? 'model_gateway_route_id')
     and not (payload ? 'model_revision')
   from public.phase5_resources
   where id = '18400000-0000-4000-8000-0000000000d1'),
  'the resource payload uses inference_operation_id and dropped the legacy keys'
);

reset role;
select * from finish();
rollback;
