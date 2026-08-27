-- Consent-gated, aggregate post-performance feedback. This intentionally stores no
-- audience data, content text, raw provider payloads, tokens, or post copy.

create table if not exists public.performance_feedback_policies (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  metrics_ingestion_enabled boolean not null default false,
  suggestions_enabled boolean not null default false,
  retention_days integer not null default 90 check (retention_days between 1 and 365),
  policy_version text not null default 'unacknowledged' check (length(policy_version) <= 80),
  revision integer not null default 1 check (revision > 0),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workspace_id, user_id)
);

create table if not exists public.performance_feedback_policy_events (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  user_id uuid not null,
  policy_id uuid references public.performance_feedback_policies(id) on delete set null,
  revision integer not null,
  event_type text not null check (event_type in ('opt_in', 'opt_out', 'revoke_and_purge')),
  reason text not null default 'user_request',
  created_at timestamptz not null default now()
);

create table if not exists public.performance_feedback_metrics (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  publish_job_id uuid not null references public.publish_jobs(id) on delete cascade,
  platform text not null check (platform in ('tiktok', 'instagram', 'youtube')),
  observed_at timestamptz not null,
  views bigint check (views >= 0),
  average_view_duration_seconds double precision check (
    average_view_duration_seconds is null or average_view_duration_seconds >= 0
  ),
  completion_rate double precision check (completion_rate is null or completion_rate between 0 and 1),
  engagement_rate double precision check (engagement_rate is null or engagement_rate between 0 and 1),
  -- The following are the only retained edit features; each excludes content text.
  caption_preset text check (caption_preset is null or length(caption_preset) <= 80),
  hook_style text check (hook_style is null or length(hook_style) <= 80),
  duration_bucket text not null check (
    duration_bucket in ('under_30_seconds', '30_to_60_seconds', 'over_60_seconds', 'unknown')
  ),
  duration_seconds double precision check (duration_seconds is null or duration_seconds >= 0),
  template_id text check (template_id is null or length(template_id) <= 80),
  captions_enabled boolean not null,
  policy_revision integer not null check (policy_revision > 0),
  suggestion_eligible boolean not null default false,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (publish_job_id, observed_at),
  check (
    views is not null or average_view_duration_seconds is not null
    or completion_rate is not null or engagement_rate is not null
  )
);

create index if not exists performance_feedback_metrics_eligible_idx
  on public.performance_feedback_metrics (workspace_id, user_id, platform, observed_at desc)
  where suggestion_eligible;
create index if not exists performance_feedback_metrics_expiry_idx
  on public.performance_feedback_metrics (expires_at);

alter table public.performance_feedback_policies enable row level security;
alter table public.performance_feedback_policy_events enable row level security;
alter table public.performance_feedback_metrics enable row level security;
revoke all on public.performance_feedback_policies, public.performance_feedback_policy_events,
  public.performance_feedback_metrics from public, anon, authenticated;
grant all on public.performance_feedback_policies, public.performance_feedback_policy_events,
  public.performance_feedback_metrics to service_role;

create or replace function public.motionly_set_performance_feedback_policy(
  p_workspace_id uuid,
  p_user_id uuid,
  p_metrics_ingestion_enabled boolean,
  p_suggestions_enabled boolean,
  p_policy_version text,
  p_retention_days integer default null,
  p_expected_revision integer default null
) returns setof public.performance_feedback_policies
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  policy_row public.performance_feedback_policies;
  event_type_value text;
begin
  if p_policy_version is null or length(trim(p_policy_version)) = 0 then
    raise exception 'PERFORMANCE_FEEDBACK_POLICY_VERSION_REQUIRED' using errcode = '22023';
  end if;
  if p_retention_days is not null and p_retention_days not between 1 and 365 then
    raise exception 'PERFORMANCE_FEEDBACK_RETENTION_INVALID' using errcode = '22023';
  end if;

  select * into policy_row from public.performance_feedback_policies
    where workspace_id = p_workspace_id and user_id = p_user_id for update;
  if not found then
    if p_expected_revision is not null and p_expected_revision <> 0 then
      raise exception 'PERFORMANCE_FEEDBACK_POLICY_STALE' using errcode = '40001';
    end if;
    insert into public.performance_feedback_policies(
      workspace_id, user_id, metrics_ingestion_enabled, suggestions_enabled,
      retention_days, policy_version, revision, revoked_at
    ) values (
      p_workspace_id, p_user_id, p_metrics_ingestion_enabled, p_suggestions_enabled,
      coalesce(p_retention_days, 90), trim(p_policy_version), 1, null
    ) returning * into policy_row;
  else
    if p_expected_revision is not null and p_expected_revision <> policy_row.revision then
      raise exception 'PERFORMANCE_FEEDBACK_POLICY_STALE' using errcode = '40001';
    end if;
    update public.performance_feedback_policies set
      metrics_ingestion_enabled = p_metrics_ingestion_enabled,
      suggestions_enabled = p_suggestions_enabled,
      retention_days = coalesce(p_retention_days, policy_row.retention_days),
      policy_version = trim(p_policy_version),
      revision = policy_row.revision + 1,
      revoked_at = null,
      updated_at = now()
      where id = policy_row.id
      returning * into policy_row;
  end if;

  event_type_value := case when policy_row.metrics_ingestion_enabled
    then 'opt_in' else 'opt_out' end;
  insert into public.performance_feedback_policy_events(
    workspace_id, user_id, policy_id, revision, event_type, reason
  ) values (
    p_workspace_id, p_user_id, policy_row.id, policy_row.revision, event_type_value, 'user_request'
  );
  return query select * from public.performance_feedback_policies where id = policy_row.id;
