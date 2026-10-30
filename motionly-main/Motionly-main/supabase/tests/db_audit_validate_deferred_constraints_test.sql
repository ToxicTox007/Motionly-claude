-- Database audit DB-17 (migration 202610280206): on a database whose rows
-- satisfy them, every deferred (NOT VALID) constraint is validated, and the
-- tenant-consistency keys keep rejecting cross-tenant rows.

begin;
select plan(3);

select is(
  (select count(*)::int from pg_constraint
   where connamespace = 'public'::regnamespace and contype in ('f', 'c') and not convalidated),
  0, 'no constraint in public is left NOT VALID on a clean database'
);
select ok(
  (select convalidated from pg_constraint where conname = 'task3_transcript_words_transcript_tenant_fk'),
  'the transcript words tenant key is validated'
);

insert into auth.users (id, email, raw_user_meta_data)
values ('0e000000-0000-4000-8000-000000000001', 'dbaudit-validate@test.local', '{}');
insert into public.workspaces (id, name, owner_user_id) values
  ('0e000000-0000-4000-8000-0000000000a1', 'Validate A', '0e000000-0000-4000-8000-000000000001'),
  ('0e000000-0000-4000-8000-0000000000a2', 'Validate B', '0e000000-0000-4000-8000-000000000001');
insert into public.projects (id, workspace_id, created_by, title) values
  ('0e000000-0000-4000-8000-0000000000b1', '0e000000-0000-4000-8000-0000000000a1', '0e000000-0000-4000-8000-000000000001', 'A');
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
  mime_type, byte_size, file_size_bytes)
values ('0e000000-0000-4000-8000-0000000000c1', '0e000000-0000-4000-8000-0000000000a1',
  '0e000000-0000-4000-8000-0000000000b1', 'raw-uploads', 'dbaudit/validate.mp4', 'v.mp4', 'video/mp4', 10, 10);
select throws_ok(
  $$insert into public.transcripts (workspace_id, project_id, uploaded_video_id, provider, model)
    values ('0e000000-0000-4000-8000-0000000000a2', '0e000000-0000-4000-8000-0000000000b1',
            '0e000000-0000-4000-8000-0000000000c1', 'fixture', 'fixture')$$,
  '23503', null,
  'a transcript cannot claim another workspace''s upload'
);

select * from finish();
rollback;
