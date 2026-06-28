-- Phase 7: feedback is now a real persisted product workflow.

create table if not exists public.launch_feedback (
  id uuid primary key default gen_random_uuid(),
  category text not null check (category in ('general','bug','render_quality','billing','feature_request')),
  message text not null check (char_length(message) between 10 and 5000),
  page_url text,
  user_agent text,
  created_at timestamptz not null default now()
);

alter table public.launch_feedback enable row level security;
-- Writes go through the rate-limited API service role. No direct client policy.
