begin;
select plan(31);

select has_function(
  'public', 'motionly_complete_workflow_step',
  array['uuid','uuid','uuid','text','uuid','text','jsonb','text','text'],
  'stable workflow completion RPC remains available'
);
select has_function(
  'public', 'motionly_decide_ai_edit_proposal',
  array['uuid','uuid','uuid','text','integer','uuid','integer','text','text[]','uuid'],
  'stable audited proposal decision RPC remains available'
);
select ok(
  has_function_privilege('service_role',
    'public.motionly_complete_workflow_step(uuid,uuid,uuid,text,uuid,text,jsonb,text,text)',
    'EXECUTE')
  and has_function_privilege('service_role',
    'public.motionly_decide_ai_edit_proposal(uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid)',
    'EXECUTE')
  and not has_function_privilege('authenticated',
    'public.motionly_decide_ai_edit_proposal(uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid)',
    'EXECUTE'),
  'stable completion and decision RPCs remain service-role-only'
);
select ok(
  not has_function_privilege('service_role',
    'public.motionly_complete_workflow_step_task7_audited(uuid,uuid,uuid,text,uuid,text,jsonb,text,text)',
    'EXECUTE')
  and not has_function_privilege('service_role',
    'public.motionly_decide_ai_edit_proposal_task11_audited(uuid,uuid,uuid,text,integer,uuid,integer,text,text[],uuid)',
    'EXECUTE')
  and not has_function_privilege('service_role',
    'public.motionly_validate_workflow_proposal_link()', 'EXECUTE'),
  'wrapped audited and trigger implementations are not directly executable'
);
select ok(
  exists (select 1 from pg_indexes where schemaname = 'public'
    and indexname = 'task5_one_proposal_per_workflow_run_idx'),
  'one-proposal-per-workflow-run index is preserved'
);
select ok(
  exists (select 1 from pg_constraint
    where conname = 'task5_workflow_proposal_creator_required'
      and conrelid = 'public.ai_edit_proposals'::regclass),
  'workflow-linked proposals require an initiating creator'
);

insert into auth.users (id,email,raw_user_meta_data) values
 ('1d000000-0000-4000-8000-000000000001','task13-owner@test.local','{}');
insert into public.workspaces (id,name,owner_user_id) values
 ('3d000000-0000-4000-8000-000000000001','Task 13','1d000000-0000-4000-8000-000000000001');
insert into public.workspace_members (workspace_id,user_id,role,status) values
 ('3d000000-0000-4000-8000-000000000001','1d000000-0000-4000-8000-000000000001','owner','active');
set local role service_role;

insert into public.projects (id,workspace_id,created_by,title) values
 ('4d000000-0000-4000-8000-000000000001','3d000000-0000-4000-8000-000000000001','1d000000-0000-4000-8000-000000000001','Approval'),
 ('4d000000-0000-4000-8000-000000000002','3d000000-0000-4000-8000-000000000001','1d000000-0000-4000-8000-000000000001','Rejection'),
 ('4d000000-0000-4000-8000-000000000003','3d000000-0000-4000-8000-000000000001','1d000000-0000-4000-8000-000000000001','Recovery');

insert into public.uploaded_videos (
 id,workspace_id,project_id,storage_bucket,storage_path,original_filename,
 mime_type,byte_size,file_size_bytes,status,workflow_version,
 duration_ms,width,height,has_audio
) values
 ('5d000000-0000-4000-8000-000000000001','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001',
  'raw-uploads','task13/approve.mp4','approve.mp4','video/mp4',2048,2048,'processing','first-draft-v2',10000,1920,1080,true),
 ('5d000000-0000-4000-8000-000000000002','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002',
  'raw-uploads','task13/reject.mp4','reject.mp4','video/mp4',2048,2048,'processing','first-draft-v2',10000,1920,1080,true),
 ('5d000000-0000-4000-8000-000000000003','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000003',
  'raw-uploads','task13/recover.mp4','recover.mp4','video/mp4',2048,2048,'processing','first-draft-v2',10000,1920,1080,true);

