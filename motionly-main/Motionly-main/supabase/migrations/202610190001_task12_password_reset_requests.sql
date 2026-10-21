-- Task 12: admin-assisted password reset and user admin.
--
-- A user who forgot their password asks for help from the public
-- /forgot-password page.  The request is recorded here (never confirming
-- whether the email exists), an admin finds it on the Users page and issues a
-- one-time GoTrue recovery link.  Every admin action (link created, ban,
-- unban, request dismissed) is recorded in an append-only audit log.
--
-- Both tables are service-role only: they are written by the API with the
-- service-role key, never read or written by browser roles.

begin;

create table public.password_reset_requests (
  id uuid primary key default gen_random_uuid(),
  -- The user may not exist (unknown email) and is never revealed to the viewer.
  user_id uuid,
  email text not null check (email = lower(email) and char_length(email) between 3 and 254),
  status text not null default 'pending'
    check (status in ('pending', 'link_issued', 'completed', 'dismissed')),
  -- A short, non-reversible digest of the viewer's address (never the address).
  viewer_hash text not null check (viewer_hash ~ '^[0-9a-f]{12}$'),
  handled_by uuid,
  handled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check ((status = 'pending') = (handled_at is null)),
  check ((status = 'pending') = (handled_by is null))
);

create index password_reset_requests_pending_idx
  on public.password_reset_requests (created_at desc)
  where status = 'pending';
create index password_reset_requests_email_idx
  on public.password_reset_requests (email, created_at desc);

create table public.admin_audit_events (
  id uuid primary key default gen_random_uuid(),
  actor_user_id uuid not null,
  action text not null check (char_length(action) between 1 and 80),
  target_user_id uuid,
  -- Context for the action; never secrets (no tokens, links, or passwords).
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index admin_audit_events_recent_idx
  on public.admin_audit_events (created_at desc);
create index admin_audit_events_target_idx
  on public.admin_audit_events (target_user_id, created_at desc)
  where target_user_id is not null;

create trigger password_reset_requests_touch_updated_at
before update on public.password_reset_requests
for each row execute function public.touch_updated_at();

-- Browser roles never touch these tables; only the service role does.
alter table public.password_reset_requests enable row level security;
alter table public.admin_audit_events enable row level security;
revoke all privileges on table public.password_reset_requests from public, anon, authenticated;
revoke all privileges on table public.admin_audit_events from public, anon, authenticated;
grant all privileges on table public.password_reset_requests to service_role;
grant all privileges on table public.admin_audit_events to service_role;

commit;
