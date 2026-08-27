begin;
select plan(12);

select has_function(
  'public', 'motionly_complete_render',
  array['uuid','uuid','uuid','text','text','text','integer','integer','integer','integer','bigint','text','jsonb','integer'],
  'transactional render completion RPC exists'
);
select ok(
  has_function_privilege('service_role',
    'public.motionly_complete_render(uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb,integer)',
    'EXECUTE')
  and not has_function_privilege('authenticated',
    'public.motionly_complete_render(uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb,integer)',
    'EXECUTE'),
  'render completion is service-role-only'
);

insert into auth.users (id, email, raw_user_meta_data) values
  ('25000000-0000-4000-8000-000000000001', 'task25-owner@test.local', '{}'::jsonb);
insert into public.workspaces (id, name, owner_user_id) values
  ('25000000-0000-4000-8000-000000000002', 'Task 25', '25000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status) values
  ('25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000001', 'owner', 'active');
set local role service_role;
insert into public.projects (id, workspace_id, created_by, title) values
  ('25000000-0000-4000-8000-000000000003', '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000001', 'Task 25 render');
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path,
  original_filename, mime_type, byte_size, file_size_bytes
) values (
  '25000000-0000-4000-8000-000000000004',
  '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003',
  'raw-uploads', 'task25/source.mp4', 'source.mp4', 'video/mp4', 1024, 1024
);
insert into public.transcripts (id, workspace_id, project_id, uploaded_video_id, provider, model) values (
  '25000000-0000-4000-8000-000000000005',
  '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003',
  '25000000-0000-4000-8000-000000000004', 'fixture', 'fixture'
);
insert into public.caption_tracks (id, workspace_id, project_id, transcript_id, preset) values (
  '25000000-0000-4000-8000-000000000006',
  '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003',
  '25000000-0000-4000-8000-000000000005', 'founder_clean'
);
insert into public.render_jobs (
  id, workspace_id, project_id, uploaded_video_id, caption_track_id, status,
  planner_version, specs, progress_pct
) values (
  '25000000-0000-4000-8000-000000000007',
  '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003',
  '25000000-0000-4000-8000-000000000004', '25000000-0000-4000-8000-000000000006',
  'rendering', 'task25', '{"width":1080,"height":1920,"fps":30,"format":"mp4"}'::jsonb, 88
);

create temporary table task25_first as
select * from public.motionly_complete_render(
  '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003',
  '25000000-0000-4000-8000-000000000007', 'exports',
  'workspaces/25000000-0000-4000-8000-000000000002/projects/25000000-0000-4000-8000-000000000003/exports/25000000-0000-4000-8000-000000000007/attempts/bull-a-1/final.mp4',
  'mp4', 1080, 1920, 30, 1000, 2048, null,
  '{"render_engine":"remotion_premium","renderer_version":"remotion-timeline-v2"}'::jsonb, 321
);
select ok(
  (select status = 'complete' and artifact_path like '%/attempts/bull-a-1/final.mp4' and benchmark_ms = 321
   from public.render_jobs where id = '25000000-0000-4000-8000-000000000007')
  and (select count(*) = 1 from public.exports where render_job_id = '25000000-0000-4000-8000-000000000007')
  and (select count(*) = 1 from public.render_job_events where render_job_id = '25000000-0000-4000-8000-000000000007' and event_type = 'complete'),
  'CAS publishes exactly one canonical export, complete state, and event'
);
select is(
  (select export_id from public.motionly_complete_render(
    '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003',
    '25000000-0000-4000-8000-000000000007', 'exports',
    'workspaces/25000000-0000-4000-8000-000000000002/projects/25000000-0000-4000-8000-000000000003/exports/25000000-0000-4000-8000-000000000007/attempts/bull-a-1/final.mp4',
    'mp4', 1080, 1920, 30, 1000, 2048, null,
    '{"render_engine":"remotion_premium","renderer_version":"remotion-timeline-v2"}'::jsonb, 321
  )),
  (select export_id from task25_first),
  'exact replay returns the same canonical export'
);
select is(
  (select count(*)::integer from public.render_job_events
   where render_job_id = '25000000-0000-4000-8000-000000000007' and event_type = 'complete'),
  1,
  'exact replay emits no duplicate completion event'
);
select throws_ok(
  $$select * from public.motionly_complete_render(
    '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003',
    '25000000-0000-4000-8000-000000000007', 'exports',
    'workspaces/25000000-0000-4000-8000-000000000002/projects/25000000-0000-4000-8000-000000000003/exports/25000000-0000-4000-8000-000000000007/attempts/bull-b-1/final.mp4',
    'mp4', 1080, 1920, 30, 1000, 2048, null,
    '{"render_engine":"remotion_premium","renderer_version":"remotion-timeline-v2"}'::jsonb, 321
  )$$,
  'P0001', 'RENDER_COMPLETION_IDEMPOTENCY_CONFLICT',
  'different attempt artifact cannot replace a completed canonical export'
);

