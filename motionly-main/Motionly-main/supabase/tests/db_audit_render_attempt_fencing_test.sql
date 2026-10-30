-- Database audit DB-11 (migration 202610280209): a render belongs to one
-- worker attempt at a time; a superseded attempt cannot write or publish.

begin;
select plan(16);

insert into auth.users (id, email, raw_user_meta_data) values
  ('29000000-0000-4000-8000-000000000001', 'dbaudit-render@test.local', '{}'::jsonb);
insert into public.workspaces (id, name, owner_user_id) values
  ('29000000-0000-4000-8000-000000000002', 'Render fencing', '29000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status) values
  ('29000000-0000-4000-8000-000000000002', '29000000-0000-4000-8000-000000000001', 'owner', 'active');
insert into public.projects (id, workspace_id, created_by, title) values
  ('29000000-0000-4000-8000-000000000003', '29000000-0000-4000-8000-000000000002',
   '29000000-0000-4000-8000-000000000001', 'Render fencing');
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path,
  original_filename, mime_type, byte_size, file_size_bytes
) values (
  '29000000-0000-4000-8000-000000000004',
  '29000000-0000-4000-8000-000000000002', '29000000-0000-4000-8000-000000000003',
  'raw-uploads', 'fencing/source.mp4', 'source.mp4', 'video/mp4', 1024, 1024
);
insert into public.render_jobs (
  id, workspace_id, project_id, uploaded_video_id, status, planner_version, specs, progress_pct
) values
  ('29000000-0000-4000-8000-000000000010', '29000000-0000-4000-8000-000000000002',
   '29000000-0000-4000-8000-000000000003', '29000000-0000-4000-8000-000000000004',
   'queued', 'fencing', '{"width":1080,"height":1920,"fps":30,"format":"mp4"}'::jsonb, 5),
  ('29000000-0000-4000-8000-000000000011', '29000000-0000-4000-8000-000000000002',
   '29000000-0000-4000-8000-000000000003', '29000000-0000-4000-8000-000000000004',
   'canceled', 'fencing', '{"width":1080,"height":1920,"fps":30,"format":"mp4"}'::jsonb, 0);

create function pg_temp.claim(p_render uuid, p_attempt uuid) returns jsonb
language sql as $$
  select public.motionly_claim_render_attempt(
    '29000000-0000-4000-8000-000000000002', '29000000-0000-4000-8000-000000000003',
    p_render, p_attempt, 10, 'Running preflight.', 'preflight_started')
$$;
create function pg_temp.complete(p_attempt uuid, p_suffix text) returns uuid
language sql as $$
  select export_id from public.motionly_complete_render(
    '29000000-0000-4000-8000-000000000002', '29000000-0000-4000-8000-000000000003',
    '29000000-0000-4000-8000-000000000010', 'exports',
    'workspaces/29000000-0000-4000-8000-000000000002/projects/29000000-0000-4000-8000-000000000003/exports/29000000-0000-4000-8000-000000000010/attempts/' || p_suffix || '/final.mp4',
    'mp4', 1080, 1920, 30, 1000, 2048, null, '{}'::jsonb, 100, p_attempt)
$$;
grant execute on all functions in schema pg_temp to service_role;

select has_column('public', 'render_jobs', 'attempt_id', 'renders record their owning attempt');
select ok(
  has_function_privilege('motionly_worker',
    'public.motionly_claim_render_attempt(uuid,uuid,uuid,uuid,integer,text,text)', 'EXECUTE')
  and has_function_privilege('motionly_worker',
    'public.motionly_complete_render(uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb,integer,uuid)', 'EXECUTE')
  and not has_function_privilege('authenticated',
    'public.motionly_claim_render_attempt(uuid,uuid,uuid,uuid,integer,text,text)', 'EXECUTE')
  and not has_function_privilege('anon',
    'public.motionly_complete_render(uuid,uuid,uuid,text,text,text,integer,integer,integer,integer,bigint,text,jsonb,integer,uuid)', 'EXECUTE'),
  'render workers may claim and complete; browser roles may not'
);

