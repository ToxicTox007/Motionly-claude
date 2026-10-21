-- Task 27: music catalog import evidence + private storage bucket.
--
-- The import tool refuses any track without complete, verified rights. This
-- migration adds the per-track verification columns (the track's own source page
-- URL, the archived-licence SHA-256 kept as evidence, licence id + version, the
-- rights holder/uploader, SEPARATE rights status for the composition AND the
-- recording, and who verified it and when) and a private music-catalog storage
-- bucket for the audio: service_role writes, no browser writes, previews are
-- served through short-lived signed links the API mints.
--
-- Forward-only. Reserved timestamp 2026101913xx (Task 27).

begin;

-- Per-track verification evidence on the licensed catalog. The existing rights_*
-- columns (migration 202608090001) carry the server-authoritative grant; these
-- record HOW that clearance was verified so the admin catalog page and audits
-- can show it. Both sides must be covered before rights_status becomes 'cleared'.
alter table public.licensed_music_tracks
  add column if not exists license_version text,
  add column if not exists license_url text,
  add column if not exists source_page_url text,
  add column if not exists rights_holder text,
  add column if not exists composition_rights_status text,
  add column if not exists recording_rights_status text,
  add column if not exists verified_by text,
  add column if not exists verified_at timestamptz,
  add column if not exists tempo_bpm numeric(7,3),
  add column if not exists beat_positions_ms jsonb not null default '[]'::jsonb,
  -- Generated test tracks are labelled and must never leak into production.
  add column if not exists is_test_track boolean not null default false;

-- Rights status per side accepts only the reviewed clearance states.
alter table public.licensed_music_tracks
  drop constraint if exists licensed_music_tracks_composition_rights_check,
  add constraint licensed_music_tracks_composition_rights_check
    check (
      composition_rights_status is null
      or composition_rights_status in ('cleared', 'unknown', 'not_covered')
    ),
  drop constraint if exists licensed_music_tracks_recording_rights_check,
  add constraint licensed_music_tracks_recording_rights_check
    check (
      recording_rights_status is null
      or recording_rights_status in ('cleared', 'unknown', 'not_covered')
    ),
  -- A cleared catalog track must have both sides cleared and evidence recorded.
  drop constraint if exists licensed_music_tracks_cleared_requires_both_sides,
  add constraint licensed_music_tracks_cleared_requires_both_sides
    check (
      rights_status is distinct from 'cleared'
      or (
        composition_rights_status = 'cleared'
        and recording_rights_status = 'cleared'
        and license_evidence_hash is not null
        and source_page_url is not null
        and verified_by is not null
        and verified_at is not null
      )
    );

create index if not exists licensed_music_tracks_test_track_idx
  on public.licensed_music_tracks (is_test_track);

-- Private music-catalog bucket. Not public: previews are reached only through
-- signed links the API mints with the service role. Audio only; capped size.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'music-catalog',
  'music-catalog',
  false,
  104857600,
  array['audio/mpeg', 'audio/mp4', 'audio/aac', 'audio/wav', 'audio/x-wav', 'audio/flac', 'audio/ogg']
)
on conflict (id) do nothing;

-- RLS on storage.objects: only the service role touches music-catalog. Browser
-- roles (authenticated/anon) get no access; they receive signed links instead.
drop policy if exists "service role manages music catalog" on storage.objects;
create policy "service role manages music catalog" on storage.objects
  for all
  to service_role
  using (bucket_id = 'music-catalog')
  with check (bucket_id = 'music-catalog');

commit;
