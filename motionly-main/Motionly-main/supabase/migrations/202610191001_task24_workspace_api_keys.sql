-- Task 24: API keys for the Agency plan.
--
-- Agency-plan workspaces call the Motionly API with keys of the form
-- `mtly_<random>`. Only a SHA-256 fingerprint of each key is ever stored; the
-- raw secret is shown to the creator exactly once and never persisted or
-- logged. A short display prefix (e.g. `mtly_ab`) and the last four characters
-- are kept so a key can be recognised in the UI without revealing it.
--
-- Browser clients never touch this table directly: RLS is on and only
-- service_role (the API) may read or write. Keys authenticate to the API via
-- `Authorization: Bearer mtly_...`; the API looks the key up by fingerprint
-- (constant-time compare) and resolves it to the key's workspace.
--
-- Forward-only. Reserved timestamp 2026101910xx (Task 24).

begin;

create table if not exists public.workspace_api_keys (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  name text not null check (char_length(trim(name)) between 1 and 120),
  -- Only a SHA-256 fingerprint of the raw key is stored, never the key itself.
  fingerprint text not null unique check (fingerprint ~ '^[0-9a-f]{64}$'),
  -- Display-only hints so a key is recognisable in the UI without the secret.
  prefix text not null check (prefix ~ '^mtly_[0-9A-Za-z_-]{1,12}$'),
  last4 text not null check (last4 ~ '^[0-9A-Za-z_-]{4}$'),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at timestamptz,
  unique (workspace_id, id)
);

create index if not exists workspace_api_keys_workspace_created_idx
  on public.workspace_api_keys (workspace_id, created_at desc);

-- Authentication looks up live (non-revoked) keys by fingerprint; this partial
-- index keeps that hot path fast and enumeration-safe.
create index if not exists workspace_api_keys_live_fingerprint_idx
  on public.workspace_api_keys (fingerprint)
  where revoked_at is null;

alter table public.workspace_api_keys enable row level security;

-- Browser/tenant roles never read API keys directly; the API mediates all access.
revoke all on public.workspace_api_keys from public, anon, authenticated;

grant select, insert, update, delete on public.workspace_api_keys to service_role;

commit;
