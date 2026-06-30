-- Owned stock/generated B-roll objects participate in the storage deletion queue.

create or replace function public.queue_deleted_stock_storage_object() returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  object_bucket text := old.raw_metadata->>'storage_bucket';
  object_path text := old.raw_metadata->>'storage_path';
begin
  if object_bucket is not null and object_path is not null then
    insert into public.storage_deletion_queue(storage_bucket, storage_path, source_table)
    values (object_bucket, object_path, tg_table_name)
    on conflict (storage_bucket, storage_path) do nothing;
  end if;
  return old;
end;
$$;

drop trigger if exists stock_assets_queue_storage_delete on public.stock_assets;
create trigger stock_assets_queue_storage_delete
before delete on public.stock_assets
for each row execute function public.queue_deleted_stock_storage_object();
