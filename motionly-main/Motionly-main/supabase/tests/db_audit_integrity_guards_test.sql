-- Database audit integrity guards (migration 202610280202):
-- DB-05 account deletion, DB-11 render terminal states, DB-13 export
-- artifact links, DB-23 profile email sync and paise width.

begin;
select plan(19);

insert into auth.users (id, email, raw_user_meta_data) values
  ('0b000000-0000-4000-8000-000000000001', 'dbaudit-owner@test.local', '{}'),
  ('0b000000-0000-4000-8000-000000000002', 'dbaudit-member@test.local', '{}'),
  ('0b000000-0000-4000-8000-000000000003', 'dbaudit-coowner@test.local', '{}'),
  ('0b000000-0000-4000-8000-000000000004', 'dbaudit-solo@test.local', '{}');

-- Three workspaces owned by the same account:
--   a1 shared with an active member only  -> blocks the account delete
--   a2 shared with another active owner   -> passes to that owner
--   a3 with an invited (not active) member -> deleted with the account
insert into public.workspaces (id, name, owner_user_id) values
  ('0b000000-0000-4000-8000-0000000000a1', 'Shared', '0b000000-0000-4000-8000-000000000001'),
  ('0b000000-0000-4000-8000-0000000000a2', 'Co-owned', '0b000000-0000-4000-8000-000000000001'),
  ('0b000000-0000-4000-8000-0000000000a3', 'Personal', '0b000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id, user_id, role, status) values
  ('0b000000-0000-4000-8000-0000000000a1', '0b000000-0000-4000-8000-000000000001', 'owner', 'active'),
  ('0b000000-0000-4000-8000-0000000000a1', '0b000000-0000-4000-8000-000000000002', 'member', 'active'),
  ('0b000000-0000-4000-8000-0000000000a2', '0b000000-0000-4000-8000-000000000001', 'owner', 'active'),
  ('0b000000-0000-4000-8000-0000000000a2', '0b000000-0000-4000-8000-000000000003', 'owner', 'active'),
  ('0b000000-0000-4000-8000-0000000000a3', '0b000000-0000-4000-8000-000000000001', 'owner', 'active'),
  ('0b000000-0000-4000-8000-0000000000a3', '0b000000-0000-4000-8000-000000000002', 'member', 'invited')
on conflict do nothing;
insert into public.projects (id, workspace_id, created_by, title) values
  ('0b000000-0000-4000-8000-0000000000b1', '0b000000-0000-4000-8000-0000000000a1',
   '0b000000-0000-4000-8000-000000000002', 'Member project'),
  ('0b000000-0000-4000-8000-0000000000b2', '0b000000-0000-4000-8000-0000000000a2',
   '0b000000-0000-4000-8000-000000000003', 'Co-owner project');

-- DB-05
select throws_ok(
  $$delete from auth.users where id = '0b000000-0000-4000-8000-000000000001'$$,
  'P0001', null,
  'an account that owns a workspace other active members use cannot be deleted'
);
select is(
  (select count(*)::int from public.projects where id = '0b000000-0000-4000-8000-0000000000b1'),
  1, 'the refused delete leaves the member''s project in place'
);
select lives_ok(
  $$select public.motionly_transfer_workspace_ownership(
      '0b000000-0000-4000-8000-0000000000a1',
      '0b000000-0000-4000-8000-000000000001',
      '0b000000-0000-4000-8000-000000000002')$$,
  'ownership can be transferred first'
);
select lives_ok(
  $$delete from auth.users where id = '0b000000-0000-4000-8000-000000000001'$$,
  'after the transfer the account can be deleted'
);
set constraints all immediate;
select is(
  (select owner_user_id from public.workspaces where id = '0b000000-0000-4000-8000-0000000000a2'),
  '0b000000-0000-4000-8000-000000000003'::uuid,
  'a workspace with another active owner passes to that owner'
);
select is(
  (select count(*)::int from public.projects
   where id in ('0b000000-0000-4000-8000-0000000000b1', '0b000000-0000-4000-8000-0000000000b2')),
  2, 'other members'' projects survive the owner''s account deletion'
);
select is(
  (select count(*)::int from public.workspaces where id = '0b000000-0000-4000-8000-0000000000a3'),
  0, 'a workspace nobody else is active in is deleted with the account, as before'
);

-- A personal workspace still goes with its only owner.
select lives_ok(
  $$delete from auth.users where id = '0b000000-0000-4000-8000-000000000004'$$,
  'a sole owner''s account deletes its personal workspace'
);
set constraints all immediate;
select is(
  (select count(*)::int from public.workspaces where owner_user_id = '0b000000-0000-4000-8000-000000000004'),
  0, 'the personal workspace created at sign-up is gone'
);

-- DB-11
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
  mime_type, byte_size, file_size_bytes)
values ('0b000000-0000-4000-8000-0000000000c1', '0b000000-0000-4000-8000-0000000000a1',
  '0b000000-0000-4000-8000-0000000000b1', 'raw-uploads', 'dbaudit/source.mp4', 'source.mp4',
  'video/mp4', 10, 10);
insert into public.render_jobs (id, workspace_id, project_id, uploaded_video_id, status, specs) values
  ('0b000000-0000-4000-8000-0000000000d1', '0b000000-0000-4000-8000-0000000000a1',
   '0b000000-0000-4000-8000-0000000000b1', '0b000000-0000-4000-8000-0000000000c1', 'failed',
   '{"width":1080,"height":1920,"fps":30,"format":"mp4"}'),
  ('0b000000-0000-4000-8000-0000000000d2', '0b000000-0000-4000-8000-0000000000a1',
   '0b000000-0000-4000-8000-0000000000b1', '0b000000-0000-4000-8000-0000000000c1', 'queued',
   '{"width":1080,"height":1920,"fps":30,"format":"mp4"}');
