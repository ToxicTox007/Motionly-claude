-- Task 16: shareable links for finished exports.
-- A signed-out viewer can watch, and optionally download, a finished export
-- through the API service. Only a SHA-256 fingerprint of each secret token is
-- stored; the raw token lives in the viewer page's URL fragment and is never
-- persisted or logged. Browser clients never touch this table directly.

begin;

create table if not exists public.export_share_links (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null,
  export_id uuid not null,
  token_digest text not null unique check (token_digest ~ '^[0-9a-f]{64}$'),
  allow_download boolean not null default true,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_by uuid not null references auth.users(id) on delete restrict,
  last_accessed_at timestamptz,
  created_at timestamptz not null default now(),
  constraint export_share_links_expiry check (expires_at > created_at),
  constraint export_share_links_project_tenant_fk
    foreign key (workspace_id, project_id)
    references public.projects (workspace_id, id)
    on delete cascade,
  constraint export_share_links_export_tenant_fk
    foreign key (workspace_id, project_id, export_id)
    references public.exports (workspace_id, project_id, id)
    on delete cascade,
  unique (workspace_id, project_id, id)
);

create index if not exists export_share_links_export_created_idx
  on public.export_share_links (workspace_id, project_id, export_id, created_at desc);

-- Enumeration-safe public resolve looks up only live links by their token
-- fingerprint; this partial index keeps that lookup fast.
create index if not exists export_share_links_live_token_idx
  on public.export_share_links (token_digest)
  where revoked_at is null;

alter table public.export_share_links enable row level security;

revoke all on public.export_share_links from public, anon, authenticated;

grant select, insert, update, delete on public.export_share_links to service_role;

commit;
