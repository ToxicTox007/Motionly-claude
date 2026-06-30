alter table public.publish_connections
  add column if not exists refresh_token_expires_at timestamptz,
  add column if not exists scopes text[] not null default '{}',
  add column if not exists connection_status text not null default 'active',
  add column if not exists last_refresh_at timestamptz,
  add column if not exists last_refresh_failure_reason text;

alter table public.publish_connections
  drop constraint if exists publish_connections_connection_status_check;

alter table public.publish_connections
  add constraint publish_connections_connection_status_check
  check (connection_status in ('active', 'reconnect_required'));

create index if not exists publish_connections_status_expiry_idx
  on public.publish_connections(connection_status, token_expires_at);

alter table public.publish_jobs
  add column if not exists provider_operation_id text,
  add column if not exists error_code text,
  add column if not exists error_category text,
  add column if not exists user_message text,
  add column if not exists retryable boolean,
  add column if not exists reconnect_required boolean not null default false,
  add column if not exists retry_after_seconds integer,
  add column if not exists retry_count integer not null default 0,
  add column if not exists duplicate_publish_risk boolean not null default false;

alter table public.publish_jobs
  drop constraint if exists publish_jobs_status_check;

alter table public.publish_jobs
  add constraint publish_jobs_status_check
  check (
    status in (
      'queued',
      'refreshing_token',
      'publishing',
      'uploading',
      'processing',
      'published',
      'retry_scheduled',
      'reconnect_required',
      'duplicate_risk_needs_review',
      'failed',
      'cancelled'
    )
  );

create unique index if not exists publish_jobs_inflight_dedupe_idx
  on public.publish_jobs(workspace_id, export_id, connection_id)
  where status in ('queued', 'refreshing_token', 'publishing', 'uploading', 'processing', 'retry_scheduled');