insert into public.object_references (
 id,workspace_id,project_id,storage_bucket,storage_path,owner_type,owner_id,
 object_type,mime_type,size_bytes,sha256
) values
 ('8d000000-0000-4000-8000-000000000001','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001',
  'raw-uploads','task13/approve.mp4','uploaded_video','5d000000-0000-4000-8000-000000000001','source_video','video/mp4',2048,repeat('a',64)),
 ('8d000000-0000-4000-8000-000000000002','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002',
  'raw-uploads','task13/reject.mp4','uploaded_video','5d000000-0000-4000-8000-000000000002','source_video','video/mp4',2048,repeat('b',64)),
 ('8d000000-0000-4000-8000-000000000003','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000003',
  'raw-uploads','task13/recover.mp4','uploaded_video','5d000000-0000-4000-8000-000000000003','source_video','video/mp4',2048,repeat('c',64));

update public.uploaded_videos video set
 source_object_reference_id = reference.id,
 verified_size_bytes = reference.size_bytes,
 verified_sha256 = reference.sha256,
 status = 'ready'
from public.object_references reference
where reference.owner_type = 'uploaded_video'
  and reference.owner_id = video.id
  and video.id in (
    '5d000000-0000-4000-8000-000000000001',
    '5d000000-0000-4000-8000-000000000002',
    '5d000000-0000-4000-8000-000000000003'
  );
update public.projects project set active_video_id = video.id
from public.uploaded_videos video
where video.project_id = project.id
  and project.id in (
    '4d000000-0000-4000-8000-000000000001',
    '4d000000-0000-4000-8000-000000000002',
    '4d000000-0000-4000-8000-000000000003'
  );

insert into public.transcripts (
 id,workspace_id,project_id,uploaded_video_id,status,language,provider,model,
 confidence,duration_ms,quality_status
) values
 ('6d000000-0000-4000-8000-000000000001','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','5d000000-0000-4000-8000-000000000001','ready','en','fixture','fixture',0.99,10000,'ok'),
 ('6d000000-0000-4000-8000-000000000002','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002','5d000000-0000-4000-8000-000000000002','ready','en','fixture','fixture',0.99,10000,'ok');
insert into public.transcript_words (
 id,workspace_id,project_id,transcript_id,word_index,text,start_ms,end_ms,confidence
) values
 ('7d000000-0000-4000-8000-000000000001','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','6d000000-0000-4000-8000-000000000001',0,'hello',0,500,0.99),
 ('7d000000-0000-4000-8000-000000000002','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002','6d000000-0000-4000-8000-000000000002',0,'hello',0,500,0.99);
insert into public.video_manifests (
 id,workspace_id,project_id,uploaded_video_id,version,status,provider,model,manifest
) values
 ('9d000000-0000-4000-8000-000000000001','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','5d000000-0000-4000-8000-000000000001',1,'ready','fixture','fixture','{"summary":{"scene_count":1}}'),
 ('9d000000-0000-4000-8000-000000000002','3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002','5d000000-0000-4000-8000-000000000002',1,'ready','fixture','fixture','{"summary":{"scene_count":1}}');

insert into public.workflow_runs (
 id,workspace_id,project_id,uploaded_video_id,source_media_revision,
 source_object_reference_id,source_size_bytes,source_sha256,workflow_type,
 workflow_version,idempotency_key,request_hash,temporal_workflow_id,state,started_by
)
select
 case video.id
  when '5d000000-0000-4000-8000-000000000001' then 'ad000000-0000-4000-8000-000000000001'::uuid
  else 'ad000000-0000-4000-8000-000000000002'::uuid end,
 video.workspace_id,video.project_id,video.id,video.media_revision,
 video.source_object_reference_id,video.verified_size_bytes,video.verified_sha256,
 'first_draft','first-draft-v2','task13-run-' || right(video.id::text,1),repeat('d',64),
 'motionly-task13-' || right(video.id::text,1),'proposal_ready','1d000000-0000-4000-8000-000000000001'
