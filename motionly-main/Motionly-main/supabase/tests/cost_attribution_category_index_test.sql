-- Task 10 / M1 pgTAP: the feature-keyed quota index exists on
-- cost_attribution_events. Forward-only guarantee for the
-- 202610240103_cost_attribution_category_index.sql migration. Counting plan
-- usage per (workspace_id, category) across every provider label (Decision B-1)
-- is served by a category-leading index rather than the provider-leading ones.
begin;
select plan(2);

select ok(
  to_regclass('public.cost_attribution_workspace_category_created_idx') is not null,
  'cost usage workspace/category/time index exists'
);

-- The index leads with (workspace_id, category, created_at) so a provider-less
-- count is index-only (the pre-existing provider-leading index cannot serve it).
select is(
  (select array_to_string(array_agg(a.attname order by k.ord), ',')
     from pg_index i
     join pg_class c on c.oid = i.indexrelid
     join unnest(i.indkey) with ordinality as k(attnum, ord) on true
     join pg_attribute a on a.attrelid = i.indrelid and a.attnum = k.attnum
    where c.relname = 'cost_attribution_workspace_category_created_idx'),
  'workspace_id,category,created_at',
  'the index columns lead with workspace_id, category, created_at'
);

select * from finish();
rollback;
