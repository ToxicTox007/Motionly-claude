begin;
select plan(9);

-- Verification columns required by the import tool.
select has_column('public', 'licensed_music_tracks', 'source_page_url',
  'catalog records the track''s own source page URL');
select has_column('public', 'licensed_music_tracks', 'composition_rights_status',
  'catalog records a separate composition rights status');
select has_column('public', 'licensed_music_tracks', 'recording_rights_status',
  'catalog records a separate recording rights status');
select has_column('public', 'licensed_music_tracks', 'verified_by',
  'catalog records who verified the rights');
select has_column('public', 'licensed_music_tracks', 'is_test_track',
  'catalog marks generated test tracks');

-- A cleared track must have BOTH sides cleared and evidence present.
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.licensed_music_tracks'::regclass
      and conname = 'licensed_music_tracks_cleared_requires_both_sides'
  ),
  'a cleared catalog track must cover composition AND recording with evidence'
);

-- The private music-catalog bucket exists and is not public.
select ok(
  exists (
    select 1 from storage.buckets
    where id = 'music-catalog' and public = false
  ),
  'music-catalog storage bucket exists and is private'
);

-- service_role may write storage.objects for the bucket (policy present).
select ok(
  exists (
    select 1 from pg_policies
    where schemaname = 'storage' and tablename = 'objects'
      and policyname = 'service role manages music catalog'
  ),
  'service_role manages the music-catalog bucket; browsers cannot write it'
);

-- Browser roles cannot write the catalog table (grants from 202608090001).
select ok(
  not has_table_privilege('authenticated', 'public.licensed_music_tracks', 'INSERT')
    and has_table_privilege('service_role', 'public.licensed_music_tracks', 'INSERT'),
  'only the service role can persist verified catalog tracks'
);

select * from finish();
rollback;