from public.uploaded_videos video
where video.id in (
 '5d000000-0000-4000-8000-000000000001','5d000000-0000-4000-8000-000000000002'
);

create temporary table task13_approve_proposal as
select result from public.motionly_create_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001',
 repeat('1',64),repeat('e',64),
 '{"controls":{"broll_density":"medium","prompt":""},"modality_degradation":[]}',
 '5d000000-0000-4000-8000-000000000001',
 (select media_revision from public.uploaded_videos where id='5d000000-0000-4000-8000-000000000001'),
 '6d000000-0000-4000-8000-000000000001','9d000000-0000-4000-8000-000000000001',1,
 null,null,null,'ai-edit-plan-v1','1d000000-0000-4000-8000-000000000001'
) result;
select * from public.motionly_link_workflow_artifact(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','ad000000-0000-4000-8000-000000000001',
 'transcript','6d000000-0000-4000-8000-000000000001',null);
select * from public.motionly_link_workflow_artifact(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','ad000000-0000-4000-8000-000000000001',
 'visual_manifest','9d000000-0000-4000-8000-000000000001',1);
select * from public.motionly_link_workflow_artifact(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','ad000000-0000-4000-8000-000000000001',
 'proposal',(select (result->>'id')::uuid from task13_approve_proposal),1);
select ok(
 (select count(*)=3 from public.workflow_run_artifacts where workflow_run_id='ad000000-0000-4000-8000-000000000001')
 and (select workflow_run_id='ad000000-0000-4000-8000-000000000001' from public.ai_edit_proposals where id=(select (result->>'id')::uuid from task13_approve_proposal)),
 'canonical transcript, visual manifest, and proposal are linked'
);

create temporary table task13_second_proposal as
select result from public.motionly_create_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001',
 repeat('2',64),repeat('f',64),'{"controls":{"broll_density":"medium"},"modality_degradation":[]}',
 '5d000000-0000-4000-8000-000000000001',
 (select media_revision from public.uploaded_videos where id='5d000000-0000-4000-8000-000000000001'),
 '6d000000-0000-4000-8000-000000000001','9d000000-0000-4000-8000-000000000001',1,
 null,null,null,'ai-edit-plan-v1','1d000000-0000-4000-8000-000000000001'
) result;
select throws_ok(
 $$select * from public.motionly_link_workflow_artifact(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','ad000000-0000-4000-8000-000000000001',
 'proposal',(select (result->>'id')::uuid from task13_second_proposal),1)$$,
 'P0001','WORKFLOW_ARTIFACT_CONFLICT','a workflow run cannot link a second proposal'
);

select * from public.motionly_prepare_workflow_step(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','ad000000-0000-4000-8000-000000000001',
 'generate-proposal-v2','generate_proposal',repeat('e',64),3,'workflow--task13--approve',(select (result->>'id')::uuid from task13_approve_proposal));
create temporary table task13_approve_begin as
select result from public.motionly_begin_workflow_step(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','ad000000-0000-4000-8000-000000000001',
 'generate-proposal-v2','generate_proposal','task13-approve-attempt','worker-a',60,repeat('e',64),3
) result;
select throws_ok(
 $$select * from public.motionly_complete_workflow_step(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','ad000000-0000-4000-8000-000000000001',
 'generate-proposal-v2',(select (result->'attempt'->>'claim_token')::uuid from task13_approve_begin),repeat('9',64),
 jsonb_build_object('proposal_id',(select result->>'id' from task13_approve_proposal),'status','queued','result_hash',repeat('0',64),'failed',false),
 'awaiting_approval','workflow.step.completed')$$,
 'P0001','WORKFLOW_PROPOSAL_NOT_APPROVAL_READY','planning cannot complete before canonical proposal approval readiness'
);
select ok(
 (select state='proposal_ready' from public.workflow_runs where id='ad000000-0000-4000-8000-000000000001')
 and (select state='running' from public.workflow_steps where workflow_run_id='ad000000-0000-4000-8000-000000000001' and step_key='generate-proposal-v2'),
 'failed planning fence leaves workflow and active attempt unchanged'
);

