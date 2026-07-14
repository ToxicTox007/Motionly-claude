begin;
select plan(19);

-- ============================================================================
-- Setup: create users, workspaces, projects, uploads, transcripts, captions, plans
-- ============================================================================

insert into auth.users (id, email, raw_user_meta_data)
values
  ('15000000-0000-4000-8000-000000000001', 'task5-owner@rev.test', '{}'::jsonb),
  ('15000000-0000-4000-8000-000000000002', 'task5-outsider@rev.test', '{}'::jsonb);

insert into public.workspaces (id, name, owner_user_id)
values
  ('35000000-0000-4000-8000-000000000001', 'Task 5 workspace', '15000000-0000-4000-8000-000000000001');

insert into public.workspace_members (workspace_id, user_id, role, status)
values
  ('35000000-0000-4000-8000-000000000001', '15000000-0000-4000-8000-000000000001', 'owner', 'active');

set local role service_role;

insert into public.projects (id, workspace_id, created_by, title)
values ('45000000-0000-4000-8000-000000000001', '35000000-0000-4000-8000-000000000001', '15000000-0000-4000-8000-000000000001', 'Task 5 project');

insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path,
  original_filename, mime_type, byte_size, file_size_bytes
) values (
  '55000000-0000-4000-8000-000000000001',
  '35000000-0000-4000-8000-000000000001',
  '45000000-0000-4000-8000-000000000001',
  'raw-uploads', 'task5/video.mp4', 'video.mp4', 'video/mp4', 1024, 1024
);

insert into public.transcripts (
  id, workspace_id, project_id, uploaded_video_id, provider, model
) values (
  '65000000-0000-4000-8000-000000000001',
  '35000000-0000-4000-8000-000000000001',
  '45000000-0000-4000-8000-000000000001',
  '55000000-0000-4000-8000-000000000001', 'fixture', 'fixture'
);

insert into public.caption_tracks (
  id, workspace_id, project_id, transcript_id, preset
) values (
  '75000000-0000-4000-8000-000000000001',
  '35000000-0000-4000-8000-000000000001',
  '45000000-0000-4000-8000-000000000001',
  '65000000-0000-4000-8000-000000000001', 'founder_clean'
);

-- ============================================================================
-- Test 1: edit_plans.revision starts at 1
-- ============================================================================

insert into public.edit_plans (
  id, workspace_id, project_id, transcript_id, version, style,
  target_duration_s, provider, model, cache_key, schema_hash, plan, summary
) values (
  '85000000-0000-4000-8000-000000000001',
  '35000000-0000-4000-8000-000000000001',
  '45000000-0000-4000-8000-000000000001',
  '65000000-0000-4000-8000-000000000001', 1, 'founder',
  30, 'fixture', 'fixture', 'task5-plan-a', 'task5-hash-a',
  '{"cuts": []}'::jsonb, '{"title": "test"}'::jsonb
);

select is(
  (select revision from public.edit_plans where id = '85000000-0000-4000-8000-000000000001'),
  1,
  'edit_plans.revision starts at 1'
);

-- ============================================================================
-- Test 2: revision auto-increments on plan update
-- ============================================================================

update public.edit_plans
set plan = '{"cuts": [{"start": 0, "end": 5000}]}'::jsonb
where id = '85000000-0000-4000-8000-000000000001';

select is(
  (select revision from public.edit_plans where id = '85000000-0000-4000-8000-000000000001'),
  2,
  'revision auto-increments on plan JSONB update'
);

-- ============================================================================
-- Test 3: revision does NOT increment when plan unchanged
-- ============================================================================

update public.edit_plans
set feedback = 'some feedback'
where id = '85000000-0000-4000-8000-000000000001';

select is(
  (select revision from public.edit_plans where id = '85000000-0000-4000-8000-000000000001'),
  2,
  'revision does NOT increment when plan column unchanged'
);

-- ============================================================================
-- Test 4: media_revision starts at 1
-- ============================================================================

select is(
  (select media_revision from public.uploaded_videos where id = '55000000-0000-4000-8000-000000000001'),
  1,
  'media_revision starts at 1'
);

-- ============================================================================
-- Test 5: media_revision increments on storage_path change
-- ============================================================================

