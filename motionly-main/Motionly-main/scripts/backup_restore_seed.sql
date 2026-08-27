-- Representative data for the disposable CI backup/restore drill. This is not a
-- production seed and must only run against the freshly reset local source DB.
begin;

insert into auth.users (id, email, raw_user_meta_data)
values ('19000000-0000-4000-8000-000000000001', 'backup-drill@test.local', '{}'::jsonb);
insert into public.workspaces (id, name, owner_user_id)
values ('39000000-0000-4000-8000-000000000001', 'Backup restore drill', '19000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status)
values ('39000000-0000-4000-8000-000000000001', '19000000-0000-4000-8000-000000000001', 'owner', 'active');

set local role service_role;
insert into public.projects (id, workspace_id, created_by, title)
values ('49000000-0000-4000-8000-000000000001', '39000000-0000-4000-8000-000000000001', '19000000-0000-4000-8000-000000000001', 'Backup restore drill project');
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
  mime_type, byte_size, file_size_bytes, status, workflow_version
) values (
  '59000000-0000-4000-8000-000000000001',
  '39000000-0000-4000-8000-000000000001',
  '49000000-0000-4000-8000-000000000001',
  'raw-uploads', 'backup-drill/source.mp4', 'source.mp4',
  'video/mp4', 4096, 4096, 'processing', 'first-draft-v2'
);
insert into public.object_references (
  id, workspace_id, project_id, storage_bucket, storage_path,
  owner_type, owner_id, object_type, mime_type, size_bytes, sha256
) values (
  '89000000-0000-4000-8000-000000000001',
  '39000000-0000-4000-8000-000000000001',
  '49000000-0000-4000-8000-000000000001',
  'raw-uploads', 'backup-drill/source.mp4',
  'uploaded_video', '59000000-0000-4000-8000-000000000001', 'source_video',
  'video/mp4', 4096, repeat('a', 64)
);
update public.uploaded_videos
set source_object_reference_id = '89000000-0000-4000-8000-000000000001',
    verified_size_bytes = 4096,
    verified_sha256 = repeat('a', 64)
where id = '59000000-0000-4000-8000-000000000001';

select result from public.motionly_ensure_first_draft_v2(
  '39000000-0000-4000-8000-000000000001',
  '49000000-0000-4000-8000-000000000001',
  '59000000-0000-4000-8000-000000000001',
  '19000000-0000-4000-8000-000000000001'
) as result;

commit;