select * from public.motionly_transition_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001',(select (result->>'id')::uuid from task13_approve_proposal),'generating','{}');
select * from public.motionly_transition_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001',(select (result->>'id')::uuid from task13_approve_proposal),'awaiting_approval',
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
create temporary table task13_approve_complete as
select result from public.motionly_complete_workflow_step(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001','ad000000-0000-4000-8000-000000000001',
 'generate-proposal-v2',(select (result->'attempt'->>'claim_token')::uuid from task13_approve_begin),repeat('9',64),
 jsonb_build_object(
  'proposal_id',(select result->>'id' from task13_approve_proposal),'status','awaiting_approval',
  'result_hash',(select result_hash from public.ai_edit_proposals where id=(select (result->>'id')::uuid from task13_approve_proposal)),
  'failed',false
 ),'awaiting_approval','workflow.step.completed') result;
select ok(
 (select state='awaiting_approval' from public.workflow_runs where id='ad000000-0000-4000-8000-000000000001')
 and (select result->'step'->>'state'='succeeded' from task13_approve_complete),
 'canonical planning completion advances only to durable approval wait'
);
select throws_ok(
 $$select * from public.motionly_decide_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001',(select (result->>'id')::uuid from task13_approve_proposal),
 'approve',1,null,null,'task13-stale-approval','{}','1d000000-0000-4000-8000-000000000001')$$,
 'P0001','PROPOSAL_REVISION_STALE','stale proposal revision remains fenced by audited decision logic'
);
select is(
 (select count(*)::integer from public.ai_edit_proposal_decisions where project_id='4d000000-0000-4000-8000-000000000001'),
 0,'stale decision rolls back without artifacts'
);

create temporary table task13_approve_decision as
select * from public.motionly_decide_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001',(select (result->>'id')::uuid from task13_approve_proposal),
 'approve',(select revision from public.ai_edit_proposals where id=(select (result->>'id')::uuid from task13_approve_proposal)),
 null,null,'task13-approve-decision','{}','1d000000-0000-4000-8000-000000000001');
select ok(
 (select edit_plan_id is not null and edit_plan_revision > 0 and not idempotent_replay from task13_approve_decision),
 'approval atomically creates an active edit plan'
);
select ok(
 (select run.state='assembling' and run.active_plan_id=decision.edit_plan_id
   and run.active_plan_revision=decision.edit_plan_revision
  from public.workflow_runs run cross join task13_approve_decision decision
  where run.id='ad000000-0000-4000-8000-000000000001'),
 'approval projects workflow into canonical assembling state and plan revision'
);
select is(
 (select count(*)::integer from public.workflow_run_artifacts
  where workflow_run_id='ad000000-0000-4000-8000-000000000001'
    and artifact_type in ('decision','timeline_revision')),
 2,'approval links typed decision and timeline artifacts'
);
select ok(
 exists (select 1 from public.timeline_revisions timeline
  join task13_approve_decision decision on decision.decision_id=timeline.decision_id
   and decision.edit_plan_id=timeline.edit_plan_id
  where timeline.proposal_id=(select (result->>'id')::uuid from task13_approve_proposal)),
 'approval timeline preserves proposal, decision, and plan lineage'
);
select ok(
 (select count(*)=1 from public.workflow_events where workflow_run_id='ad000000-0000-4000-8000-000000000001' and event_type='workflow.approval.decided')
 and (select count(*)=1 from public.transactional_outbox where workflow_run_id='ad000000-0000-4000-8000-000000000001' and event_type='workflow.approval.decided'),
 'approval appends exactly one event and one Temporal outbox fact'
);
select ok(
 (select payload ?& array['workflow_run_id','proposal_id','decision_id','decision','proposal_revision','workflow_revision','edit_plan_id','edit_plan_revision']
   and not payload ?| array['actions','plan','timeline','context','url','token','credential','transcript']
  from public.transactional_outbox
  where workflow_run_id='ad000000-0000-4000-8000-000000000001' and event_type='workflow.approval.decided'),
 'approval outbox is exact identifier/revision metadata without sensitive payloads'
);
create temporary table task13_approve_replay as
select * from public.motionly_decide_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000001',(select (result->>'id')::uuid from task13_approve_proposal),
 'approve',3,null,null,'task13-approve-decision','{}','1d000000-0000-4000-8000-000000000001');
