-- Phase 6: profile authority and retention metadata.

alter table public.profiles add column if not exists company text;
alter table public.profiles add column if not exists role text;

create or replace function public.update_own_profile(
  profile_full_name text,
  profile_company text,
  profile_role text
) returns void
language plpgsql
security definer
set search_path = public, auth
as $$
begin
  if auth.uid() is null then
    raise exception 'authentication required';
  end if;
  update public.profiles
  set full_name = nullif(left(trim(profile_full_name), 120), ''),
      company = nullif(left(trim(profile_company), 120), ''),
      role = nullif(left(trim(profile_role), 80), ''),
      updated_at = now()
  where id = auth.uid();

  update auth.users
  set raw_user_meta_data = coalesce(raw_user_meta_data, '{}'::jsonb) || jsonb_build_object(
    'full_name', left(trim(profile_full_name), 120),
    'company', left(trim(profile_company), 120),
    'role', left(trim(profile_role), 80)
  ), updated_at = now()
  where id = auth.uid();
end;
$$;

revoke all on function public.update_own_profile(text, text, text) from public;
grant execute on function public.update_own_profile(text, text, text) to authenticated;

create table if not exists public.retention_runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  status text not null check (status in ('running', 'completed', 'failed')),
  deleted_counts jsonb not null default '{}'::jsonb,
  error_message text
);

alter table public.retention_runs enable row level security;
-- Service-role only: intentionally no client policy.
