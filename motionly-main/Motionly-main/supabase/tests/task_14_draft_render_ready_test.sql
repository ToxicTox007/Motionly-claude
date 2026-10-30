begin;
select plan(26);

select has_function(
  'public','motionly_ensure_workflow_draft_render',
  array['uuid','uuid','uuid','uuid','uuid','integer','uuid','integer','uuid','text','jsonb','jsonb','jsonb','text','text','text','text','jsonb','integer'],
  'atomic workflow draft-render ensure RPC exists'
);
select has_function(
  'public','motionly_complete_workflow_step',
  array['uuid','uuid','uuid','text','uuid','text','jsonb','text','text'],
  'stable workflow completion RPC remains available'
);
select ok(
  has_function_privilege('service_role',
    'public.motionly_ensure_workflow_draft_render(uuid,uuid,uuid,uuid,uuid,integer,uuid,integer,uuid,text,jsonb,jsonb,jsonb,text,text,text,text,jsonb,integer)',
    'EXECUTE')
  and not has_function_privilege('authenticated',
    'public.motionly_ensure_workflow_draft_render(uuid,uuid,uuid,uuid,uuid,integer,uuid,integer,uuid,text,jsonb,jsonb,jsonb,text,text,text,text,jsonb,integer)',
    'EXECUTE')
  and not has_function_privilege('anon',
    'public.motionly_ensure_workflow_draft_render(uuid,uuid,uuid,uuid,uuid,integer,uuid,integer,uuid,text,jsonb,jsonb,jsonb,text,text,text,text,jsonb,integer)',
    'EXECUTE'),
  'draft-render ensure is service-role-only'
);
select ok(
  not has_function_privilege('service_role',
    'public.motionly_complete_workflow_step_task5_proposal_gate_audited(uuid,uuid,uuid,text,uuid,text,jsonb,text,text)',
    'EXECUTE')
  and has_function_privilege('service_role',
    'public.motionly_complete_workflow_step(uuid,uuid,uuid,text,uuid,text,jsonb,text,text)',
    'EXECUTE'),
  'only the stable completion wrapper is executable by service role'
);
select ok(
  exists (select 1 from pg_indexes where schemaname='public'
    and indexname='task5_one_draft_render_per_workflow_run_idx')
  and exists (select 1 from pg_indexes where schemaname='public'
    and indexname='task5_one_base_export_per_render_idx'),
  'one render per workflow and one base export per render barriers remain present'
);

insert into auth.users (id,email,raw_user_meta_data) values
 ('1e000000-0000-4000-8000-000000000001','task14-owner@test.local','{}');
