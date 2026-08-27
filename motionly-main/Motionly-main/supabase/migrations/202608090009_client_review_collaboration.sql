-- Task 19: client review collaboration on immutable editor revisions.
-- Browser clients use the API service; no direct table policies are granted.

begin;

create table if not exists public.client_review_revisions (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null,
  edit_plan_id uuid not null,
  edit_plan_revision integer not null check (edit_plan_revision > 0),
  uploaded_video_id uuid not null,
  source_media_revision integer not null check (source_media_revision > 0),
  source_object_reference_id uuid not null,
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  plan_snapshot jsonb not null,
  plan_hash text not null check (plan_hash ~ '^[0-9a-f]{64}$'),
  status text not null default 'open' check (status in ('open', 'approved', 'rejected')),
  status_revision integer not null default 1 check (status_revision > 0),
  decision_reason text,
  decision_idempotency_key text,
  decided_by uuid references auth.users(id) on delete set null,
  decided_at timestamptz,
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint client_review_revisions_project_tenant_fk
    foreign key (workspace_id, project_id)
    references public.projects (workspace_id, id)
    on delete cascade,
  constraint client_review_revisions_plan_tenant_fk
    foreign key (workspace_id, project_id, edit_plan_id)
    references public.edit_plans (workspace_id, project_id, id)
    on delete restrict,
  constraint client_review_revisions_upload_tenant_fk
    foreign key (workspace_id, project_id, uploaded_video_id)
    references public.uploaded_videos (workspace_id, project_id, id)
    on delete restrict,
  constraint client_review_revisions_reference_tenant_fk
    foreign key (workspace_id, project_id, source_object_reference_id)
    references public.object_references (workspace_id, project_id, id)
    on delete restrict,
  constraint client_review_revisions_decision_shape check (
    (status = 'open' and decided_at is null)
    or (status in ('approved', 'rejected') and decided_at is not null)
  ),
  unique (workspace_id, project_id, id)
);

create index if not exists client_review_revisions_project_created_idx
  on public.client_review_revisions (workspace_id, project_id, created_at desc);
create index if not exists client_review_revisions_plan_revision_idx
  on public.client_review_revisions (workspace_id, project_id, edit_plan_id, edit_plan_revision);

create table if not exists public.client_review_comments (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  review_id uuid not null,
  parent_comment_id uuid,
  timecode_ms integer check (timecode_ms is null or timecode_ms >= 0),
  body text not null check (char_length(body) between 1 and 2000),
  author_user_id uuid references auth.users(id) on delete set null,
  author_name text check (author_name is null or char_length(author_name) between 1 and 120),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz,
  constraint client_review_comments_review_tenant_fk
    foreign key (workspace_id, project_id, review_id)
    references public.client_review_revisions (workspace_id, project_id, id)
    on delete cascade,
  unique (workspace_id, project_id, id)
);

alter table public.client_review_comments
  add constraint client_review_comments_parent_tenant_fk
  foreign key (workspace_id, project_id, parent_comment_id)
  references public.client_review_comments (workspace_id, project_id, id)
  on delete cascade;

create index if not exists client_review_comments_review_created_idx
  on public.client_review_comments (workspace_id, project_id, review_id, created_at, id);

create table if not exists public.client_review_comment_assignees (
  workspace_id uuid not null,
  project_id uuid not null,
  review_id uuid not null,
  comment_id uuid not null,
  user_id uuid not null,
  assigned_by uuid references auth.users(id) on delete set null,
  assigned_at timestamptz not null default now(),
  primary key (comment_id, user_id),
  constraint client_review_comment_assignees_comment_tenant_fk
    foreign key (workspace_id, project_id, comment_id)
    references public.client_review_comments (workspace_id, project_id, id)
    on delete cascade,
  constraint client_review_comment_assignees_review_tenant_fk
    foreign key (workspace_id, project_id, review_id)
    references public.client_review_revisions (workspace_id, project_id, id)
    on delete cascade,
  constraint client_review_comment_assignees_member_tenant_fk
    foreign key (workspace_id, user_id)
    references public.workspace_members (workspace_id, user_id)
    on delete cascade
);

create index if not exists client_review_comment_assignees_user_idx
  on public.client_review_comment_assignees (workspace_id, user_id, assigned_at desc);

create table if not exists public.client_review_share_links (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  review_id uuid not null,
  token_digest text not null unique check (token_digest ~ '^[0-9a-f]{64}$'),
  can_comment boolean not null default true,
  can_decide boolean not null default false,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_by uuid not null references auth.users(id) on delete restrict,
  last_accessed_at timestamptz,
  created_at timestamptz not null default now(),
  constraint client_review_share_links_expiry check (expires_at > created_at),
  constraint client_review_share_links_review_tenant_fk
    foreign key (workspace_id, project_id, review_id)
    references public.client_review_revisions (workspace_id, project_id, id)
    on delete cascade,
  unique (workspace_id, project_id, id)
);

create index if not exists client_review_share_links_review_expiry_idx
  on public.client_review_share_links (review_id, expires_at)
  where revoked_at is null;

-- Review-link signing is audited as a first-class capability principal.
do $task19$
declare
  existing_constraint record;
begin
  for existing_constraint in
    select constraint_name
    from pg_constraint
    where conrelid = 'public.signed_url_grants'::regclass
      and contype = 'c'
      and (
        pg_get_constraintdef(oid) like '%principal_type%'
        or pg_get_constraintdef(oid) like '%principal_id%'
      )
  loop
    execute format(
      'alter table public.signed_url_grants drop constraint %I',
      existing_constraint.constraint_name
    );
  end loop;
end;
$task19$;

alter table public.signed_url_grants
  add constraint signed_url_grants_principal_shape check (
    (principal_type = 'worker' and principal_id is null)
    or (principal_type in ('user', 'share_link') and principal_id is not null)
  ),
  add constraint signed_url_grants_principal_type check (
    principal_type in ('user', 'worker', 'share_link')
  );

alter table public.client_review_revisions enable row level security;
alter table public.client_review_comments enable row level security;
alter table public.client_review_comment_assignees enable row level security;
alter table public.client_review_share_links enable row level security;

revoke all on public.client_review_revisions,
  public.client_review_comments,
  public.client_review_comment_assignees,
  public.client_review_share_links
from public, anon, authenticated;

grant select, insert, update, delete on public.client_review_revisions,
  public.client_review_comments,
  public.client_review_comment_assignees,
  public.client_review_share_links
to service_role;

commit;