-- The workers run these through PostgREST; the privilege check above covers
-- motionly_worker, and service_role exercises the same functions here.
set local role service_role;

select is(
  (pg_temp.claim('29000000-0000-4000-8000-000000000010', 'a1000000-0000-4000-8000-000000000001'))->>'attempt_number',
  '1', 'the first attempt claims the queued render'
);
select is(
  (select status::text || '/' || attempt_id::text || '/' || progress_pct
   from public.render_jobs where id = '29000000-0000-4000-8000-000000000010'),
  'preparing/a1000000-0000-4000-8000-000000000001/10', 'the claim moves the render to preparing'
);
select is(
  (pg_temp.claim('29000000-0000-4000-8000-000000000010', 'a1000000-0000-4000-8000-000000000001'))->>'claimed',
  'false', 'the same attempt claiming again changes nothing'
);
-- A stalled redelivery takes over the render from the hung attempt.
select is(
  (pg_temp.claim('29000000-0000-4000-8000-000000000010', 'a2000000-0000-4000-8000-000000000002'))->>'attempt_number',
  '2', 'a newer attempt takes the render over'
);

-- The old attempt's conditional writes no longer match.
update public.render_jobs set status = 'rendering', progress_pct = 70
where id = '29000000-0000-4000-8000-000000000010'
  and attempt_id = 'a1000000-0000-4000-8000-000000000001';
select is(
  (select status::text from public.render_jobs where id = '29000000-0000-4000-8000-000000000010'),
  'preparing', 'a superseded attempt''s progress write matches no row'
);
select throws_ok(
  $$select pg_temp.complete('a1000000-0000-4000-8000-000000000001', 'stale')$$,
  'P0001', 'RENDER_ATTEMPT_SUPERSEDED', 'a superseded attempt cannot publish its output'
);
select throws_ok(
  $$select pg_temp.complete(null, 'legacy')$$,
  'P0001', 'RENDER_ATTEMPT_SUPERSEDED', 'a caller without an attempt cannot publish a claimed render'
);

-- Requeueing releases the attempt; the next run claims afresh.
update public.render_jobs set status = 'queued', progress_pct = 5
where id = '29000000-0000-4000-8000-000000000010'
  and attempt_id = 'a2000000-0000-4000-8000-000000000002';
select is(
  (select status::text || '/' || coalesce(attempt_id::text, 'none')
   from public.render_jobs where id = '29000000-0000-4000-8000-000000000010'),
  'queued/none', 'requeueing releases the attempt'
);
update public.render_jobs set status = 'rendering', progress_pct = 60
where id = '29000000-0000-4000-8000-000000000010'
  and attempt_id = 'a2000000-0000-4000-8000-000000000002';
select is(
  (select status::text from public.render_jobs where id = '29000000-0000-4000-8000-000000000010'),
  'queued', 'the requeued attempt cannot write over the queued render'
);
select is(
  (pg_temp.claim('29000000-0000-4000-8000-000000000010', 'a3000000-0000-4000-8000-000000000003'))->>'attempt_number',
  '3', 'the retry claims the render'
);
select isnt(
  pg_temp.complete('a3000000-0000-4000-8000-000000000003', 'current'),
  null, 'the owning attempt publishes its output'
);
select throws_ok(
  $$select pg_temp.claim('29000000-0000-4000-8000-000000000010', 'a4000000-0000-4000-8000-000000000004')$$,
  'P0001', 'RENDER_ALREADY_COMPLETE', 'a completed render cannot be claimed'
);
select throws_ok(
  $$select pg_temp.claim('29000000-0000-4000-8000-000000000011', 'a5000000-0000-4000-8000-000000000005')$$,
  'P0001', 'RENDER_CANCELED', 'a canceled render cannot be claimed'
);

reset role;
select is(
  (select count(*)::int from public.render_job_events
   where render_job_id = '29000000-0000-4000-8000-000000000010'
     and event_type = 'preflight_started'),
  3, 'each claim is recorded once'
);

select * from finish();
rollback;
