begin;
select plan(53);

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
select has_table('public', 'storage_deletion_queue', 'storage deletion is durable');
select has_function(
  'public', 'queue_deleted_storage_object', array[]::text[],
  'storage records queue object cleanup before cascade deletion'
);
select has_trigger(
  'public', 'uploaded_videos', 'uploaded_videos_queue_storage_delete',
  'source deletion queues storage cleanup'
);
select has_trigger(
  'public', 'exports', 'exports_queue_storage_delete',
  'export deletion queues storage cleanup'
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
select has_index('public', 'usage_ledger', 'usage_ledger_outcome_source_unique_idx', 'successful render usage is idempotent');
select has_index('public', 'ai_edit_feedback_events', 'ai_edit_feedback_decision_outcome_unique_idx', 'kept outcomes are idempotent');
select has_trigger('public', 'stock_assets', 'stock_assets_queue_storage_delete', 'owned stock deletion queues storage cleanup');
select ok(
  (select pg_get_constraintdef(oid) like '%render_succeeded%'
   from pg_constraint
   where conrelid = 'public.usage_ledger'::regclass
     and conname = 'usage_ledger_event_type_check'),
  'usage ledger supports successful render outcomes'
);
select has_table('public', 'worker_heartbeats', 'worker liveness is persisted');
select has_table('public', 'provider_slo_events', 'provider SLO events are persisted');
select has_table('public', 'cost_attribution_events', 'cost attribution is persisted');
select has_index('public', 'provider_slo_events', 'provider_slo_events_provider_created_idx', 'provider SLO history is queryable');
select has_index('public', 'cost_attribution_events', 'cost_attribution_workspace_created_idx', 'workspace costs are queryable');
select has_index('public', 'cost_attribution_events', 'cost_attribution_source_unique_idx', 'cost events are idempotent');
select ok(
  (select pg_get_constraintdef(oid) like '%canceled%'
   from pg_constraint
   where conrelid = 'public.phase5_resources'::regclass
     and conname = 'phase5_resources_status_check'),
  'batch resources support cancellation'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.worker_heartbeats'::regclass),
  'worker heartbeats are service-role only'
);

select hasnt_table('public', 'audit_logs', 'unused audit logs table is removed');
select has_table('public', 'launch_feedback', 'launch feedback is persisted');
select hasnt_table('public', 'launch_ops_events', 'unused launch ops table is removed');

select has_column('public', 'profiles', 'company', 'profiles store company identity');
select has_column('public', 'profiles', 'role', 'profiles store creator role');
select has_function(
  'public',
  'update_own_profile',
  array['text', 'text', 'text'],
  'profile updates use one transactional function'
);
select has_table('public', 'retention_runs', 'retention execution is persisted');
select ok(
  (select relrowsecurity from pg_class where oid = 'public.retention_runs'::regclass),
  'retention execution records are service-role only'
);

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
