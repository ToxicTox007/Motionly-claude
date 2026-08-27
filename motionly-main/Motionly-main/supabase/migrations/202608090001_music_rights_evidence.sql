-- Server-authoritative catalog rights. Existing selections are deliberately
-- unverified until a current catalog grant is revalidated by the API.

alter table public.licensed_music_tracks
  add column if not exists rights_status text,
  add column if not exists rights_grant_id text,
  add column if not exists rights_grant_version text,
  add column if not exists rights_valid_from timestamptz,
  add column if not exists rights_valid_until timestamptz,
  add column if not exists permitted_uses text[] not null default array[]::text[],
  add column if not exists license_evidence_hash text,
  add column if not exists rights_revision integer not null default 1,
  add column if not exists rights_revoked_at timestamptz;

alter table public.licensed_music_tracks
  drop constraint if exists licensed_music_tracks_rights_status_check,
  add constraint licensed_music_tracks_rights_status_check
    check (rights_status is null or rights_status in ('cleared', 'revoked', 'expired')),
  drop constraint if exists licensed_music_tracks_rights_window_check,
  add constraint licensed_music_tracks_rights_window_check
    check (rights_valid_until is null or rights_valid_from is null or rights_valid_until > rights_valid_from),
  drop constraint if exists licensed_music_tracks_rights_evidence_check,
  add constraint licensed_music_tracks_rights_evidence_check
    check (license_evidence_hash is null or license_evidence_hash ~ '^[0-9A-Fa-f]{32,128}$');

create index if not exists licensed_music_tracks_rights_lookup_idx
  on public.licensed_music_tracks (active, rights_status, rights_valid_until);

alter table public.project_music_selections
  add column if not exists rights_snapshot jsonb not null default '{}'::jsonb;

alter table public.project_music_selections
  drop constraint if exists project_music_selections_rights_snapshot_object,
  add constraint project_music_selections_rights_snapshot_object
    check (jsonb_typeof(rights_snapshot) = 'object');

-- Browser users retain read access but cannot forge a catalog track, selection,
-- rights snapshot, or legacy rights_confirmed value through PostgREST.
drop policy if exists "members manage music tracks" on public.music_tracks;
drop policy if exists "members manage project music selections" on public.project_music_selections;
revoke insert, update, delete on public.music_tracks from authenticated, anon, public;
revoke insert, update, delete on public.project_music_selections from authenticated, anon, public;
grant all on public.music_tracks to service_role;
grant all on public.project_music_selections to service_role;
revoke all on public.licensed_music_tracks from authenticated, anon, public;
grant all on public.licensed_music_tracks to service_role;
