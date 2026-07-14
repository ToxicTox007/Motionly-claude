begin;
select plan(6);

insert into auth.users (id, email, raw_user_meta_data)
values
  ('11000000-0000-4000-8000-000000000001', 'phase0-owner@rls.test', '{}'::jsonb),
  ('11000000-0000-4000-8000-000000000002', 'phase0-outsider@rls.test', '{}'::jsonb);

insert into public.workspaces (id, name, owner_user_id)
values ('31000000-0000-4000-8000-000000000001', 'Phase 0 owned workspace', '11000000-0000-4000-8000-000000000001');

insert into public.workspace_members (workspace_id, user_id, role, status)
values ('31000000-0000-4000-8000-000000000001', '11000000-0000-4000-8000-000000000001', 'owner', 'active');

insert into public.projects (id, workspace_id, created_by, title)
values (
  '41000000-0000-4000-8000-000000000001',
  '31000000-0000-4000-8000-000000000001',
  '11000000-0000-4000-8000-000000000001',
  'Phase 0 RLS project'
);

insert into public.uploaded_videos (
  id,
  workspace_id,
  project_id,
  storage_bucket,
  storage_path,
  original_filename,
  mime_type,
  byte_size,
  file_size_bytes
) values (
  '51000000-0000-4000-8000-000000000001',
  '31000000-0000-4000-8000-000000000001',
  '41000000-0000-4000-8000-000000000001',
  'raw-uploads',
  'workspaces/31000000-0000-4000-8000-000000000001/projects/41000000-0000-4000-8000-000000000001/raw/video.mp4',
  'video.mp4',
  'video/mp4',
  100,
  100
);

set local role authenticated;
select set_config('request.jwt.claim.sub', '11000000-0000-4000-8000-000000000001', true);
select is((select count(*)::integer from public.projects), 1, 'member can read own workspace project');
select is((select count(*)::integer from public.uploaded_videos), 1, 'member can read own workspace upload');
select throws_ok(
  $$update public.projects
    set title = 'Phase 0 RLS project updated'
    where id = '41000000-0000-4000-8000-000000000001'$$,
  '42501', null,
  'member must use the API rather than directly updating a project'
);

select set_config('request.jwt.claim.sub', '11000000-0000-4000-8000-000000000002', true);
select is((select count(*)::integer from public.projects), 0, 'outsider cannot read foreign workspace project');
select is((select count(*)::integer from public.uploaded_videos), 0, 'outsider cannot read foreign workspace upload');
select throws_ok(
  $$update public.projects
    set title = 'forged update'
    where id = '41000000-0000-4000-8000-000000000001'$$,
  '42501', null,
  'outsider cannot directly update a foreign workspace project'
);

select * from finish();
rollback;
