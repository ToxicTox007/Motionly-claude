begin;
select plan(8);

select has_table('public', 'localized_publish_packages', 'localized publishing package table exists');
select has_table('public', 'asset_library_items', 'workspace asset library table exists');
select has_table('public', 'asset_collections', 'asset collection table exists');
select has_table('public', 'asset_collection_items', 'asset collection membership table exists');
select ok(
  exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'caption_tracks'
      and column_name = 'language'
  ) and exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'caption_tracks'
      and column_name = 'review_status'
  ),
  'caption tracks record their locale and review status'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.localized_publish_packages'::regclass)
  and (select relrowsecurity from pg_class where oid = 'public.asset_library_items'::regclass)
  and (select relrowsecurity from pg_class where oid = 'public.asset_collections'::regclass)
  and (select relrowsecurity from pg_class where oid = 'public.asset_collection_items'::regclass),
  'localized publishing and library tables enforce RLS'
);
select ok(
  to_regprocedure('public.touch_localized_publishing_updated_at()') is not null
    and to_regclass('public.localized_publish_packages_project_updated_idx') is not null,
  'localized publishing timestamp trigger and review index exist'
);
select ok(
  not has_table_privilege('authenticated', 'public.localized_publish_packages', 'INSERT')
    and has_table_privilege('service_role', 'public.asset_library_items', 'UPDATE'),
  'localized packages and asset library remain API/service-role mediated'
);

select * from finish();
rollback;
