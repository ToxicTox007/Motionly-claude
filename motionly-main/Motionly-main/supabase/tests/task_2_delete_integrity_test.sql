begin;
select plan(8);

-- A SET NULL action that nulls a NOT NULL column makes the referenced row
-- impossible to delete: Postgres raises a not-null violation instead.
select is(
  array(
    select c.conrelid::regclass::text || '.' || c.conname::text
      from pg_constraint c
      join pg_namespace n on n.oid = c.connamespace
     where n.nspname = 'public'
       and c.contype = 'f'
       and (
         (c.confdeltype = 'n' and exists (
            select 1
              from unnest(c.conkey) as k(attnum)
              join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
             where a.attnotnull
               and (c.confdelsetcols is null or k.attnum = any (c.confdelsetcols))))
         or (c.confupdtype = 'n' and exists (
            select 1
              from unnest(c.conkey) as k(attnum)
              join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
             where a.attnotnull))
       )
     order by 1
  ),
  array[]::text[],
  'no foreign key sets a NOT NULL column to null'
);

insert into auth.users (id, email, raw_user_meta_data) values
  ('26100000-0000-4000-8000-000000000001', 'delete-integrity@test.local', '{}'::jsonb);
insert into public.workspaces (id, name, owner_user_id) values
  ('26100000-0000-4000-8000-000000000002', 'Delete integrity', '26100000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status) values
  ('26100000-0000-4000-8000-000000000002', '26100000-0000-4000-8000-000000000001', 'owner', 'active');
set local role service_role;
insert into public.projects (id, workspace_id, created_by, title) values
  ('26100000-0000-4000-8000-000000000003', '26100000-0000-4000-8000-000000000002',
   '26100000-0000-4000-8000-000000000001', 'Project with an export');
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path,
  original_filename, mime_type, byte_size, file_size_bytes
) values (
  '26100000-0000-4000-8000-000000000004',
  '26100000-0000-4000-8000-000000000002', '26100000-0000-4000-8000-000000000003',
  'raw-uploads', 'delete-integrity/source.mp4', 'source.mp4', 'video/mp4', 1024, 1024
);
insert into public.render_jobs (
  id, workspace_id, project_id, uploaded_video_id, status, planner_version, specs, progress_pct
) values (
  '26100000-0000-4000-8000-000000000005',
  '26100000-0000-4000-8000-000000000002', '26100000-0000-4000-8000-000000000003',
  '26100000-0000-4000-8000-000000000004', 'rendering', 'delete-integrity',
  '{"width":1080,"height":1920,"fps":30,"format":"mp4"}'::jsonb, 90
);
select lives_ok(
  $$select * from public.motionly_complete_render(
    '26100000-0000-4000-8000-000000000002', '26100000-0000-4000-8000-000000000003',
    '26100000-0000-4000-8000-000000000005', 'exports',
    'workspaces/26100000-0000-4000-8000-000000000002/projects/26100000-0000-4000-8000-000000000003/exports/26100000-0000-4000-8000-000000000005/attempts/a-1/final.mp4',
    'mp4', 1080, 1920, 30, 1000, 2048, null,
    '{"render_engine":"remotion_premium","renderer_version":"remotion-timeline-v2"}'::jsonb, 100
  )$$,
  'the project has a completed export'
);
insert into public.asset_library_items (
  id, workspace_id, kind, name, source_project_id
) values (
  '26100000-0000-4000-8000-000000000006', '26100000-0000-4000-8000-000000000002',
  'export', 'Saved from the project', '26100000-0000-4000-8000-000000000003'
);
insert into public.transcripts (id, workspace_id, project_id, uploaded_video_id, provider, model) values (
  '26100000-0000-4000-8000-000000000007',
  '26100000-0000-4000-8000-000000000002', '26100000-0000-4000-8000-000000000003',
  '26100000-0000-4000-8000-000000000004', 'fixture', 'fixture'
);
insert into public.caption_tracks (id, workspace_id, project_id, transcript_id, preset) values (
  '26100000-0000-4000-8000-000000000008',
  '26100000-0000-4000-8000-000000000002', '26100000-0000-4000-8000-000000000003',
  '26100000-0000-4000-8000-000000000007', 'founder_clean'
);
insert into public.caption_tracks (
  id, workspace_id, project_id, transcript_id, preset, language, source_track_id
) values (
  '26100000-0000-4000-8000-000000000009',
  '26100000-0000-4000-8000-000000000002', '26100000-0000-4000-8000-000000000003',
  '26100000-0000-4000-8000-000000000007', 'founder_clean', 'es',
  '26100000-0000-4000-8000-000000000008'
);
insert into public.edit_plans (
  id, workspace_id, project_id, transcript_id, version, style,
  target_duration_s, provider, model, cache_key, schema_hash, plan, summary
) values (
  '26100000-0000-4000-8000-00000000000a',
  '26100000-0000-4000-8000-000000000002', '26100000-0000-4000-8000-000000000003',
  '26100000-0000-4000-8000-000000000007', 1, 'founder',
  30, 'fixture', 'fixture', 'delete-integrity', 'delete-integrity', '{}'::jsonb, '{}'::jsonb
);
insert into public.localized_publish_packages (
  id, workspace_id, project_id, source_caption_track_id, caption_track_id, edit_plan_id, language
) values (
  '26100000-0000-4000-8000-00000000000b',
  '26100000-0000-4000-8000-000000000002', '26100000-0000-4000-8000-000000000003',
  '26100000-0000-4000-8000-000000000008', '26100000-0000-4000-8000-000000000009',
  '26100000-0000-4000-8000-00000000000a', 'es'
);

select lives_ok(
  $$delete from public.edit_plans where id = '26100000-0000-4000-8000-00000000000a'$$,
  'an edit plan used by a localized package can be deleted'
);
select is(
  (select project_id::text || ':' || coalesce(edit_plan_id::text, 'null')
   from public.localized_publish_packages where id = '26100000-0000-4000-8000-00000000000b'),
  '26100000-0000-4000-8000-000000000003:null',
  'the localized package keeps its project and only loses the deleted plan'
);

select lives_ok(
  $$delete from public.projects where id = '26100000-0000-4000-8000-000000000003'$$,
  'a project with exports, localized packages and saved library items can be deleted'
);
select is(
  (select count(*)::integer from public.exports
   where project_id = '26100000-0000-4000-8000-000000000003'),
  0,
  'deleting a project deletes its exports'
);
select is(
  (select count(*)::integer from public.localized_publish_packages
   where project_id = '26100000-0000-4000-8000-000000000003'),
  0,
  'deleting a project deletes its localized packages'
);
select is(
  (select workspace_id::text || ':' || coalesce(source_project_id::text, 'null')
   from public.asset_library_items where id = '26100000-0000-4000-8000-000000000006'),
  '26100000-0000-4000-8000-000000000002:null',
  'library items saved from a deleted project stay in their workspace'
);

select * from finish();
rollback;
