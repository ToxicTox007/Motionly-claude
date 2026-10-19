begin;
select plan(14);

-- Task 6: deleting a project (or its workspace) removes its workflow and
-- proposal history. The history stays append-only otherwise: rows cannot be
-- changed, and cannot be deleted while their project exists. Fixture: a
-- project whose first-draft run produced events, outbox facts, step attempts,
-- artifacts, a proposal with actions, an approval decision, an edit plan and a
-- timeline revision (same flow as task_13_proposal_approval_gate_test.sql),
-- plus an untouched second project in the same workspace.

insert into auth.users (id,email,raw_user_meta_data) values
 ('1e000000-0000-4000-8000-000000000001','task26-owner@test.local','{}');
insert into public.workspaces (id,name,owner_user_id) values
 ('3e000000-0000-4000-8000-000000000001','Task 26 deletion','1e000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id,user_id,role,status) values
 ('3e000000-0000-4000-8000-000000000001','1e000000-0000-4000-8000-000000000001','owner','active');
set local role service_role;
insert into public.projects (id,workspace_id,created_by,title) values
 ('4e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001','1e000000-0000-4000-8000-000000000001','Approval'),
 ('4e000000-0000-4000-8000-000000000002','3e000000-0000-4000-8000-000000000001','1e000000-0000-4000-8000-000000000001','Rejection'),
 ('4e000000-0000-4000-8000-000000000003','3e000000-0000-4000-8000-000000000001','1e000000-0000-4000-8000-000000000001','Recovery');
insert into public.uploaded_videos (
 id,workspace_id,project_id,storage_bucket,storage_path,original_filename,
 mime_type,byte_size,file_size_bytes,status,workflow_version,
 duration_ms,width,height,has_audio
) values
 ('5e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
  'raw-uploads','task26/approve.mp4','approve.mp4','video/mp4',2048,2048,'processing','first-draft-v2',10000,1920,1080,true),
 ('5e000000-0000-4000-8000-000000000002','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000002',
  'raw-uploads','task26/reject.mp4','reject.mp4','video/mp4',2048,2048,'processing','first-draft-v2',10000,1920,1080,true),
 ('5e000000-0000-4000-8000-000000000003','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000003',
  'raw-uploads','task26/recover.mp4','recover.mp4','video/mp4',2048,2048,'processing','first-draft-v2',10000,1920,1080,true);
insert into public.object_references (
 id,workspace_id,project_id,storage_bucket,storage_path,owner_type,owner_id,
 object_type,mime_type,size_bytes,sha256
) values
 ('8e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
  'raw-uploads','task26/approve.mp4','uploaded_video','5e000000-0000-4000-8000-000000000001','source_video','video/mp4',2048,repeat('a',64)),
 ('8e000000-0000-4000-8000-000000000002','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000002',
  'raw-uploads','task26/reject.mp4','uploaded_video','5e000000-0000-4000-8000-000000000002','source_video','video/mp4',2048,repeat('b',64)),
 ('8e000000-0000-4000-8000-000000000003','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000003',
  'raw-uploads','task26/recover.mp4','uploaded_video','5e000000-0000-4000-8000-000000000003','source_video','video/mp4',2048,repeat('c',64));
update public.uploaded_videos video set
 source_object_reference_id = reference.id,
 verified_size_bytes = reference.size_bytes,
 verified_sha256 = reference.sha256,
 status = 'ready'
from public.object_references reference
where reference.owner_type = 'uploaded_video'
  and reference.owner_id = video.id
  and video.id in (
    '5e000000-0000-4000-8000-000000000001',
    '5e000000-0000-4000-8000-000000000002',
    '5e000000-0000-4000-8000-000000000003'
  );
update public.projects project set active_video_id = video.id
from public.uploaded_videos video
where video.project_id = project.id
  and project.id in (
    '4e000000-0000-4000-8000-000000000001',
    '4e000000-0000-4000-8000-000000000002',
    '4e000000-0000-4000-8000-000000000003'
  );
insert into public.transcripts (
 id,workspace_id,project_id,uploaded_video_id,status,language,provider,model,
 confidence,duration_ms,quality_status
) values
 ('6e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001','5e000000-0000-4000-8000-000000000001','ready','en','fixture','fixture',0.99,10000,'ok'),
 ('6e000000-0000-4000-8000-000000000002','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000002','5e000000-0000-4000-8000-000000000002','ready','en','fixture','fixture',0.99,10000,'ok');
insert into public.transcript_words (
 id,workspace_id,project_id,transcript_id,word_index,text,start_ms,end_ms,confidence
) values
 ('7e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001','6e000000-0000-4000-8000-000000000001',0,'hello',0,500,0.99),
 ('7e000000-0000-4000-8000-000000000002','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000002','6e000000-0000-4000-8000-000000000002',0,'hello',0,500,0.99);
insert into public.video_manifests (
 id,workspace_id,project_id,uploaded_video_id,version,status,provider,model,manifest
) values
 ('9e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001','5e000000-0000-4000-8000-000000000001',1,'ready','fixture','fixture','{"summary":{"scene_count":1}}'),
 ('9e000000-0000-4000-8000-000000000002','3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000002','5e000000-0000-4000-8000-000000000002',1,'ready','fixture','fixture','{"summary":{"scene_count":1}}');
insert into public.workflow_runs (
 id,workspace_id,project_id,uploaded_video_id,source_media_revision,
 source_object_reference_id,source_size_bytes,source_sha256,workflow_type,
 workflow_version,idempotency_key,request_hash,temporal_workflow_id,state,started_by
)
select
 case video.id
  when '5e000000-0000-4000-8000-000000000001' then 'ae000000-0000-4000-8000-000000000001'::uuid
  else 'ae000000-0000-4000-8000-000000000002'::uuid end,
 video.workspace_id,video.project_id,video.id,video.media_revision,
 video.source_object_reference_id,video.verified_size_bytes,video.verified_sha256,
 'first_draft','first-draft-v2','task26-run-' || right(video.id::text,1),repeat('d',64),
 'motionly-task26-' || right(video.id::text,1),'proposal_ready','1e000000-0000-4000-8000-000000000001'
from public.uploaded_videos video
where video.id in (
 '5e000000-0000-4000-8000-000000000001','5e000000-0000-4000-8000-000000000002'
);
create temporary table task26_approve_proposal as
select result from public.motionly_create_ai_edit_proposal(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 repeat('1',64),repeat('e',64),
 '{"controls":{"broll_density":"medium","prompt":""},"modality_degradation":[]}',
 '5e000000-0000-4000-8000-000000000001',
 (select media_revision from public.uploaded_videos where id='5e000000-0000-4000-8000-000000000001'),
 '6e000000-0000-4000-8000-000000000001','9e000000-0000-4000-8000-000000000001',1,
 null,null,null,'ai-edit-plan-v1','1e000000-0000-4000-8000-000000000001'
) result;
select * from public.motionly_link_workflow_artifact(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001','ae000000-0000-4000-8000-000000000001',
 'transcript','6e000000-0000-4000-8000-000000000001',null);
select * from public.motionly_link_workflow_artifact(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001','ae000000-0000-4000-8000-000000000001',
 'visual_manifest','9e000000-0000-4000-8000-000000000001',1);
select * from public.motionly_link_workflow_artifact(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001','ae000000-0000-4000-8000-000000000001',
 'proposal',(select (result->>'id')::uuid from task26_approve_proposal),1);
create temporary table task26_second_proposal as
select result from public.motionly_create_ai_edit_proposal(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 repeat('2',64),repeat('f',64),'{"controls":{"broll_density":"medium"},"modality_degradation":[]}',
 '5e000000-0000-4000-8000-000000000001',
 (select media_revision from public.uploaded_videos where id='5e000000-0000-4000-8000-000000000001'),
 '6e000000-0000-4000-8000-000000000001','9e000000-0000-4000-8000-000000000001',1,
 null,null,null,'ai-edit-plan-v1','1e000000-0000-4000-8000-000000000001'
) result;
select * from public.motionly_prepare_workflow_step(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001','ae000000-0000-4000-8000-000000000001',
 'generate-proposal-v2','generate_proposal',repeat('e',64),3,'workflow--task26--approve',(select (result->>'id')::uuid from task26_approve_proposal));
create temporary table task26_approve_begin as
select result from public.motionly_begin_workflow_step(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001','ae000000-0000-4000-8000-000000000001',
 'generate-proposal-v2','generate_proposal','task26-approve-attempt','worker-a',60,repeat('e',64),3
) result;
select * from public.motionly_transition_ai_edit_proposal(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',(select (result->>'id')::uuid from task26_approve_proposal),'generating','{}');
select * from public.motionly_transition_ai_edit_proposal(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',(select (result->>'id')::uuid from task26_approve_proposal),'awaiting_approval',
 jsonb_build_object(
  'generated_plan','{"schema_version":2,"style":"founder","target_duration_s":10,"cuts":[],"caption_highlights":[],"broll_requests":[],"music_mood":"none","effects":[],"color_grade":"natural","zoom_moments":[],"risk_flags":[]}'::jsonb,
  'generated_summary','{}'::jsonb,
  'actions',jsonb_build_array(jsonb_build_object(
    'id','action_01_aaaaaaaaaaaa','ordinal',0,'tool_name','complete_first_plan','tool_version','1',
    'args',jsonb_build_object('plan','{"schema_version":2,"style":"founder","target_duration_s":10,"cuts":[],"caption_highlights":[],"broll_requests":[],"music_mood":"none","effects":[],"color_grade":"natural","zoom_moments":[],"risk_flags":[]}'::jsonb),
    'rationale','Create the first canonical plan','confidence',0.9,'dependency_ids','[]'::jsonb,
    'risk_flags','[]'::jsonb,'dry_run_hash',repeat('a',64)
  ))
 ));
create temporary table task26_approve_complete as
select result from public.motionly_complete_workflow_step(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001','ae000000-0000-4000-8000-000000000001',
 'generate-proposal-v2',(select (result->'attempt'->>'claim_token')::uuid from task26_approve_begin),repeat('9',64),
 jsonb_build_object(
  'proposal_id',(select result->>'id' from task26_approve_proposal),'status','awaiting_approval',
  'result_hash',(select result_hash from public.ai_edit_proposals where id=(select (result->>'id')::uuid from task26_approve_proposal)),
  'failed',false
 ),'awaiting_approval','workflow.step.completed') result;
create temporary table task26_approve_decision as
select * from public.motionly_decide_ai_edit_proposal(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',(select (result->>'id')::uuid from task26_approve_proposal),
 'approve',(select revision from public.ai_edit_proposals where id=(select (result->>'id')::uuid from task26_approve_proposal)),
 null,null,'task26-approve-decision','{}','1e000000-0000-4000-8000-000000000001');
create temporary table task26_approve_replay as
select * from public.motionly_decide_ai_edit_proposal(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',(select (result->>'id')::uuid from task26_approve_proposal),
 'approve',3,null,null,'task26-approve-decision','{}','1e000000-0000-4000-8000-000000000001');
-- Rejection uses a second canonical run and must never produce a plan.

insert into public.projects (id,workspace_id,created_by,title) values
 ('4e000000-0000-4000-8000-000000000009','3e000000-0000-4000-8000-000000000001','1e000000-0000-4000-8000-000000000001','Kept');

create temporary table task26_history as
select table_name, count
from (values
  ('workflow_runs', (select count(*) from public.workflow_runs where project_id='4e000000-0000-4000-8000-000000000001')),
  ('workflow_events', (select count(*) from public.workflow_events where project_id='4e000000-0000-4000-8000-000000000001')),
  ('workflow_step_attempts', (select count(*) from public.workflow_step_attempts where project_id='4e000000-0000-4000-8000-000000000001')),
  ('workflow_run_artifacts', (select count(*) from public.workflow_run_artifacts where project_id='4e000000-0000-4000-8000-000000000001')),
  ('ai_edit_proposal_actions', (select count(*) from public.ai_edit_proposal_actions where project_id='4e000000-0000-4000-8000-000000000001')),
  ('ai_edit_proposal_decisions', (select count(*) from public.ai_edit_proposal_decisions where project_id='4e000000-0000-4000-8000-000000000001')),
  ('timeline_revisions', (select count(*) from public.timeline_revisions where project_id='4e000000-0000-4000-8000-000000000001'))
) as history(table_name, count);
select is(
  (select count(*)::integer from task26_history where count > 0), 7,
  'the fixture project has history in every append-only and audit table'
);

-- While the project exists, its history cannot be removed or rewritten.
select throws_ok(
  $$delete from public.timeline_revisions where project_id='4e000000-0000-4000-8000-000000000001'$$,
  '55000', 'IMMUTABLE_RECORD: timeline_revisions rows are append-only',
  'timeline revisions cannot be deleted directly');
select throws_ok(
  $$delete from public.workflow_events where project_id='4e000000-0000-4000-8000-000000000001'$$,
  '55000', 'IMMUTABLE_RECORD: workflow_events rows are append-only',
  'workflow events cannot be deleted directly');
select throws_ok(
  $$update public.ai_edit_proposal_decisions set idempotency_key='rewritten'
    where project_id='4e000000-0000-4000-8000-000000000001'$$,
  '55000', 'IMMUTABLE_RECORD: ai_edit_proposal_decisions rows are append-only',
  'decisions cannot be rewritten');
select throws_ok(
  $$delete from public.workflow_runs where project_id='4e000000-0000-4000-8000-000000000001'$$,
  '55000', 'IMMUTABLE_RECORD: workflow_step_attempts rows cannot be deleted',
  'a workflow run with attempts cannot be deleted while its project exists');
select throws_ok(
  $$delete from public.ai_edit_proposals where project_id='4e000000-0000-4000-8000-000000000001'$$,
  '55000', 'IMMUTABLE_RECORD: ai_edit_proposal_actions rows are append-only',
  'a proposal cannot be deleted on its own (its actions are append-only)');
select throws_ok(
  $$delete from public.transcripts where project_id='4e000000-0000-4000-8000-000000000001'$$,
  '23503', null,
  'a transcript referenced by a proposal cannot be deleted on its own');

-- Deleting the project removes everything that belongs to it.
select lives_ok(
  $$delete from public.projects where id='4e000000-0000-4000-8000-000000000001'$$,
  'a project with workflow and proposal history can be deleted');
set constraints all immediate;
select is(
  (select count(*)::integer from public.workflow_runs where project_id='4e000000-0000-4000-8000-000000000001')
  + (select count(*)::integer from public.workflow_events where project_id='4e000000-0000-4000-8000-000000000001')
  + (select count(*)::integer from public.transactional_outbox where project_id='4e000000-0000-4000-8000-000000000001')
  + (select count(*)::integer from public.workflow_step_attempts where project_id='4e000000-0000-4000-8000-000000000001')
  + (select count(*)::integer from public.workflow_run_artifacts where project_id='4e000000-0000-4000-8000-000000000001')
  + (select count(*)::integer from public.workflow_run_commands where project_id='4e000000-0000-4000-8000-000000000001')
  + (select count(*)::integer from public.ai_edit_proposals where project_id='4e000000-0000-4000-8000-000000000001')
  + (select count(*)::integer from public.ai_edit_proposal_actions where project_id='4e000000-0000-4000-8000-000000000001')
  + (select count(*)::integer from public.ai_edit_proposal_decisions where project_id='4e000000-0000-4000-8000-000000000001')
  + (select count(*)::integer from public.timeline_revisions where project_id='4e000000-0000-4000-8000-000000000001')
  + (select count(*)::integer from public.edit_plans where project_id='4e000000-0000-4000-8000-000000000001'),
  0, 'the deleted project leaves no workflow, proposal or timeline rows');
select is(
  (select count(*)::integer from public.projects where id='4e000000-0000-4000-8000-000000000009'), 1,
  'other projects in the workspace are untouched');

-- Deleting a user's account (auth.users -> workspace -> projects) works too.
select lives_ok(
  $$delete from public.projects where id='4e000000-0000-4000-8000-000000000009'$$,
  'an empty project can still be deleted');
reset role;
select lives_ok(
  $$delete from auth.users where id='1e000000-0000-4000-8000-000000000001'$$,
  'deleting the owner removes the workspace and everything in it');
set constraints all immediate;
select is(
  (select count(*)::integer from public.workspaces where id='3e000000-0000-4000-8000-000000000001'), 0,
  'the workspace is gone');

-- Deferred checks are only relaxed for a project delete.
select ok(
  (select bool_and(not condeferred) from pg_constraint
    where contype='f' and connamespace='public'::regnamespace),
  'every foreign key is checked immediately unless a project delete defers it');

select * from finish();
rollback;
