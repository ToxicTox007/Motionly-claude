begin;
select plan(12);

select has_table(
  'public', 'generic_media_upload_sessions',
  'generic verified media sessions are persistent'
);
select has_table(
  'public', 'generic_media_upload_session_parts',
  'generic verified media parts are persistent'
);
select ok(
  (select count(*) from information_schema.columns
   where table_schema = 'public'
     and table_name = 'generic_media_upload_sessions'
     and column_name = any(array[
       'owner_type', 'owner_id', 'object_type', 'object_reference_id',
       'quota_reservation_id', 'verified_size_bytes', 'verified_sha256', 'expires_at'
     ])) = 8,
  'generic sessions persist owner, durable reference, quota, verification, and expiry'
);
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.generic_media_upload_session_parts'::regclass
      and contype = 'u'
      and pg_get_constraintdef(oid) = 'UNIQUE (session_id, part_number)'
  ),
  'one generic part row exists per session part number'
);
select ok(
  (select count(*) from information_schema.columns
   where table_schema = 'public'
     and table_name = 'stock_assets'
     and column_name = any(array[
       'media_upload_session_id', 'object_reference_id', 'media_state',
       'verified_size_bytes', 'verified_sha256', 'storage_quota_reservation_id'
     ])) = 6,
  'B-roll assets persist server-verified media identity without raw metadata authority'
);
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.stock_assets'::regclass
      and conname = 'stock_assets_media_reference_tenant_fk'
      and contype = 'f'
  ),
  'B-roll durable references are tenant and project scoped'
);
select ok(
  to_regprocedure(
    'public.motionly_finalize_generic_media_upload_session(uuid,uuid,uuid,uuid,bigint,text)'
  ) is not null
  and to_regprocedure(
    'public.motionly_terminate_generic_media_upload_session(uuid,uuid,uuid,text,text,boolean)'
  ) is not null
  and to_regprocedure('public.motionly_expire_generic_media_upload_sessions()') is not null,
  'generic lifecycle finalization, cleanup, and expiry functions exist'
);
select ok(
  not has_function_privilege(
    'authenticated',
    'public.motionly_finalize_generic_media_upload_session(uuid,uuid,uuid,uuid,bigint,text)',
    'EXECUTE'
  )
  and has_function_privilege(
    'service_role',
    'public.motionly_finalize_generic_media_upload_session(uuid,uuid,uuid,uuid,bigint,text)',
    'EXECUTE'
  ),
  'only service role may finalize generic media sessions'
);
select ok(
  not has_table_privilege('authenticated', 'public.generic_media_upload_sessions', 'INSERT')
  and has_table_privilege('service_role', 'public.generic_media_upload_sessions', 'INSERT'),
  'only service role may mutate generic media sessions'
);
select ok(
  (select relrowsecurity
   from pg_class
   where oid = 'public.generic_media_upload_sessions'::regclass)
  and (select relrowsecurity
       from pg_class
       where oid = 'public.generic_media_upload_session_parts'::regclass),
  'generic media session tables enforce RLS'
);
select ok(
  to_regclass('public.generic_media_sessions_owner_idx') is not null
  and to_regclass('public.stock_assets_verified_media_idx') is not null,
  'owner and verified B-roll reference lookups are indexed'
);
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.object_references'::regclass
      and conname = 'object_references_owner_type_check'
      and pg_get_constraintdef(oid) like '%style_profile%'
  ),
  'durable references permit verified style-profile ownership'
);

select * from finish();
rollback;
