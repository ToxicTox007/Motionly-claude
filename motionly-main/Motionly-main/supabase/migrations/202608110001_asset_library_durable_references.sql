-- Audit finding 1: asset-library media may only be addressed by tenant-owned
-- ObjectReference IDs. This migration is additive: legacy rows are retained in
-- a recoverable quarantine state until a service-role proof scan can link them.

begin;

alter table public.asset_library_items
  add column if not exists object_reference_id uuid,
  add column if not exists media_state text not null default 'not_applicable',
  add column if not exists quarantine_reason text,
  add column if not exists quarantined_at timestamptz,
  add column if not exists media_mime_type text,
  add column if not exists media_width integer,
  add column if not exists media_height integer,
  add column if not exists media_duration_ms bigint,
  add column if not exists recipe jsonb,
  add column if not exists source_key text;

-- Existing payload values are deliberately never treated as storage authority.
-- A media row with no independently provable ObjectReference stays visible only
-- as quarantined recovery evidence; no domain item or physical object is deleted.
update public.asset_library_items
set object_reference_id = null,
    media_state = 'quarantined',
    quarantine_reason = coalesce(quarantine_reason, 'legacy_reference_unproven'),
    quarantined_at = coalesce(quarantined_at, now())
where kind in ('broll', 'upload', 'export')
  and object_reference_id is null
  and media_state <> 'verified';

update public.asset_library_items
set media_state = 'not_applicable',
    quarantine_reason = null,
    quarantined_at = null
where kind in ('music', 'clip', 'brand_asset', 'style')
  and object_reference_id is null
  and media_state <> 'verified';

alter table public.asset_library_items
  drop constraint if exists asset_library_items_media_state_check,
  add constraint asset_library_items_media_state_check
    check (media_state in ('verified', 'quarantined', 'not_applicable')),
  drop constraint if exists asset_library_items_media_dimensions_check,
  add constraint asset_library_items_media_dimensions_check
    check (
      (media_width is null or media_width > 0)
      and (media_height is null or media_height > 0)
      and (media_duration_ms is null or media_duration_ms >= 0)
    ),
  drop constraint if exists asset_library_items_reference_state_check,
  add constraint asset_library_items_reference_state_check
    check (
      (media_state = 'verified'
        and object_reference_id is not null
        and source_project_id is not null
        and source_asset_id is not null)
      or (media_state = 'quarantined')
      or (media_state = 'not_applicable' and object_reference_id is null)
    );

do $asset_library_reference_fk$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'asset_library_items_reference_tenant_fk'
      and conrelid = 'public.asset_library_items'::regclass
  ) then
    alter table public.asset_library_items
      add constraint asset_library_items_reference_tenant_fk
      foreign key (workspace_id, source_project_id, object_reference_id)
      references public.object_references (workspace_id, project_id, id)
      on delete restrict
      not valid;
  end if;
end
$asset_library_reference_fk$;

create unique index if not exists asset_library_items_workspace_source_key_v2_idx
  on public.asset_library_items (workspace_id, source_key)
  where source_key is not null;
create index if not exists asset_library_items_reference_state_idx
  on public.asset_library_items (workspace_id, source_project_id, object_reference_id, media_state);

-- Proof-only means the function never creates ObjectReferences from a legacy
-- JSON payload or raw path. It can only link a row to a pre-existing active
-- reference whose tenant, owner, configured allowlist bucket, and generated
-- project prefix all match. Re-running converges without deleting any item.
create or replace function public.motionly_backfill_asset_library_references(
  p_allowed_buckets text[]
)
returns table (verified_count integer, quarantined_count integer)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_item public.asset_library_items%rowtype;
  v_reference public.object_references%rowtype;
  v_verified integer := 0;
  v_quarantined integer := 0;
begin
  if coalesce(cardinality(p_allowed_buckets), 0) = 0
     or exists (
       select 1 from unnest(p_allowed_buckets) as bucket
       where bucket is null or btrim(bucket) = ''
     ) then
    raise exception using errcode = '22023',
      message = 'at least one non-empty asset-library storage bucket is required';
  end if;

  for v_item in
    select *
    from public.asset_library_items
    where kind in ('broll', 'upload', 'export')
      and media_state = 'quarantined'
    for update skip locked
  loop
    select reference.* into v_reference
    from public.object_references as reference
    where reference.workspace_id = v_item.workspace_id
      and reference.project_id = v_item.source_project_id
      and reference.status = 'active'
      and reference.storage_bucket = any (p_allowed_buckets)
      and reference.storage_path like (
        'workspaces/' || v_item.workspace_id || '/projects/' || v_item.source_project_id || '/%'
      )
      and reference.owner_id = v_item.source_asset_id
      and (
        (v_item.kind = 'upload'
          and reference.owner_type = 'uploaded_video'
          and reference.object_type = 'source_video')
        or (v_item.kind = 'export'
          and reference.owner_type = 'export'
          and reference.object_type = 'export')
        or (v_item.kind = 'broll'
          and reference.owner_type = 'stock_asset'
          and reference.object_type = 'broll_asset')
      )
    order by reference.created_at desc
    limit 1;

    if found then
      update public.asset_library_items
      set object_reference_id = v_reference.id,
          media_state = 'verified',
          quarantine_reason = null,
          quarantined_at = null,
          updated_at = now()
      where id = v_item.id;
      v_verified := v_verified + 1;
    elsif v_item.quarantine_reason is distinct from 'legacy_reference_unproven'
       or v_item.object_reference_id is not null then
      update public.asset_library_items
      set object_reference_id = null,
          media_state = 'quarantined',
          quarantine_reason = 'legacy_reference_unproven',
          quarantined_at = coalesce(quarantined_at, now()),
          updated_at = now()
      where id = v_item.id;
      v_quarantined := v_quarantined + 1;
    end if;
  end loop;

  return query select v_verified, v_quarantined;
end;
$$;

revoke all on function public.motionly_backfill_asset_library_references(text[])
  from public, anon, authenticated;
grant execute on function public.motionly_backfill_asset_library_references(text[])
  to service_role;

commit;