insert into public.workspaces (id,name,owner_user_id) values
 ('3e000000-0000-4000-8000-000000000001','Task 14','1e000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id,user_id,role,status) values
 ('3e000000-0000-4000-8000-000000000001','1e000000-0000-4000-8000-000000000001','owner','active');
set local role service_role;
insert into public.projects (id,workspace_id,created_by,title) values
 ('4e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001','1e000000-0000-4000-8000-000000000001','Draft render');
insert into public.uploaded_videos (
 id,workspace_id,project_id,storage_bucket,storage_path,original_filename,
 mime_type,byte_size,file_size_bytes,status,workflow_version,
 duration_ms,width,height,has_audio
) values (
 '5e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001',
 '4e000000-0000-4000-8000-000000000001','raw-uploads','task14/source.mp4',
 'source.mp4','video/mp4',4096,4096,'processing','first-draft-v2',10000,1920,1080,true
);
insert into public.object_references (
 id,workspace_id,project_id,storage_bucket,storage_path,owner_type,owner_id,
 object_type,mime_type,size_bytes,sha256
) values (
 '8e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001',
 '4e000000-0000-4000-8000-000000000001','raw-uploads','task14/source.mp4',
 'uploaded_video','5e000000-0000-4000-8000-000000000001','source_video',
 'video/mp4',4096,repeat('a',64)
);
update public.uploaded_videos set
 source_object_reference_id='8e000000-0000-4000-8000-000000000001',
 verified_size_bytes=4096,verified_sha256=repeat('a',64),status='ready'
where id='5e000000-0000-4000-8000-000000000001';
update public.projects set active_video_id='5e000000-0000-4000-8000-000000000001'
where id='4e000000-0000-4000-8000-000000000001';
insert into public.transcripts (
 id,workspace_id,project_id,uploaded_video_id,status,language,provider,model,
 confidence,duration_ms,quality_status
) values (
 '6e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001',
 '4e000000-0000-4000-8000-000000000001','5e000000-0000-4000-8000-000000000001',
 'ready','en','fixture','fixture',0.99,10000,'ok'
);
insert into public.transcript_words (
 id,workspace_id,project_id,transcript_id,word_index,text,start_ms,end_ms,confidence
) values (
 '7e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001',
 '4e000000-0000-4000-8000-000000000001','6e000000-0000-4000-8000-000000000001',
 0,'draft',0,500,0.99
);
insert into public.video_manifests (
 id,workspace_id,project_id,uploaded_video_id,version,status,provider,model,manifest
) values (
 '9e000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001',
 '4e000000-0000-4000-8000-000000000001','5e000000-0000-4000-8000-000000000001',
 1,'ready','fixture','fixture','{"summary":{"scene_count":1}}'
);
insert into public.workflow_runs (
 id,workspace_id,project_id,uploaded_video_id,source_media_revision,
 source_object_reference_id,source_size_bytes,source_sha256,workflow_type,
 workflow_version,idempotency_key,request_hash,temporal_workflow_id,state,started_by
) select
 'ae000000-0000-4000-8000-000000000001',video.workspace_id,video.project_id,video.id,
 video.media_revision,video.source_object_reference_id,video.verified_size_bytes,
 video.verified_sha256,'first_draft','first-draft-v2','task14-render-run',repeat('b',64),
 'motionly-task14-render','proposal_ready','1e000000-0000-4000-8000-000000000001'
from public.uploaded_videos video where video.id='5e000000-0000-4000-8000-000000000001';

insert into public.edit_plans (
 id,workspace_id,project_id,transcript_id,version,schema_version,status,is_active,
 style,target_duration_s,broll_density,prompt,provider,model,cache_key,schema_hash,
 plan,summary,revision
) values (
 'be000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001',
 '4e000000-0000-4000-8000-000000000001','6e000000-0000-4000-8000-000000000001',
 1,2,'ready',true,'founder',10,'none','approved','fixture','fixture-plan',
 'task14-plan',repeat('c',64),
 '{"schema_version":2,"style":"founder","target_duration_s":10,"cuts":[],"music_mood":"none","color_grade":"natural"}',
 '{"duration_s":10}',1
);
insert into public.ai_edit_proposals (
 id,workspace_id,project_id,request_id,request_hash,state,revision,context_digest,
 context_snapshot,upload_id,media_revision,transcript_id,visual_manifest_id,
 visual_manifest_version,prompt_version,result_hash,generated_plan,generated_summary,
 created_by,workflow_run_id
) select
 'ce000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001',
 '4e000000-0000-4000-8000-000000000001','task14-proposal-request',repeat('d',64),
 'approved',1,repeat('e',64),'{}',video.id,video.media_revision,
 '6e000000-0000-4000-8000-000000000001','9e000000-0000-4000-8000-000000000001',1,
 'ai-edit-plan-v1',repeat('f',64),
 '{"schema_version":2,"style":"founder","target_duration_s":10,"cuts":[]}',
 '{}','1e000000-0000-4000-8000-000000000001','ae000000-0000-4000-8000-000000000001'
from public.uploaded_videos video where video.id='5e000000-0000-4000-8000-000000000001';
insert into public.ai_edit_proposal_decisions (
 id,proposal_id,workspace_id,project_id,decision,expected_proposal_revision,
 idempotency_key,request_hash,accepted_action_ids,result_hash,edit_plan_id,decided_by
) values (
 'de000000-0000-4000-8000-000000000001','ce000000-0000-4000-8000-000000000001',
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 'approve',1,'task14-approval',repeat('1',64),'{}',repeat('2',64),
 'be000000-0000-4000-8000-000000000001','1e000000-0000-4000-8000-000000000001'
);
insert into public.timeline_revisions (
 id,workspace_id,project_id,edit_plan_id,revision,plan_snapshot,changed_by,
 change_type,proposal_id,decision_id,result_hash
) values (
 'ee000000-0000-4000-8000-000000000001','3e000000-0000-4000-8000-000000000001',
 '4e000000-0000-4000-8000-000000000001','be000000-0000-4000-8000-000000000001',
 1,(select plan from public.edit_plans where id='be000000-0000-4000-8000-000000000001'),
 '1e000000-0000-4000-8000-000000000001','approval',
 'ce000000-0000-4000-8000-000000000001','de000000-0000-4000-8000-000000000001',repeat('2',64)
);
select * from public.motionly_link_workflow_artifact(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 'ae000000-0000-4000-8000-000000000001','decision','de000000-0000-4000-8000-000000000001',null
);
select * from public.motionly_link_workflow_artifact(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 'ae000000-0000-4000-8000-000000000001','timeline_revision','ee000000-0000-4000-8000-000000000001',1
);
update public.workflow_runs set
 state='assembling',active_plan_id='be000000-0000-4000-8000-000000000001',
 active_plan_revision=1,current_step_key='approval-gate-v2'
where id='ae000000-0000-4000-8000-000000000001';

create temporary table task14_render as
select * from public.motionly_ensure_workflow_draft_render(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 'ae000000-0000-4000-8000-000000000001','5e000000-0000-4000-8000-000000000001',
 'be000000-0000-4000-8000-000000000001',1,'ee000000-0000-4000-8000-000000000001',
 (select media_revision from public.uploaded_videos where id='5e000000-0000-4000-8000-000000000001'),
 null,'timeline-v1',
 jsonb_build_object(
  'workflow_run_id','ae000000-0000-4000-8000-000000000001',
  'edit_plan_id','be000000-0000-4000-8000-000000000001',
  'timeline_revision_id','ee000000-0000-4000-8000-000000000001',
  'source_plan_revision',1,
  'source_media_revision',(select media_revision from public.uploaded_videos where id='5e000000-0000-4000-8000-000000000001'),
  'props_hash',repeat('3',64),'render_engine','ffmpeg_basic','compute_tier','gpu',
  'timeline',jsonb_build_object('schema_version',1,'duration_ms',10000,'clips',jsonb_build_array(jsonb_build_object('id','source'))),
  'render_props',jsonb_build_object('timeline',jsonb_build_object('schema_version',1,'duration_ms',10000,'clips',jsonb_build_array(jsonb_build_object('id','source')))),
  'source',jsonb_build_object('uploaded_video_id','5e000000-0000-4000-8000-000000000001','reference_id','8e000000-0000-4000-8000-000000000001')
 ),
 '[]','{"width":1080,"height":1920,"fps":30}'::jsonb,null,'ffmpeg_basic','ffmpeg-timeline-v3',repeat('3',64),'[]',5
);
select is((select count(*)::integer from task14_render),1,
 'initial ensure returns exactly one PostgreSQL-generated render target');
select ok(
 (select state='draft_rendering' and current_step_key='render-first-draft-v2'
  from public.workflow_runs where id='ae000000-0000-4000-8000-000000000001')
 and (select count(*)=1 from public.workflow_run_artifacts
  where workflow_run_id='ae000000-0000-4000-8000-000000000001'
    and artifact_type='draft_render_job'),
 'ensure atomically binds render artifact and advances assembling to draft_rendering'
);
select is(
 (select count(*)::integer from public.motionly_ensure_workflow_draft_render(
  '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
  'ae000000-0000-4000-8000-000000000001','5e000000-0000-4000-8000-000000000001',
  'be000000-0000-4000-8000-000000000001',1,'ee000000-0000-4000-8000-000000000001',
  (select media_revision from public.uploaded_videos where id='5e000000-0000-4000-8000-000000000001'),
  null,'timeline-v1',(select manifest from task14_render),'[]','{}',null,
  'ffmpeg_basic','ffmpeg-timeline-v3',repeat('3',64),'[]',5)),
 1,'lost ensure response rediscovers the same target'
);
select ok(
 (select count(*)=1 from public.render_jobs where workflow_run_id='ae000000-0000-4000-8000-000000000001')
 and (select count(distinct id)=1 from task14_render),
 'idempotent ensure leaves one immutable render UUID'
);
select throws_ok(
 $$select * from public.motionly_ensure_workflow_draft_render(
  '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
  'ae000000-0000-4000-8000-000000000001','5e000000-0000-4000-8000-000000000001',
  'be000000-0000-4000-8000-000000000001',1,'ee000000-0000-4000-8000-000000000001',
  (select media_revision from public.uploaded_videos where id='5e000000-0000-4000-8000-000000000001'),
  null,'timeline-v1',(select jsonb_set(manifest,'{props_hash}',to_jsonb(repeat('4',64))) from task14_render),
  '[]','{"width":1080,"height":1920,"fps":30}'::jsonb,null,'ffmpeg_basic','ffmpeg-timeline-v3',repeat('4',64),'[]',5)$$,
 'P0001','WORKFLOW_RENDER_IDENTITY_CONFLICT','retry cannot mutate immutable props identity'
);
select throws_ok(
 $$select * from public.motionly_ensure_workflow_draft_render(
  '3e000000-0000-4000-8000-000000000002','4e000000-0000-4000-8000-000000000001',
  'ae000000-0000-4000-8000-000000000001','5e000000-0000-4000-8000-000000000001',
  'be000000-0000-4000-8000-000000000001',1,'ee000000-0000-4000-8000-000000000001',1,
  null,'timeline-v1','{}','[]','{}',null,'ffmpeg_basic','v1',repeat('5',64),'[]',5)$$,
 'P0001','WORKFLOW_RENDER_MANIFEST_INVALID','cross-tenant request cannot reuse canonical target'
);
select throws_ok(
 $$update public.render_jobs set source_plan_revision=2
   where id=(select id from task14_render)$$,
 'P0001','RENDER_WORKFLOW_RUN_LINEAGE_INVALID','workflow render lineage cannot diverge from active plan'
);
update public.workflow_runs set active_plan_revision=2
where id='ae000000-0000-4000-8000-000000000001';
select throws_ok(
 $$select * from public.motionly_complete_render(
   '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
   (select id from task14_render),'exports',
   'workspaces/3e000000-0000-4000-8000-000000000001/projects/4e000000-0000-4000-8000-000000000001/exports/' ||
     (select id::text from task14_render) || '/attempts/task14-lineage-a-1/final.mp4',
   'mp4',1080,1920,30,10000,4096,null,
   '{"render_engine":"remotion_premium","renderer_version":"remotion-timeline-v2"}'::jsonb,1)$$,
 'P0001','RENDER_WORKFLOW_LINEAGE_INVALID','CAS completion rejects mismatched workflow active-plan lineage'
);
update public.workflow_runs set active_plan_revision=1
where id='ae000000-0000-4000-8000-000000000001';

select * from public.motionly_prepare_workflow_step(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 'ae000000-0000-4000-8000-000000000001','render-first-draft-v2','render_draft',
 repeat('3',64),3,'workflow--task14--render',(select id from task14_render)
);
create temporary table task14_begin as
select result from public.motionly_begin_workflow_step(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 'ae000000-0000-4000-8000-000000000001','render-first-draft-v2','render_draft',
 'task14-render-attempt','worker-render',60,repeat('3',64),3
) result;
select throws_ok(
 $$update public.render_jobs set status='complete',progress_pct=100
   where id=(select id from task14_render)$$,
 'P0001','WORKFLOW_RENDER_EXPORT_MISSING','render cannot complete before canonical export exists'
);
select throws_ok(
 $$select * from public.motionly_complete_workflow_step(
  '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
  'ae000000-0000-4000-8000-000000000001','render-first-draft-v2',
  (select (result->'attempt'->>'claim_token')::uuid from task14_begin),repeat('6',64),
  jsonb_build_object('render_job_id',(select id::text from task14_render),'render_status','complete','failed',false),
  'draft_ready','workflow.step.completed')$$,
 'P0001','WORKFLOW_DRAFT_RENDER_LINEAGE_INVALID','completion rejects a non-complete canonical render'
);

create temporary table task14_export_first as
select * from public.motionly_upsert_base_export(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 (select id from task14_render),'exports','task14/base.mp4','mp4',1080,1920,30,10000,4096,null,'{}'
);
create temporary table task14_export_replay as
select * from public.motionly_upsert_base_export(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 (select id from task14_render),'exports','task14/base.mp4','mp4',1080,1920,30,10000,4096,null,'{}'
);
select ok(
 (select id=(select id from task14_export_replay) from task14_export_first)
 and (select count(*)=1 from public.exports
      where render_job_id=(select id from task14_render) and restored_from_export_id is null),
 'race-safe base export upsert converges on one canonical row'
);
select ok(
 (select workflow_run_id='ae000000-0000-4000-8000-000000000001'
     and source_plan_revision=1 from task14_export_first),
 'base export inherits workflow and source-plan lineage from render'
);
select throws_ok(
 $$insert into public.exports (
  workspace_id,project_id,render_job_id,workflow_run_id,source_plan_revision,version,
  storage_bucket,storage_path,format,width,height,fps,restored_from_export_id
 ) select workspace_id,project_id,id,workflow_run_id,source_plan_revision,2,
  'exports','task14/duplicate.mp4','mp4',1080,1920,30,null from task14_render$$,
 '23505',null,'unique base-export barrier rejects a second canonical export'
);
update public.render_jobs set status='complete',progress_pct=100
where id=(select id from task14_render);
select ok(
 (select status='complete' and progress_pct=100 from public.render_jobs
  where id=(select id from task14_render)),
 'render completes only after its canonical export exists'
);
select throws_ok(
 $$select * from public.motionly_complete_workflow_step(
  '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
  'ae000000-0000-4000-8000-000000000001','render-first-draft-v2',
  (select (result->'attempt'->>'claim_token')::uuid from task14_begin),repeat('6',64),
  jsonb_build_object('render_job_id',(select id::text from task14_render),'render_status','complete','failed',false,'export_id',(select id::text from task14_export_first)),
  'draft_ready','workflow.step.completed')$$,
 'P0001','WORKFLOW_DRAFT_RENDER_RESULT_INVALID','completion rejects arbitrary worker result fields'
);
select throws_ok(
 $$select * from public.motionly_complete_workflow_step(
  '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
  'ae000000-0000-4000-8000-000000000001','render-first-draft-v2',
  (select (result->'attempt'->>'claim_token')::uuid from task14_begin),repeat('6',64),
  jsonb_build_object('render_job_id',(select id::text from task14_render),'render_status','complete','failed',false),
  'export_ready','workflow.step.completed')$$,
 'P0001','WORKFLOW_DRAFT_RENDER_RESULT_INVALID','render_draft cannot choose an arbitrary next state'
);
create temporary table task14_complete as
select result from public.motionly_complete_workflow_step(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 'ae000000-0000-4000-8000-000000000001','render-first-draft-v2',
 (select (result->'attempt'->>'claim_token')::uuid from task14_begin),repeat('6',64),
 jsonb_build_object('render_job_id',(select id::text from task14_render),'render_status','complete','failed',false),
 'draft_ready','workflow.step.completed'
) result;
select ok(
 (select state='draft_ready' from public.workflow_runs
  where id='ae000000-0000-4000-8000-000000000001')
 and (select result->'step'->>'state'='succeeded' from task14_complete),
 'exact compact success advances canonical workflow to draft_ready'
);
select ok(
 (select count(*)=1 from public.workflow_run_artifacts
  where workflow_run_id='ae000000-0000-4000-8000-000000000001'
    and artifact_type='draft_export'
    and export_id=(select id from task14_export_first)),
 'successful completion links the canonical draft export artifact'
);
select is(
 (select count(*)::integer from public.motionly_complete_workflow_step(
  '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
  'ae000000-0000-4000-8000-000000000001','render-first-draft-v2',
  (select (result->'attempt'->>'claim_token')::uuid from task14_begin),repeat('6',64),
  jsonb_build_object('render_job_id',(select id::text from task14_render),'render_status','complete','failed',false),
  'draft_ready','workflow.step.completed')),
 1,'exact completion replay is idempotent'
);

-- Simulate a render still uploading when the run is cancelled. A complete
-- render can no longer change status (RENDER_TERMINAL_STATE, migration
-- 202610280202), so the test steps around that guard for its own setup only.
reset role;
alter table public.render_jobs disable trigger task25_guard_render_terminal_state;
update public.render_jobs set status='uploading' where id=(select id from task14_render);
alter table public.render_jobs enable trigger task25_guard_render_terminal_state;
set local role service_role;
select * from public.motionly_cancel_workflow_run(
 '3e000000-0000-4000-8000-000000000001','4e000000-0000-4000-8000-000000000001',
 'ae000000-0000-4000-8000-000000000001',
 (select revision from public.workflow_runs where id='ae000000-0000-4000-8000-000000000001'),
 'task14-cancel-after-ready','1e000000-0000-4000-8000-000000000001'
);
select is((select state from public.workflow_runs where id='ae000000-0000-4000-8000-000000000001'),
 'cancelled','cancellation canonicalizes the run before late worker effects');
select throws_ok(
 $$update public.render_jobs set status='complete' where id=(select id from task14_render)$$,
 'P0001','WORKFLOW_RENDER_RUN_NOT_ACTIVE','late render success cannot reopen a cancelled workflow'
);

select * from finish();
rollback;