set local role service_role;
select lives_ok(
  $$update public.render_jobs set status = 'queued' where id = '0b000000-0000-4000-8000-0000000000d1'$$,
  'a failed render can be requeued (render retry)'
);
select throws_ok(
  $$update public.render_jobs set status = 'failed' where id = '0b000000-0000-4000-8000-0000000000d1';
    update public.render_jobs set status = 'rendering' where id = '0b000000-0000-4000-8000-0000000000d1'$$,
  'P0001', 'RENDER_TERMINAL_STATE',
  'a failed render cannot move anywhere but queued'
);
update public.render_jobs set status = 'complete', progress_pct = 100
where id = '0b000000-0000-4000-8000-0000000000d2';
select throws_ok(
  $$update public.render_jobs set status = 'uploading' where id = '0b000000-0000-4000-8000-0000000000d2'$$,
  'P0001', 'RENDER_TERMINAL_STATE',
  'a complete render never changes status'
);
reset role;

-- DB-13
select is(
  (select confdeltype::text from pg_constraint
   where conname = 'task5_workflow_artifact_export_fk'
     and conrelid = 'public.workflow_run_artifacts'::regclass),
  'c', 'an export''s workflow artifact link is deleted with the export'
);
select ok(
  position('draft_export' in pg_get_functiondef('public.motionly_protect_workflow_run_artifact()'::regprocedure)) > 0
  and exists (
    select 1 from pg_trigger
    where tgname = 'task5_workflow_artifacts_append_only'
      and tgrelid = 'public.workflow_run_artifacts'::regclass
      and tgfoid = 'public.motionly_protect_workflow_run_artifact()'::regprocedure),
  'workflow artifacts stay append-only except when their project or export is gone'
);
insert into public.object_references (
  id, workspace_id, project_id, storage_bucket, storage_path, owner_type, owner_id,
  object_type, mime_type, size_bytes, sha256)
values ('0b000000-0000-4000-8000-0000000000e1', '0b000000-0000-4000-8000-0000000000a1',
  '0b000000-0000-4000-8000-0000000000b1', 'raw-uploads', 'dbaudit/source.mp4',
  'uploaded_video', '0b000000-0000-4000-8000-0000000000c1', 'source_video', 'video/mp4', 10, repeat('a', 64));
update public.uploaded_videos
set source_object_reference_id = '0b000000-0000-4000-8000-0000000000e1',
    verified_size_bytes = 10, verified_sha256 = repeat('a', 64), status = 'ready',
    workflow_version = 'first-draft-v2', duration_ms = 1000, width = 1080, height = 1920
where id = '0b000000-0000-4000-8000-0000000000c1';
insert into public.workflow_runs (
  id, workspace_id, project_id, uploaded_video_id, source_media_revision,
  source_object_reference_id, source_size_bytes, source_sha256, workflow_type,
  workflow_version, idempotency_key, request_hash, temporal_workflow_id, state, started_by)
select '0b000000-0000-4000-8000-0000000000f1', v.workspace_id, v.project_id, v.id, v.media_revision,
  v.source_object_reference_id, v.verified_size_bytes, v.verified_sha256, 'first_draft',
  'first-draft-v2', 'dbaudit-run', repeat('b', 64), 'motionly-dbaudit-run', 'proposal_ready',
  '0b000000-0000-4000-8000-000000000002'
from public.uploaded_videos v where v.id = '0b000000-0000-4000-8000-0000000000c1';
insert into public.exports (id, workspace_id, project_id, render_job_id, storage_bucket, storage_path, format)
values ('0b000000-0000-4000-8000-0000000000e2', '0b000000-0000-4000-8000-0000000000a1',
  '0b000000-0000-4000-8000-0000000000b1', '0b000000-0000-4000-8000-0000000000d2',
  'exports', 'dbaudit/export.mp4', 'mp4');
insert into public.workflow_run_artifacts (workspace_id, project_id, workflow_run_id, artifact_type, export_id)
values ('0b000000-0000-4000-8000-0000000000a1', '0b000000-0000-4000-8000-0000000000b1',
  '0b000000-0000-4000-8000-0000000000f1', 'draft_export', '0b000000-0000-4000-8000-0000000000e2');
select throws_ok(
  $$delete from public.workflow_run_artifacts where export_id = '0b000000-0000-4000-8000-0000000000e2'$$,
  '55000', null,
  'the artifact link cannot be deleted while its export exists'
);
select lives_ok(
  $$delete from public.exports where id = '0b000000-0000-4000-8000-0000000000e2'$$,
  'retention can delete a workflow-produced export'
);
select ok(
  not exists (select 1 from public.workflow_run_artifacts where export_id = '0b000000-0000-4000-8000-0000000000e2')
  and exists (select 1 from public.storage_deletion_queue
              where storage_bucket = 'exports' and storage_path = 'dbaudit/export.mp4'),
  'its artifact link goes with it and its file is queued for deletion'
);

-- DB-23
update auth.users set email = 'dbaudit-member-new@test.local'
where id = '0b000000-0000-4000-8000-000000000002';
select is(
  (select email from public.profiles where id = '0b000000-0000-4000-8000-000000000002'),
  'dbaudit-member-new@test.local', 'an account email change reaches the profile'
);
select is(
  (select data_type from information_schema.columns
   where table_schema = 'public' and table_name = 'billing_adjustments' and column_name = 'amount_paise'),
  'bigint', 'billing adjustment amounts use the same width as every other paise amount'
);

select * from finish();
rollback;
