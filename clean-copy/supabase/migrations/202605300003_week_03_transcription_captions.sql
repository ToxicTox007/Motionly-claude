-- Week 3 transcription and captions system.
-- Scope: transcript rows, word timings, caption tracks, caption segments, and RLS.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'transcript_status') then
    create type public.transcript_status as enum (
      'queued',
      'processing',
      'ready',
      'failed',
      'captions_unavailable'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'caption_preset_key') then
    create type public.caption_preset_key as enum (
      'luxury_minimal',
      'bold_influencer',
      'vogue_white',
      'gold_premium',
      'founder_clean'
    );
  end if;
end $$;

create table if not exists public.transcripts (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  uploaded_video_id uuid not null references public.uploaded_videos(id) on delete cascade,
  status public.transcript_status not null default 'queued',
  language text not null default 'unknown',
  language_hint text not null default 'auto',
  provider text not null,
  model text not null,
  confidence numeric,
  duration_ms integer,
  raw_payload jsonb not null default '{}'::jsonb,
  schema_version integer not null default 1,
  error_code text,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint transcripts_language_check check (language in ('en', 'hi', 'unknown')),
  constraint transcripts_language_hint_check check (language_hint in ('auto', 'en', 'hi'))
);

create table if not exists public.transcript_words (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  transcript_id uuid not null references public.transcripts(id) on delete cascade,
  word_index integer not null check (word_index >= 0),
  text text not null,
  start_ms integer not null check (start_ms >= 0),
  end_ms integer not null check (end_ms >= start_ms),
  confidence numeric,
  created_at timestamptz not null default now(),
  unique (transcript_id, word_index)
);

create table if not exists public.caption_tracks (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  transcript_id uuid not null references public.transcripts(id) on delete cascade,
  preset public.caption_preset_key not null,
  schema_version integer not null default 1,
  style jsonb not null default '{}'::jsonb,
  hide_filler_words boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.caption_segments (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  caption_track_id uuid not null references public.caption_tracks(id) on delete cascade,
  segment_index integer not null check (segment_index >= 0),
  start_ms integer not null check (start_ms >= 0),
  end_ms integer not null check (end_ms >= start_ms),
  text text not null,
  line_count integer not null default 1 check (line_count between 1 and 2),
  word_indexes integer[] not null default '{}',
  words jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  unique (caption_track_id, segment_index)
);

create index if not exists transcripts_project_updated_idx
on public.transcripts (project_id, updated_at desc);

create index if not exists transcript_words_transcript_index_idx
on public.transcript_words (transcript_id, word_index);

create index if not exists caption_tracks_project_updated_idx
on public.caption_tracks (project_id, updated_at desc);

create index if not exists caption_segments_track_index_idx
on public.caption_segments (caption_track_id, segment_index);

drop trigger if exists transcripts_touch_updated_at on public.transcripts;
create trigger transcripts_touch_updated_at
before update on public.transcripts
for each row execute function public.touch_updated_at();

drop trigger if exists caption_tracks_touch_updated_at on public.caption_tracks;
create trigger caption_tracks_touch_updated_at
before update on public.caption_tracks
for each row execute function public.touch_updated_at();

alter table public.transcripts enable row level security;
alter table public.transcript_words enable row level security;
alter table public.caption_tracks enable row level security;
alter table public.caption_segments enable row level security;

drop policy if exists "members read transcripts" on public.transcripts;
create policy "members read transcripts"
on public.transcripts for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members insert transcripts" on public.transcripts;
create policy "members insert transcripts"
on public.transcripts for insert
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members update transcripts" on public.transcripts;
create policy "members update transcripts"
on public.transcripts for update
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members read transcript words" on public.transcript_words;
create policy "members read transcript words"
on public.transcript_words for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members insert transcript words" on public.transcript_words;
create policy "members insert transcript words"
on public.transcript_words for insert
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members read caption tracks" on public.caption_tracks;
create policy "members read caption tracks"
on public.caption_tracks for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members insert caption tracks" on public.caption_tracks;
create policy "members insert caption tracks"
on public.caption_tracks for insert
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members update caption tracks" on public.caption_tracks;
create policy "members update caption tracks"
on public.caption_tracks for update
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));

drop policy if exists "members read caption segments" on public.caption_segments;
create policy "members read caption segments"
on public.caption_segments for select
using (public.is_workspace_member(workspace_id));

drop policy if exists "members insert caption segments" on public.caption_segments;
create policy "members insert caption segments"
on public.caption_segments for insert
with check (public.is_workspace_member(workspace_id));