insert into public.render_jobs (id, workspace_id, project_id, uploaded_video_id, caption_track_id, status, planner_version, specs) values
  ('25000000-0000-4000-8000-000000000008', '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003', '25000000-0000-4000-8000-000000000004', '25000000-0000-4000-8000-000000000006', 'canceled', 'task25', '{"width":1080,"height":1920,"fps":30}'::jsonb),
  ('25000000-0000-4000-8000-000000000009', '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003', '25000000-0000-4000-8000-000000000004', '25000000-0000-4000-8000-000000000006', 'failed', 'task25', '{"width":1080,"height":1920,"fps":30}'::jsonb),
  ('25000000-0000-4000-8000-000000000010', '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003', '25000000-0000-4000-8000-000000000004', '25000000-0000-4000-8000-000000000006', 'rendering', 'task25', '{"width":1080,"height":1920,"fps":30}'::jsonb);
select throws_ok(
  $$select * from public.motionly_complete_render(
    '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003', '25000000-0000-4000-8000-000000000008', 'exports',
    'workspaces/25000000-0000-4000-8000-000000000002/projects/25000000-0000-4000-8000-000000000003/exports/25000000-0000-4000-8000-000000000008/attempts/bull-c-1/final.mp4',
    'mp4', 1080, 1920, 30, 1000, 1, null, '{}'::jsonb, 1
  )$$,
  'P0001', 'RENDER_TERMINAL_STATE', 'canceled render rejects late completion'
);
select throws_ok(
  $$select * from public.motionly_complete_render(
    '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003', '25000000-0000-4000-8000-000000000009', 'exports',
    'workspaces/25000000-0000-4000-8000-000000000002/projects/25000000-0000-4000-8000-000000000003/exports/25000000-0000-4000-8000-000000000009/attempts/bull-d-1/final.mp4',
    'mp4', 1080, 1920, 30, 1000, 1, null, '{}'::jsonb, 1
  )$$,
  'P0001', 'RENDER_TERMINAL_STATE', 'failed render rejects incompatible completion'
);
select throws_ok(
  $$select * from public.motionly_complete_render(
    '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003', '25000000-0000-4000-8000-000000000010', 'exports',
    'workspaces/25000000-0000-4000-8000-000000000002/projects/25000000-0000-4000-8000-000000000003/exports/25000000-0000-4000-8000-000000000010/attempts/bull-e-1/final.mp4',
    'mp4', 720, 1920, 30, 1000, 1, null, '{}'::jsonb, 1
  )$$,
  'P0001', 'RENDER_OUTPUT_SPECS_MISMATCH', 'requested output specs are authoritative'
);
select throws_ok(
  $$update public.render_jobs set status = 'complete' where id = '25000000-0000-4000-8000-000000000008'$$,
  'P0001', 'RENDER_TERMINAL_STATE', 'direct update cannot turn canceled into complete'
);
select ok(
  position('RENDER_WORKFLOW_LINEAGE_INVALID' in pg_get_functiondef(
    'public.motionly_complete_render(uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb,integer)'::regprocedure
  )) > 0
  and position('RENDER_WORKFLOW_PLAN_LINEAGE_INVALID' in pg_get_functiondef(
    'public.motionly_complete_render(uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb,integer)'::regprocedure
  )) > 0,
  'completion validates workflow and plan lineage before publication'
);
select throws_ok(
  $$select * from public.motionly_upsert_base_export(
    '25000000-0000-4000-8000-000000000002', '25000000-0000-4000-8000-000000000003', '25000000-0000-4000-8000-000000000008',
    'exports', 'task25/legacy.mp4', 'mp4', 1080, 1920, 30, 1000, 1, null, '{}'::jsonb
  )$$,
  'P0001', 'RENDER_COMPLETION_RPC_REQUIRED', 'legacy export RPC cannot bypass a terminal render'
);

select * from finish();
rollback;
