begin;
select plan(32);

select has_column('public', 'render_jobs', 'uploaded_video_id', 'render jobs reference uploads');
select has_column('public', 'render_jobs', 'caption_track_id', 'render jobs reference captions');
select has_column('public', 'render_jobs', 'planner_version', 'render jobs store planner version');
select has_column('public', 'render_jobs', 'manifest', 'render jobs store manifest');
select has_column('public', 'render_jobs', 'keep_segments', 'render jobs store keep segments');
select has_column('public', 'render_jobs', 'specs', 'render jobs store output specs');
select has_column('public', 'render_jobs', 'progress_pct', 'render jobs store progress');
select has_column('public', 'exports', 'format', 'exports store format');
select has_column('public', 'exports', 'width', 'exports store width');
select has_column('public', 'exports', 'height', 'exports store height');
select has_column('public', 'exports', 'fps', 'exports store fps');
select has_column('public', 'exports', 'duration_ms', 'exports store duration');
select has_column('public', 'exports', 'file_size_bytes', 'exports store file size');
select has_column('public', 'exports', 'thumbnail_url', 'exports store thumbnail URL');
select has_column('public', 'exports', 'settings_summary', 'exports store settings summary');
select has_column('public', 'exports', 'restored_from_export_id', 'exports track restoration source');

select ok(
  (select is_nullable = 'NO' from information_schema.columns
   where table_schema = 'public' and table_name = 'render_jobs'
     and column_name = 'uploaded_video_id'),
  'fresh render jobs require uploaded_video_id'
);
select ok(
  (select is_nullable = 'NO' from information_schema.columns
   where table_schema = 'public' and table_name = 'render_jobs'
     and column_name = 'caption_track_id'),
  'fresh render jobs require caption_track_id'
);
select ok(
  (select is_nullable = 'NO' from information_schema.columns
   where table_schema = 'public' and table_name = 'exports'
     and column_name = 'render_job_id'),
  'fresh exports require render_job_id'
);

select ok(
  (select array_agg(enumlabel::text order by enumsortorder) @>
      array['queued','preparing','rendering','uploading','complete','failed','canceled']::text[]
   from pg_enum where enumtypid = 'public.render_job_status'::regtype),
  'render status enum contains every application state'
);

select col_is_pk('public', 'subscriptions', 'workspace_id', 'workspace is subscription primary key');
select has_column('public', 'subscriptions', 'plan', 'subscriptions use canonical plan');
select has_column('public', 'subscriptions', 'status', 'subscriptions use canonical status');
select has_column('public', 'subscriptions', 'stripe_price_id', 'subscriptions store Stripe price');
select has_column('public', 'usage_ledger', 'source_id', 'usage entries store source ID');
select has_index('public', 'usage_ledger', 'usage_ledger_workspace_created_at_idx', 'usage has canonical workspace timestamp index');

select hasnt_table('public', 'audit_logs', 'unused audit logs table is removed');
select hasnt_table('public', 'launch_feedback', 'unused launch feedback table is removed');
select hasnt_table('public', 'launch_ops_events', 'unused launch ops table is removed');

select has_function(
  'public',
  'motionly_legacy_plan_timeline',
  array['jsonb'],
  'legacy plans have an explicit timeline migration function'
);
select is(
  (public.motionly_legacy_plan_timeline(
    '{"cuts":[{"start_ms":0,"end_ms":1000,"reason":"a"},{"start_ms":3000,"end_ms":4500,"reason":"b"}]}'::jsonb
  )->>'duration_ms')::integer,
  2500,
  'legacy timeline migration calculates assembled output duration'
);
select is(
  (public.motionly_legacy_plan_timeline(
    '{"cuts":[{"start_ms":0,"end_ms":1000,"reason":"a"},{"start_ms":3000,"end_ms":4500,"reason":"b"}]}'::jsonb
  )->'clips'->1->>'output_start_ms')::integer,
  1000,
  'legacy timeline migration maps source clips contiguously in output time'
);

select * from finish();
rollback;
