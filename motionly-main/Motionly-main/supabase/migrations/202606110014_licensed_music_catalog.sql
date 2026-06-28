-- Production licensed music catalog.
-- Scope: add a non-fixture provider and store service-managed licensed tracks.

do $$
begin
  if exists (select 1 from pg_type where typname = 'music_provider') then
    if not exists (
      select 1
      from pg_enum
      where enumtypid = 'public.music_provider'::regtype
        and enumlabel = 'licensed_catalog'
    ) then
      alter type public.music_provider add value 'licensed_catalog';
    end if;
  end if;
end $$;

create table if not exists public.licensed_music_tracks (
  id uuid primary key default gen_random_uuid(),
  provider_track_id text not null unique,
  title text not null,
  license text not null,
  duration_ms integer not null check (duration_ms >= 0),
  mood_tags text[] not null default array[]::text[],
  bpm integer check (bpm > 0),
  energy numeric(4,3) check (energy >= 0 and energy <= 1),
  vocals boolean not null default false,
  loopable boolean not null default false,
  beat_map jsonb not null default '[]'::jsonb,
  audio_url text,
  audio_bucket text,
  audio_path text,
  raw_metadata jsonb not null default '{}'::jsonb,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint licensed_music_tracks_audio_source_check
    check (audio_url is not null or (audio_bucket is not null and audio_path is not null))
);

create index if not exists licensed_music_tracks_active_idx
on public.licensed_music_tracks (active, created_at desc);

create index if not exists licensed_music_tracks_mood_tags_idx
on public.licensed_music_tracks using gin (mood_tags);

drop trigger if exists licensed_music_tracks_touch_updated_at on public.licensed_music_tracks;
create trigger licensed_music_tracks_touch_updated_at
before update on public.licensed_music_tracks
for each row execute function public.touch_updated_at();

alter table public.licensed_music_tracks enable row level security;

-- Catalog writes and reads are performed by the API with the service role.
