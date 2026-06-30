-- Preserve object locations before project/workspace cascades remove their database rows.

create table if not exists public.storage_deletion_queue (
  id uuid primary key default gen_random_uuid(),
  storage_bucket text not null,
  storage_path text not null,
  source_table text not null,
  queued_at timestamptz not null default now(),
  unique (storage_bucket, storage_path)
);
alter table public.storage_deletion_queue enable row level security;

create or replace function public.queue_deleted_storage_object() returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.storage_deletion_queue(storage_bucket, storage_path, source_table)
  values (old.storage_bucket, old.storage_path, tg_table_name)
  on conflict (storage_bucket, storage_path) do nothing;
  return old;
end;
$$;

drop trigger if exists uploaded_videos_queue_storage_delete on public.uploaded_videos;
create trigger uploaded_videos_queue_storage_delete
before delete on public.uploaded_videos
for each row execute function public.queue_deleted_storage_object();

drop trigger if exists exports_queue_storage_delete on public.exports;
create trigger exports_queue_storage_delete
before delete on public.exports
for each row execute function public.queue_deleted_storage_object();
