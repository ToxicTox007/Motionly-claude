-- Task 28: mood synonym search (N9).
--
-- The edit planner emits FREE-TEXT moods ("uplifting anthemic momentum", "soft
-- intimate piano bed", ...). Catalog tracks carry a FIXED closed vocabulary of
-- mood tags. To let a free-text mood match a track in the DATABASE we keep a
-- synonym table (synonym word -> canonical mood tag) and a per-track
-- mood_synonyms text[] array (the union of each tag plus every synonym word
-- that maps to it), GIN-indexed so the search filters with a single array
-- overlap. The application resolves the free-text phrase to canonical tags via
-- the same table (app/services/music_search.py) and filters on mood_synonyms.
--
-- Forward-only. Reserved timestamp 2026101914xx (Task 28).

begin;

-- Canonical synonym vocabulary. synonym_word is a single lowercase token a user
-- or the planner might type; mood_tag is the canonical catalog tag it resolves
-- to. One synonym can map to several tags (multiple rows).
create table if not exists public.music_mood_synonyms (
  synonym_word text not null,
  mood_tag text not null,
  created_at timestamptz not null default now(),
  primary key (synonym_word, mood_tag)
);

create index if not exists music_mood_synonyms_mood_tag_idx
  on public.music_mood_synonyms (mood_tag);

-- Only the service role manages the synonym vocabulary; it is read through the
-- API with the service role (never written by browsers).
alter table public.music_mood_synonyms enable row level security;

-- Per-track expanded synonym set for the DB filter + GIN overlap index.
alter table public.licensed_music_tracks
  add column if not exists mood_synonyms text[] not null default array[]::text[];

create index if not exists licensed_music_tracks_mood_synonyms_idx
  on public.licensed_music_tracks using gin (mood_synonyms);

-- Recompute mood_synonyms from mood_tags: each canonical tag, plus every
-- synonym word that maps to any of the track's tags. IMMUTABLE-free (reads the
-- synonym table), so it runs in a trigger rather than a generated column.
create or replace function public.compute_mood_synonyms(tags text[])
returns text[]
language sql
stable
as $$
  select coalesce(array_agg(distinct word), array[]::text[])
  from (
    -- the canonical tags themselves
    select unnest(tags) as word
    union
    -- every synonym word that resolves to one of the track's tags
    select s.synonym_word
    from public.music_mood_synonyms s
    where s.mood_tag = any(tags)
  ) expanded;
$$;

create or replace function public.licensed_music_tracks_sync_synonyms()
returns trigger
language plpgsql
as $$
begin
  new.mood_synonyms := public.compute_mood_synonyms(new.mood_tags);
  return new;
end;
$$;

-- Task 22 contract: PUBLIC must never carry an ambient EXECUTE grant on a
-- function in schema public. Revoke it; the trigger runs as the table owner and
-- the backfill below runs as the migration role, so no explicit grant is needed.
revoke execute on function public.compute_mood_synonyms(text[]) from public;
revoke execute on function public.licensed_music_tracks_sync_synonyms() from public;

drop trigger if exists licensed_music_tracks_sync_synonyms
  on public.licensed_music_tracks;
create trigger licensed_music_tracks_sync_synonyms
before insert or update of mood_tags on public.licensed_music_tracks
for each row execute function public.licensed_music_tracks_sync_synonyms();