update public.uploaded_videos
set storage_path = 'task5/video_v2.mp4'
where id = '55000000-0000-4000-8000-000000000001';

select is(
  (select media_revision from public.uploaded_videos where id = '55000000-0000-4000-8000-000000000001'),
  2,
  'media_revision increments on storage_path change'
);

-- ============================================================================
-- Test 6: media_revision increments on duration_ms change
-- ============================================================================

update public.uploaded_videos
set duration_ms = 60000
where id = '55000000-0000-4000-8000-000000000001';

select is(
  (select media_revision from public.uploaded_videos where id = '55000000-0000-4000-8000-000000000001'),
  3,
  'media_revision increments on duration_ms change'
);

-- ============================================================================
-- Test 7: media_revision does NOT increment on unrelated change
-- ============================================================================

update public.uploaded_videos
set status = 'ready', width = 1920, height = 1080
where id = '55000000-0000-4000-8000-000000000001';

select is(
  (select media_revision from public.uploaded_videos where id = '55000000-0000-4000-8000-000000000001'),
  3,
  'media_revision does NOT increment on unrelated column update'
);

-- ============================================================================
-- Test 8: motionly_update_plan_with_revision succeeds with correct revision
-- ============================================================================

select lives_ok(
  $$select * from public.motionly_update_plan_with_revision(
    '85000000-0000-4000-8000-000000000001',
    2,
    '{"cuts": [{"start": 0, "end": 10000}]}'::jsonb
  )$$,
  'motionly_update_plan_with_revision succeeds with correct revision'
);

-- ============================================================================
-- Test 9: motionly_update_plan_with_revision fails with wrong revision
-- ============================================================================

select throws_ok(
  $$select * from public.motionly_update_plan_with_revision(
    '85000000-0000-4000-8000-000000000001',
    1,
    '{"cuts": [{"start": 0, "end": 15000}]}'::jsonb
  )$$,
  'P0002', null,
  'motionly_update_plan_with_revision fails with wrong revision'
);

-- ============================================================================
-- Test 10: motionly_update_plan_with_revision auto-increments revision
-- ============================================================================

select is(
  (select revision from public.edit_plans where id = '85000000-0000-4000-8000-000000000001'),
  3,
  'motionly_update_plan_with_revision auto-increments revision after CAS'
);

-- ============================================================================
-- Test 11: stale derivatives invalidated after plan update
-- ============================================================================

insert into public.render_jobs (
  id, workspace_id, project_id, uploaded_video_id, caption_track_id,
  planner_version, source_plan_revision
) values (
  '95000000-0000-4000-8000-000000000001',
  '35000000-0000-4000-8000-000000000001',
  '45000000-0000-4000-8000-000000000001',
  '55000000-0000-4000-8000-000000000001',
  '75000000-0000-4000-8000-000000000001',
  'task5', 3
);

-- Update the plan (will bump revision to 4) and trigger invalidation
select * from public.motionly_update_plan_with_revision(
  '85000000-0000-4000-8000-000000000001',
  3,
  '{"cuts": [{"start": 0, "end": 20000}]}'::jsonb
);

select is(
  (select stale from public.render_jobs where id = '95000000-0000-4000-8000-000000000001'),
  true,
  'stale derivatives invalidated after plan update (source_plan_revision < current)'
);

-- ============================================================================
-- Test 12: non-stale derivatives NOT invalidated
-- ============================================================================

insert into public.render_jobs (
  id, workspace_id, project_id, uploaded_video_id, caption_track_id,
  planner_version, source_plan_revision, stale
) values (
  '95000000-0000-4000-8000-000000000002',
  '35000000-0000-4000-8000-000000000001',
  '45000000-0000-4000-8000-000000000001',
  '55000000-0000-4000-8000-000000000001',
  '75000000-0000-4000-8000-000000000001',
  'task5', null, false
);

-- Update plan (revision 4 -> 5)
select * from public.motionly_update_plan_with_revision(
  '85000000-0000-4000-8000-000000000001',
  4,
  '{"cuts": [{"start": 0, "end": 25000}]}'::jsonb
);

select is(
  (select stale from public.render_jobs where id = '95000000-0000-4000-8000-000000000002'),
  false,
  'derivative without source_plan_revision stays not stale'
);

-- ============================================================================
-- Test 13: media replacement invalidates ALL derivatives
-- ============================================================================

