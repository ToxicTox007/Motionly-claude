-- Remote AI quotas pgTAP: cost_attribution_events accepts the feature-unit
-- categories the model gateway records for per-plan quotas, still accepts the
-- original categories, and still rejects unknown ones. Forward-only guarantee
-- for the 202610220101 migration.
begin;
select plan(9);

insert into auth.users (id, email, raw_user_meta_data)
values ('12200000-0000-4000-8000-000000000001', 'quota-owner@rls.test', '{}'::jsonb);

insert into public.workspaces (id, name, owner_user_id)
values (
  '32200000-0000-4000-8000-000000000001',
  'Remote AI quota workspace',
  '12200000-0000-4000-8000-000000000001'
);

-- Exactly one category check remains, under the expected name.
select is(
  (select count(*)::int from pg_constraint
   where conrelid = 'public.cost_attribution_events'::regclass
     and contype = 'c'
     and pg_get_constraintdef(oid) ilike '%category%'),
  1,
  'cost_attribution_events has a single category check'
);
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.cost_attribution_events'::regclass
      and conname = 'cost_attribution_events_category_check'
  ),
  'the category check is named cost_attribution_events_category_check'
);

set local role service_role;

select lives_ok(
  $$insert into public.cost_attribution_events
      (workspace_id, source_id, provider, category, quantity, unit, unit_cost_usd)
    values
      ('32200000-0000-4000-8000-000000000001', 'q-objects', 'provider-c',
       'vision_object_images', 24, 'images', 0.0015),
      ('32200000-0000-4000-8000-000000000001', 'q-captions', 'provider-a',
       'vision_caption_images', 24, 'images', 0.0001)$$,
  'vision quota categories are accepted'
);
select lives_ok(
  $$insert into public.cost_attribution_events
      (workspace_id, source_id, provider, category, quantity, unit, unit_cost_usd)
    values ('32200000-0000-4000-8000-000000000001', 'q-embeddings', 'provider-b',
            'embedding_inputs', 16, 'inputs', 0.00002)$$,
  'embedding_inputs is accepted'
);
select lives_ok(
  $$insert into public.cost_attribution_events
      (workspace_id, source_id, provider, category, quantity, unit, unit_cost_usd)
    values
      ('32200000-0000-4000-8000-000000000001', 'q-diarization', 'provider-d',
       'diarization_seconds', 90.5, 'seconds', 0.0001),
      ('32200000-0000-4000-8000-000000000001', 'q-emotion', 'provider-a',
       'emotion_seconds', 60, 'seconds', 0.00005),
      ('32200000-0000-4000-8000-000000000001', 'q-stems', 'provider-e',
       'stem_seconds', 120, 'seconds', 0.001)$$,
  'audio quota categories are accepted'
);
select lives_ok(
  $$insert into public.cost_attribution_events
      (workspace_id, source_id, provider, category, quantity, unit, unit_cost_usd)
    values
      ('32200000-0000-4000-8000-000000000001', 'q-generated', 'provider-c',
       'generated_seconds', 5, 'second', 0),
      ('32200000-0000-4000-8000-000000000001', 'q-tokens', 'provider-a',
       'tokens', 2000, 'token', 0)$$,
  'the original cost categories are still accepted'
);
select is(
  (select sum(quantity)::numeric from public.cost_attribution_events
   where workspace_id = '32200000-0000-4000-8000-000000000001'
     and provider = 'provider-a' and category = 'emotion_seconds'),
  60::numeric,
  'usage for a feature is summed per provider and category'
);
select throws_ok(
  $$insert into public.cost_attribution_events
      (workspace_id, source_id, provider, category, quantity, unit, unit_cost_usd)
    values ('32200000-0000-4000-8000-000000000001', 'q-unknown', 'provider-b',
            'vision_keyframes', 1, 'images', 0)$$,
  '23514', null,
  'unknown categories are still rejected'
);
select throws_ok(
  $$insert into public.cost_attribution_events
      (workspace_id, source_id, provider, category, quantity, unit, unit_cost_usd)
    values ('32200000-0000-4000-8000-000000000001', 'q-negative', 'provider-b',
            'embedding_inputs', -1, 'inputs', 0)$$,
  '23514', null,
  'negative quantities are still rejected'
);

select * from finish();
rollback;