select ok(
 (select idempotent_replay from task13_approve_replay)
 and (select count(*)=1 from public.transactional_outbox where workflow_run_id='ad000000-0000-4000-8000-000000000001' and event_type='workflow.approval.decided'),
 'lost approval response rediscovers the same decision and outbox fact'
);
select is(
 (select count(*)::integer from public.ai_edit_proposals where workflow_run_id='ad000000-0000-4000-8000-000000000001'),
 1,'exactly one canonical proposal is workflow-linked'
);

-- Rejection uses a second canonical run and must never produce a plan.
create temporary table task13_reject_proposal as
select result from public.motionly_create_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002',
 repeat('3',64),repeat('4',64),'{"controls":{"broll_density":"medium"},"modality_degradation":[]}',
 '5d000000-0000-4000-8000-000000000002',
 (select media_revision from public.uploaded_videos where id='5d000000-0000-4000-8000-000000000002'),
 '6d000000-0000-4000-8000-000000000002','9d000000-0000-4000-8000-000000000002',1,
 null,null,null,'ai-edit-plan-v1','1d000000-0000-4000-8000-000000000001'
) result;
select * from public.motionly_link_workflow_artifact('3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002','ad000000-0000-4000-8000-000000000002','transcript','6d000000-0000-4000-8000-000000000002',null);
select * from public.motionly_link_workflow_artifact('3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002','ad000000-0000-4000-8000-000000000002','visual_manifest','9d000000-0000-4000-8000-000000000002',1);
select * from public.motionly_link_workflow_artifact('3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002','ad000000-0000-4000-8000-000000000002','proposal',(select (result->>'id')::uuid from task13_reject_proposal),1);
select * from public.motionly_transition_ai_edit_proposal('3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002',(select (result->>'id')::uuid from task13_reject_proposal),'generating','{}');
select * from public.motionly_transition_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002',(select (result->>'id')::uuid from task13_reject_proposal),'awaiting_approval',
 jsonb_build_object(
  'generated_plan','{"schema_version":2,"style":"founder","target_duration_s":10,"cuts":[]}'::jsonb,
  'generated_summary','{}'::jsonb,
  'actions',jsonb_build_array(jsonb_build_object(
   'id','action_01_bbbbbbbbbbbb','ordinal',0,'tool_name','complete_first_plan','tool_version','1',
   'args',jsonb_build_object('plan','{"schema_version":2,"style":"founder","target_duration_s":10,"cuts":[]}'::jsonb),
   'rationale','Create a rejectable plan','confidence',0.9,'dependency_ids','[]'::jsonb,
   'risk_flags','[]'::jsonb,'dry_run_hash',repeat('b',64)
  ))
 ));
select throws_ok(
 $$select * from public.motionly_decide_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002',(select (result->>'id')::uuid from task13_reject_proposal),
 'reject',3,null,null,'task13-early-reject','{}','1d000000-0000-4000-8000-000000000001')$$,
 'P0001','WORKFLOW_APPROVAL_STATE_CONFLICT','decision and workflow projection roll back together before approval wait'
);
select ok(
 (select state='awaiting_approval' from public.ai_edit_proposals where id=(select (result->>'id')::uuid from task13_reject_proposal))
 and not exists (select 1 from public.ai_edit_proposal_decisions where project_id='4d000000-0000-4000-8000-000000000002'),
 'failed workflow projection rolls back the audited rejection mutation'
);
select * from public.motionly_prepare_workflow_step(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002','ad000000-0000-4000-8000-000000000002',
 'generate-proposal-v2','generate_proposal',repeat('4',64),3,'workflow--task13--reject',(select (result->>'id')::uuid from task13_reject_proposal));