-- Seed the synonym vocabulary. Mirrors app/services/music_search.py SYNONYM
-- table; keep the two in sync (tests assert every planner mood resolves).
insert into public.music_mood_synonyms (synonym_word, mood_tag) values
  ('anthemic', 'uplifting'), ('anthemic', 'inspiring'),
  ('momentum', 'uplifting'), ('momentum', 'energetic'),
  ('motivational', 'uplifting'), ('motivational', 'inspiring'),
  ('inspirational', 'inspiring'), ('inspirational', 'uplifting'),
  ('triumphant', 'uplifting'), ('triumphant', 'inspiring'),
  ('hopeful', 'inspiring'), ('hopeful', 'uplifting'),
  ('positive', 'happy'), ('positive', 'uplifting'),
  ('bright', 'happy'), ('bright', 'uplifting'),
  ('feelgood', 'happy'), ('feelgood', 'uplifting'),
  ('pop', 'happy'), ('pop', 'playful'),
  ('creator', 'playful'), ('creator', 'happy'),
  ('soft', 'calm'), ('soft', 'subtle'),
  ('intimate', 'calm'), ('intimate', 'subtle'),
  ('piano', 'calm'), ('piano', 'cinematic'),
  ('bed', 'ambient'), ('bed', 'subtle'),
  ('emotional', 'calm'), ('emotional', 'cinematic'),
  ('gentle', 'calm'), ('gentle', 'subtle'),
  ('warm', 'calm'), ('warm', 'inspiring'),
  ('narrative', 'cinematic'), ('narrative', 'calm'),
  ('arc', 'cinematic'), ('arc', 'inspiring'),
  ('story', 'cinematic'), ('story', 'calm'),
  ('storytelling', 'cinematic'), ('storytelling', 'calm'),
  ('reflective', 'calm'), ('reflective', 'ambient'),
  ('cinematic', 'cinematic'),
  ('atmospheric', 'ambient'), ('atmospheric', 'cinematic'),
  ('build', 'cinematic'), ('build', 'dramatic'),
  ('epic', 'cinematic'), ('epic', 'dramatic'),
  ('score', 'cinematic'),
  ('tense', 'dark'), ('tense', 'dramatic'),
  ('impact', 'dramatic'), ('impact', 'energetic'),
  ('dramatic', 'dramatic'),
  ('dark', 'dark'),
  ('moody', 'dark'), ('moody', 'cinematic'),
  ('suspense', 'dark'), ('suspense', 'dramatic'),
  ('editorial', 'cinematic'), ('editorial', 'dramatic'),
  ('contrast', 'dramatic'), ('contrast', 'cinematic'),
  ('fashion', 'cinematic'), ('fashion', 'confident'),
  ('energetic', 'energetic'),
  ('beat', 'energetic'), ('beat', 'playful'),
  ('sync', 'energetic'),
  ('fast', 'energetic'),
  ('upbeat', 'energetic'), ('upbeat', 'happy'),
  ('driving', 'energetic'), ('driving', 'confident'),
  ('punchy', 'energetic'), ('punchy', 'confident'),
  ('travel', 'energetic'), ('travel', 'uplifting'),
  ('adventure', 'energetic'), ('adventure', 'inspiring'),
  ('clean', 'clean'), ('clean', 'minimal'),
  ('minimal', 'minimal'), ('minimal', 'clean'),
  ('corporate', 'clean'), ('corporate', 'confident'),
  ('confident', 'confident'),
  ('crisp', 'clean'), ('crisp', 'confident'),
  ('light', 'clean'), ('light', 'subtle'),
  ('educational', 'clean'), ('educational', 'neutral'),
  ('teaching', 'clean'), ('teaching', 'neutral'),
  ('neutral', 'neutral'),
  ('modern', 'clean'), ('modern', 'minimal'),
  ('sleek', 'minimal'), ('sleek', 'luxury'),
  ('luxury', 'luxury'),
  ('luxurious', 'luxury'),
  ('premium', 'luxury'), ('premium', 'confident'),
  ('elegant', 'luxury'), ('elegant', 'calm'),
  ('sophisticated', 'luxury'), ('sophisticated', 'confident'),
  ('ambient', 'ambient'),
  ('subtle', 'subtle'),
  ('background', 'ambient'), ('background', 'subtle'),
  ('underscore', 'subtle'), ('underscore', 'ambient'),
  ('conversational', 'conversational'), ('conversational', 'podcast'),
  ('podcast', 'podcast'), ('podcast', 'conversational'),
  ('talk', 'podcast'), ('talk', 'conversational'),
  ('calm', 'calm'),
  ('chill', 'calm'), ('chill', 'ambient'),
  ('chilled', 'calm'), ('chilled', 'ambient'),
  ('relaxed', 'calm'), ('relaxed', 'ambient'),
  ('mellow', 'calm'), ('mellow', 'subtle'),
  ('focused', 'minimal'), ('focused', 'clean'),
  ('happy', 'happy'),
  ('playful', 'playful'),
  ('fun', 'playful'), ('fun', 'happy'),
  ('quirky', 'playful'),
  ('inspiring', 'inspiring'),
  ('uplifting', 'uplifting')
on conflict (synonym_word, mood_tag) do nothing;

-- Backfill mood_synonyms for any existing rows.
update public.licensed_music_tracks
  set mood_synonyms = public.compute_mood_synonyms(mood_tags)
  where true;

commit;