-- Reset stale flags for test clarity
update public.render_jobs set stale = false
where project_id = '45000000-0000-4000-8000-000000000001';

-- Change storage_path (triggers media replace invalidation)
update public.uploaded_videos
set storage_path = 'task5/video_v3_replaced.mp4'
where id = '55000000-0000-4000-8000-000000000001';

select is(
  (select count(*)::integer from public.render_jobs
   where project_id = '45000000-0000-4000-8000-000000000001' and stale = true),
  2,
  'media replacement invalidates ALL project derivatives regardless of source_plan_revision'
);

-- ============================================================================
-- Test 14: timeline_revisions table exists and is insertable by service role
-- ============================================================================

select lives_ok(
  $$insert into public.timeline_revisions (
    workspace_id, project_id, edit_plan_id, revision, plan_snapshot,
    changed_by, change_type
  ) values (
    '35000000-0000-4000-8000-000000000001',
    '45000000-0000-4000-8000-000000000001',
    '85000000-0000-4000-8000-000000000001',
    1, '{"cuts":[]}'::jsonb,
    '15000000-0000-4000-8000-000000000001', 'cuts'
  )$$,
  'timeline_revisions table exists and is insertable by service role'
);

-- ============================================================================
-- Test 15: timeline_revisions RLS blocks authenticated INSERT
-- ============================================================================

set local role authenticated;
select set_config('request.jwt.claim.sub', '15000000-0000-4000-8000-000000000001', true);

select throws_ok(
  $$insert into public.timeline_revisions (
    workspace_id, project_id, edit_plan_id, revision, plan_snapshot,
    changed_by, change_type
  ) values (
    '35000000-0000-4000-8000-000000000001',
    '45000000-0000-4000-8000-000000000001',
    '85000000-0000-4000-8000-000000000001',
    2, '{"cuts":[1]}'::jsonb,
    '15000000-0000-4000-8000-000000000001', 'timeline'
  )$$,
  '42501', null,
  'timeline_revisions RLS blocks authenticated INSERT'
);

-- ============================================================================
-- Test 16: timeline_revisions RLS allows authenticated SELECT for member
-- ============================================================================

select is(
  (select count(*)::integer from public.timeline_revisions
   where workspace_id = '35000000-0000-4000-8000-000000000001'),
  1,
  'timeline_revisions RLS allows authenticated SELECT for workspace member'
);

reset role;

-- ============================================================================
-- Test 17: stale partial indexes exist
-- ============================================================================

select is(
  (select count(*)::integer from pg_indexes
   where schemaname = 'public'
     and indexname in (
       'render_jobs_stale_project_idx',
       'exports_stale_project_idx',
       'broll_slots_stale_plan_idx',
       'project_music_selections_stale_plan_idx'
     )),
  4,
  'stale partial indexes exist on render_jobs, exports, broll_slots, project_music_selections'
);

-- ============================================================================
-- Test 18: source lineage columns exist on render_jobs, broll_slots, etc.
-- ============================================================================

select ok(
  (select bool_and(result) from (
    select exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'render_jobs'
        and column_name = 'source_plan_revision'
    ) as result
    union all
    select exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'render_jobs'
        and column_name = 'source_media_revision'
    )
    union all
    select exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'broll_slots'
        and column_name = 'source_plan_revision'
    )
    union all
    select exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'broll_slots'
        and column_name = 'source_media_revision'
    )
    union all
    select exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'project_music_selections'
        and column_name = 'source_plan_revision'
    )
    union all
    select exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'exports'
        and column_name = 'source_plan_revision'
    )
  ) checks),
  'source lineage columns exist on render_jobs, broll_slots, project_music_selections, exports'
);

-- ============================================================================
-- Additional: stale column exists on all derivative tables
-- ============================================================================

select ok(
  (select bool_and(result) from (
    select exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'render_jobs'
        and column_name = 'stale'
    ) as result
    union all
    select exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'exports'
        and column_name = 'stale'
    )
    union all
    select exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'broll_slots'
        and column_name = 'stale'
    )
    union all
    select exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'project_music_selections'
        and column_name = 'stale'
    )
  ) checks),
  'stale boolean column exists on all derivative tables'
);

select * from finish();
rollback;
