begin;
select plan(8);

-- The synonym vocabulary table exists.
select has_table('public', 'music_mood_synonyms',
  'mood synonym vocabulary table exists');
select has_column('public', 'music_mood_synonyms', 'synonym_word',
  'synonym table records the free-text word');
select has_column('public', 'music_mood_synonyms', 'mood_tag',
  'synonym table records the canonical mood tag');

-- Per-track synonym array + GIN index for the DB filter.
select has_column('public', 'licensed_music_tracks', 'mood_synonyms',
  'catalog track carries an expanded mood_synonyms array');
select ok(
  exists (
    select 1 from pg_indexes
    where schemaname = 'public'
      and tablename = 'licensed_music_tracks'
      and indexdef ilike '%gin%mood_synonyms%'
  ),
  'mood_synonyms is GIN-indexed for array-overlap search'
);

-- The synonym vocabulary is seeded and covers the free-text planner moods.
select ok(
  (select count(*) from public.music_mood_synonyms) >= 100,
  'synonym vocabulary is seeded'
);
select ok(
  exists (
    select 1 from public.music_mood_synonyms
    where synonym_word = 'anthemic' and mood_tag = 'uplifting'
  ),
  'free-text "anthemic" resolves to the "uplifting" mood tag'
);

-- compute_mood_synonyms expands a track's tags to include matching synonyms.
select ok(
  'anthemic' = any(public.compute_mood_synonyms(array['uplifting']::text[]))
    and 'uplifting' = any(public.compute_mood_synonyms(array['uplifting']::text[])),
  'compute_mood_synonyms expands a tag to its synonyms plus itself'
);

select * from finish();
rollback;