end; $$;

create or replace function public.motionly_capture_performance_metric(
  p_workspace_id uuid,
  p_project_id uuid,
  p_user_id uuid,
  p_publish_job_id uuid,
  p_platform text,
  p_observed_at timestamptz,
  p_views bigint,
  p_average_view_duration_seconds double precision,
  p_completion_rate double precision,
  p_engagement_rate double precision,
  p_caption_preset text,
  p_hook_style text,
  p_duration_bucket text,
  p_duration_seconds double precision,
  p_template_id text,
  p_captions_enabled boolean
) returns setof public.performance_feedback_metrics
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  policy_row public.performance_feedback_policies;
  metric_row public.performance_feedback_metrics;
begin
  select * into policy_row from public.performance_feedback_policies
    where workspace_id = p_workspace_id and user_id = p_user_id for key share;
  if not found or policy_row.revoked_at is not null or not policy_row.metrics_ingestion_enabled then
    return;
  end if;
  if p_observed_at is null
    or (p_views is null and p_average_view_duration_seconds is null
      and p_completion_rate is null and p_engagement_rate is null) then
    raise exception 'PERFORMANCE_FEEDBACK_METRIC_INVALID' using errcode = '22023';
  end if;
  if not exists (
    select 1 from public.publish_jobs
    where id = p_publish_job_id and workspace_id = p_workspace_id and project_id = p_project_id
      and platform = p_platform and status = 'published'
  ) then
    raise exception 'PERFORMANCE_FEEDBACK_PUBLISH_INVALID' using errcode = '22023';
  end if;

  insert into public.performance_feedback_metrics(
    workspace_id, project_id, user_id, publish_job_id, platform, observed_at,
    views, average_view_duration_seconds, completion_rate, engagement_rate,
    caption_preset, hook_style, duration_bucket, duration_seconds, template_id,
    captions_enabled, policy_revision, suggestion_eligible, expires_at
  ) values (
    p_workspace_id, p_project_id, p_user_id, p_publish_job_id, p_platform, p_observed_at,
    p_views, p_average_view_duration_seconds, p_completion_rate, p_engagement_rate,
    p_caption_preset, p_hook_style, p_duration_bucket, p_duration_seconds, p_template_id,
    p_captions_enabled, policy_row.revision, policy_row.suggestions_enabled,
    now() + make_interval(days => policy_row.retention_days)
  ) on conflict (publish_job_id, observed_at) do update set
    views = excluded.views,
    average_view_duration_seconds = excluded.average_view_duration_seconds,
    completion_rate = excluded.completion_rate,
    engagement_rate = excluded.engagement_rate
  returning * into metric_row;

  return query select * from public.performance_feedback_metrics where id = metric_row.id;
end; $$;

create or replace function public.motionly_revoke_performance_feedback(
  p_workspace_id uuid,
  p_user_id uuid,
  p_reason text default 'user_request'
) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  policy_row public.performance_feedback_policies;
begin
  select * into policy_row from public.performance_feedback_policies
    where workspace_id = p_workspace_id and user_id = p_user_id for update;
  if not found then return; end if;
  update public.performance_feedback_policies set
    metrics_ingestion_enabled = false, suggestions_enabled = false, revoked_at = now(),
    revision = revision + 1, updated_at = now()
    where id = policy_row.id
    returning * into policy_row;
  delete from public.performance_feedback_metrics
    where workspace_id = p_workspace_id and user_id = p_user_id;
  insert into public.performance_feedback_policy_events(
    workspace_id, user_id, policy_id, revision, event_type, reason
  ) values (
    p_workspace_id, p_user_id, policy_row.id, policy_row.revision, 'revoke_and_purge', p_reason
  );
end; $$;

revoke all on function public.motionly_set_performance_feedback_policy(uuid,uuid,boolean,boolean,text,integer,integer) from public, anon, authenticated;
revoke all on function public.motionly_capture_performance_metric(uuid,uuid,uuid,uuid,text,timestamptz,bigint,double precision,double precision,double precision,text,text,text,double precision,text,boolean) from public, anon, authenticated;
revoke all on function public.motionly_revoke_performance_feedback(uuid,uuid,text) from public, anon, authenticated;
grant execute on function public.motionly_set_performance_feedback_policy(uuid,uuid,boolean,boolean,text,integer,integer) to service_role;
grant execute on function public.motionly_capture_performance_metric(uuid,uuid,uuid,uuid,text,timestamptz,bigint,double precision,double precision,double precision,text,text,text,double precision,text,boolean) to service_role;
grant execute on function public.motionly_revoke_performance_feedback(uuid,uuid,text) to service_role;
