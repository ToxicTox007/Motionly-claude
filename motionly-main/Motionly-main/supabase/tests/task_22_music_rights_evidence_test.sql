begin;
select plan(8);

select has_table('public', 'licensed_music_tracks', 'licensed catalog table exists');
select ok(
  exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'licensed_music_tracks'
      and column_name = 'rights_status'
  ),
  'licensed catalog records server-authoritative rights status'
);
select ok(
  exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'licensed_music_tracks'
      and column_name = 'permitted_uses'
  ),
  'licensed catalog records permitted uses'
);
select ok(
  exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'project_music_selections'
      and column_name = 'rights_snapshot'
  ),
  'project selections retain an immutable rights snapshot'
);
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.licensed_music_tracks'::regclass
      and pg_get_constraintdef(oid) like '%cleared%revoked%expired%'
  ),
  'rights status accepts only the reviewed catalog states'
);
select ok(
  to_regclass('public.licensed_music_tracks_rights_lookup_idx') is not null,
  'rights lookup index exists for clearance and expiry checks'
);
select ok(
  not has_table_privilege('authenticated', 'public.music_tracks', 'INSERT')
    and not has_table_privilege('authenticated', 'public.project_music_selections', 'UPDATE'),
  'browser roles cannot forge catalog tracks or rights snapshots'
);
select ok(
  has_table_privilege('service_role', 'public.licensed_music_tracks', 'INSERT')
    and has_table_privilege('service_role', 'public.project_music_selections', 'UPDATE'),
  'only the service role can persist reviewed rights evidence'
);

select * from finish();
rollback;
