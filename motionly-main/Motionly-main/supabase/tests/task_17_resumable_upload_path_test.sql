begin;
select plan(4);

-- Task 17: the legacy upload completion functions used only by the removed
-- one-shot /uploads/init + /uploads/{video_id}/complete endpoints are dropped
-- by 202610190401_drop_legacy_upload_completion_functions.sql.
select is(
  (select count(*)::integer from pg_proc p
     join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = 'motionly_claim_legacy_upload'),
  0,
  'motionly_claim_legacy_upload is dropped'
);
select is(
  (select count(*)::integer from pg_proc p
     join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = 'motionly_finalize_legacy_upload'),
  0,
  'motionly_finalize_legacy_upload is dropped'
);

-- The resumable path and upload cancellation still rely on these, so they stay.
select has_function(
  'public',
  'motionly_initialize_upload',
  'the resumable upload path still initializes uploads'
);
select has_function(
  'public',
  'motionly_cancel_legacy_upload',
  'upload cancellation still uses motionly_cancel_legacy_upload'
);

select * from finish();
rollback;
