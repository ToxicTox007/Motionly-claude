begin;
select plan(8);

select has_column(
  'public', 'asset_library_items', 'object_reference_id',
  'asset library stores a durable object reference ID'
);
select has_column(
  'public', 'asset_library_items', 'media_state',
  'asset library records verified or quarantined availability'
);
select has_column(
  'public', 'asset_library_items', 'quarantine_reason',
  'asset library retains a recovery reason for legacy items'
);
select has_column(
  'public', 'asset_library_items', 'quarantined_at',
  'asset library records when a legacy item was quarantined'
);
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.asset_library_items'::regclass
      and conname = 'asset_library_items_reference_tenant_fk'
      and contype = 'f'
  ),
  'asset references are tenant and source-project scoped'
);
select ok(
  to_regprocedure('public.motionly_backfill_asset_library_references(text[])') is not null,
  'proof-only legacy reference backfill exists'
);
select ok(
  not has_function_privilege(
    'authenticated',
    'public.motionly_backfill_asset_library_references(text[])',
    'EXECUTE'
  ) and has_function_privilege(
    'service_role',
    'public.motionly_backfill_asset_library_references(text[])',
    'EXECUTE'
  ),
  'only service role can run legacy reference recovery'
);
select ok(
  to_regclass('public.asset_library_items_reference_state_idx') is not null,
  'asset reference status lookups are indexed'
);

select * from finish();
rollback;
