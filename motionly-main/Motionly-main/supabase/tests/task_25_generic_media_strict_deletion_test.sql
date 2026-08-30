begin;
select plan(14);

select has_table(
  'public', 'generic_media_deletion_jobs',
  'generic media cleanup jobs survive relational owner cascades'
);
select ok(
  (select count(*) from information_schema.columns
   where table_schema = 'public'
     and table_name = 'generic_media_upload_sessions'
     and column_name = 'deletion_requested_at') = 1,
  'generic sessions record durable deletion intent'
);
select ok(
  (select count(*) from information_schema.columns
   where table_schema = 'public'
     and table_name = 'storage_deletion_queue'
     and column_name = 'generic_media_deletion_job_id') = 1,
  'storage deletion rows can be tied to a generic cleanup job'
);
select ok(
  to_regprocedure(
    'public.motionly_create_generic_media_upload_session(uuid,uuid,uuid,uuid,text,uuid,text,text,text,text,text,bigint,text,bigint,timestamptz,jsonb)'
  ) is not null,
  'generic session creation has one atomic quota/session/parts RPC'
);
select ok(
  to_regprocedure('public.motionly_request_generic_media_deletion(uuid,uuid,uuid)') is not null
  and to_regprocedure('public.motionly_ack_generic_media_deletion_object(uuid,uuid)') is not null,
  'generic completed deletion uses explicit request and fenced acknowledgement RPCs'
);
select ok(
  exists (
    select 1
    from pg_constraint
    where conrelid = 'public.generic_media_deletion_jobs'::regclass
      and contype = 'u'
      and pg_get_constraintdef(oid) = 'UNIQUE (session_id)'
  ),
  'one durable generic cleanup job exists per upload session'
);
select ok(
  (select relrowsecurity
   from pg_class
   where oid = 'public.generic_media_deletion_jobs'::regclass)
  and not has_table_privilege('authenticated', 'public.generic_media_deletion_jobs', 'INSERT')
  and has_table_privilege('service_role', 'public.generic_media_deletion_jobs', 'INSERT'),
  'generic cleanup jobs are RLS-protected and service-owned'
);
select ok(
  not has_function_privilege(
    'authenticated',
    'public.motionly_create_generic_media_upload_session(uuid,uuid,uuid,uuid,text,uuid,text,text,text,text,text,bigint,text,bigint,timestamptz,jsonb)',
    'EXECUTE'
  )
  and has_function_privilege(
    'service_role',
    'public.motionly_create_generic_media_upload_session(uuid,uuid,uuid,uuid,text,uuid,text,text,text,text,text,bigint,text,bigint,timestamptz,jsonb)',
    'EXECUTE'
  ),
  'only service role can atomically create generic upload state'
);
select ok(
  not has_function_privilege(
    'authenticated',
    'public.motionly_ack_generic_media_deletion_object(uuid,uuid)',
    'EXECUTE'
  )
  and has_function_privilege(
    'service_role',
    'public.motionly_ack_generic_media_deletion_object(uuid,uuid)',
    'EXECUTE'
  ),
  'only service role can acknowledge physical generic-media deletion'
);
select ok(
  position(
    'generic_media_deletion_job_id is null'
    in lower(
      pg_get_functiondef('public.motionly_ack_storage_deletion_queue(uuid,uuid)'::regprocedure)
    )
  ) > 0,
  'general queue acknowledgement cannot bypass generic-media quota fencing'
);
select ok(
  position(
    'quota_reservation_id = null'
    in lower(
      pg_get_functiondef(
        'public.motionly_enqueue_generic_media_deletion(uuid,uuid,uuid,uuid,text,text,text)'::regprocedure
      )
    )
  ) > 0,
  'generic cleanup queue rows do not expose a direct quota transition'
);
select ok(
  exists (
    select 1 from pg_trigger
    where tgrelid = 'public.generic_media_upload_sessions'::regclass
      and tgname = 'generic_media_upload_sessions_queue_storage_delete'
      and not tgisinternal
  ),
  'deleting a generic session queues durable cleanup before cascade removal'
);
select ok(
  exists (
    select 1 from pg_trigger
    where tgrelid = 'public.object_references'::regclass
      and tgname = 'object_references_queue_generic_media_retirement'
      and not tgisinternal
  ),
  'retiring a linked object reference enters generic durable cleanup'
);
select ok(
  position(
    'generic_media_deletion_jobs'
    in pg_get_functiondef('public.motionly_expire_storage_quota_reservations()'::regprocedure)
  ) > 0,
  'the quota orphan sweeper excludes pending generic-media deletion jobs'
);

select * from finish();
rollback;