create temporary table task13_reject_begin as
select result from public.motionly_begin_workflow_step(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002','ad000000-0000-4000-8000-000000000002',
 'generate-proposal-v2','generate_proposal','task13-reject-attempt','worker-b',60,repeat('4',64),3
) result;
select * from public.motionly_complete_workflow_step(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002','ad000000-0000-4000-8000-000000000002',
 'generate-proposal-v2',(select (result->'attempt'->>'claim_token')::uuid from task13_reject_begin),repeat('8',64),
 jsonb_build_object(
  'proposal_id',(select result->>'id' from task13_reject_proposal),'status','awaiting_approval',
  'result_hash',(select result_hash from public.ai_edit_proposals where id=(select (result->>'id')::uuid from task13_reject_proposal)),
  'failed',false
 ),'awaiting_approval','workflow.step.completed');
select is(
 (select state from public.workflow_runs where id='ad000000-0000-4000-8000-000000000002'),
 'awaiting_approval','rejection run first enters durable approval wait'
);
create temporary table task13_reject_decision as
select * from public.motionly_decide_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002',(select (result->>'id')::uuid from task13_reject_proposal),
 'reject',3,null,null,'task13-reject-decision','{}','1d000000-0000-4000-8000-000000000001');
select ok(
 (select edit_plan_id is null and not idempotent_replay from task13_reject_decision),
 'rejection returns no edit plan'
);
select ok(
 (select state='cancelled' and active_plan_id is null and active_plan_revision is null and completed_at is not null
  from public.workflow_runs where id='ad000000-0000-4000-8000-000000000002'),
 'rejection terminalizes workflow cancellation without an active plan'
);
select ok(
 (select state='rejected' from public.ai_edit_proposals where id=(select (result->>'id')::uuid from task13_reject_proposal))
 and not exists (select 1 from public.edit_plans where project_id='4d000000-0000-4000-8000-000000000002')
 and not exists (select 1 from public.timeline_revisions where project_id='4d000000-0000-4000-8000-000000000002'),
 'rejection creates neither plan nor approval timeline'
);
select is(
 (select count(*)::integer from public.workflow_run_artifacts
  where workflow_run_id='ad000000-0000-4000-8000-000000000002' and artifact_type='decision'),
 1,'rejection links its typed decision artifact'
);
select ok(
 (select payload->>'decision'='reject'
   and not payload ?| array['edit_plan_id','edit_plan_revision','actions','plan','timeline','context','url','token','credential','transcript']
  from public.transactional_outbox
  where workflow_run_id='ad000000-0000-4000-8000-000000000002' and event_type='workflow.approval.decided'),
 'rejection emits identifier-only outbox without plan fields'
);
select * from public.motionly_decide_ai_edit_proposal(
 '3d000000-0000-4000-8000-000000000001','4d000000-0000-4000-8000-000000000002',(select (result->>'id')::uuid from task13_reject_proposal),
 'reject',3,null,null,'task13-reject-decision','{}','1d000000-0000-4000-8000-000000000001');
select ok(
 (select count(*)=1 from public.ai_edit_proposal_decisions where project_id='4d000000-0000-4000-8000-000000000002')
 and (select count(*)=1 from public.transactional_outbox where workflow_run_id='ad000000-0000-4000-8000-000000000002' and event_type='workflow.approval.decided'),
 'duplicate rejection rediscovers one decision and one outbox fact'
);

create temporary table task13_recovered as
select reconciled as result
from public.motionly_reconcile_first_draft_v2(10) as reconciled;
select ok(
 (select count(*)=1 from task13_recovered)
 and exists (
  select 1 from public.workflow_runs
  where project_id='4d000000-0000-4000-8000-000000000003'
    and started_by='1d000000-0000-4000-8000-000000000001'
 ),
 'upload reconciler derives the project creator as canonical initiating user'
);

select * from finish();
rollback;
