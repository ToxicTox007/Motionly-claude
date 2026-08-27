-- Resolve PL/pgSQL lint warnings without editing applied migrations.

create or replace function public.motionly_transition_ai_edit_proposal(
  p_workspace_id uuid,
  p_project_id uuid,
  p_proposal_id uuid,
  p_target_state text,
  p_completion jsonb default '{}'::jsonb
)
returns setof public.ai_edit_proposals
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  proposal public.ai_edit_proposals;
  action jsonb;
  action_ids text[] := array[]::text[];
  dependency text;
  generated_hash text;
begin
  select * into proposal
  from public.ai_edit_proposals
  where id = p_proposal_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    raise exception 'PROPOSAL_NOT_FOUND' using errcode = 'P0002';
  end if;

  if p_target_state = 'generating' then
    if proposal.state in ('awaiting_approval', 'approved', 'partially_approved', 'rejected') then
      return next proposal;
      return;
    end if;
    if proposal.state not in ('queued', 'generating', 'failed') then
      raise exception 'PROPOSAL_STATE_CONFLICT' using errcode = 'P0001';
    end if;
    if proposal.state <> 'generating' then
      update public.ai_edit_proposals
      set state = 'generating', revision = revision + 1, updated_at = now()
      where id = proposal.id
      returning * into proposal;
    end if;
    return next proposal;
    return;
  end if;

  if p_target_state not in ('awaiting_approval', 'failed') or proposal.state <> 'generating' then
    raise exception 'PROPOSAL_STATE_CONFLICT' using errcode = 'P0001';
  end if;

  if p_target_state = 'awaiting_approval' then
    if p_completion->'generated_plan' is null
       or jsonb_typeof(p_completion->'actions') <> 'array'
       or jsonb_array_length(p_completion->'actions') = 0 then
      raise exception 'PROPOSAL_COMPLETION_INVALID' using errcode = '22023';
    end if;

    for action in select value from jsonb_array_elements(p_completion->'actions')
    loop
      if action->>'tool_name' not in (
        'complete_first_plan', 'set_cuts', 'set_caption_highlights',
        'set_broll_requests', 'set_look_audio_effects'
      ) or action->>'tool_version' <> '1'
         or jsonb_typeof(action->'args') <> 'object' then
        raise exception 'PROPOSAL_TOOL_INVALID' using errcode = '22023';
      end if;
      if action->>'id' = any(action_ids) then
        raise exception 'PROPOSAL_ACTION_DUPLICATE' using errcode = '22023';
      end if;
      action_ids := array_append(action_ids, action->>'id');
    end loop;

    for action in select value from jsonb_array_elements(p_completion->'actions')
    loop
      for dependency in select jsonb_array_elements_text(coalesce(action->'dependency_ids', '[]'::jsonb))
      loop
        if dependency <> all(action_ids) then
          raise exception 'PROPOSAL_DEPENDENCY_INVALID' using errcode = '22023';
        end if;
      end loop;
      insert into public.ai_edit_proposal_actions (
        proposal_id, workspace_id, project_id, action_id, ordinal,
        tool_name, tool_version, args, rationale, confidence,
        dependency_ids, risk_flags, dry_run_hash
      ) values (
        proposal.id, proposal.workspace_id, proposal.project_id,
        action->>'id', (action->>'ordinal')::integer, action->>'tool_name',
        action->>'tool_version', action->'args', action->>'rationale',
        (action->>'confidence')::numeric,
        array(select jsonb_array_elements_text(coalesce(action->'dependency_ids', '[]'::jsonb))),
        coalesce(action->'risk_flags', '[]'::jsonb), action->>'dry_run_hash'
      );
    end loop;
    generated_hash := encode(digest(
      proposal.workspace_id::text || ':' || proposal.context_digest || ':' ||
      (p_completion->'generated_plan')::text || ':' || array_to_string(action_ids, ','),
      'sha256'
    ), 'hex');
  end if;

  update public.ai_edit_proposals
  set state = p_target_state,
      revision = revision + 1,
      generated_plan = p_completion->'generated_plan',
      generated_summary = p_completion->'generated_summary',
      provider = p_completion#>>'{provenance,provider}',
      model = p_completion#>>'{provenance,model}',
      model_revision = p_completion#>>'{provenance,model_revision}',
      route_id = p_completion#>>'{provenance,route_id}',
      gateway_request_id = p_completion#>>'{provenance,gateway_request_id}',
      planner_source = p_completion#>>'{provenance,planner_source}',
      overall_rationale = p_completion->>'overall_rationale',
      confidence = nullif(p_completion->>'confidence', '')::numeric,
      risk_flags = coalesce(p_completion->'risk_flags', '[]'::jsonb),
      validation_history_codes = coalesce(p_completion->'validation_history_codes', '[]'::jsonb),
      retry_history_codes = coalesce(p_completion->'retry_history_codes', '[]'::jsonb),
      fallback_history_codes = coalesce(p_completion->'fallback_history_codes', '[]'::jsonb),
      estimated_cost_usd = nullif(p_completion#>>'{cost,estimated_cost_usd}', '')::numeric,
      actual_cost_usd = nullif(p_completion#>>'{cost,actual_cost_usd}', '')::numeric,
      usage = coalesce(p_completion#>'{cost,usage}', '{}'::jsonb),
      latency_ms = nullif(p_completion#>>'{cost,latency_ms}', '')::integer,
      result_hash = generated_hash,
      failure_code = case when p_target_state = 'failed' then p_completion->>'failure_code' end,
      updated_at = now()
  where id = proposal.id
  returning * into proposal;
  return next proposal;
end;
$$;

create or replace function public.motionly_link_workflow_artifact(
  p_workspace_id uuid,
  p_project_id uuid,
  p_workflow_run_id uuid,
  p_artifact_type text,
  p_artifact_id uuid,
  p_artifact_revision integer default null
)
returns setof public.workflow_run_artifacts
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  run public.workflow_runs;
  existing public.workflow_run_artifacts;
  linked public.workflow_run_artifacts;
  proposal_run_id uuid;
  related_proposal_id uuid;
  related_render_id uuid;
  artifact_upload_id uuid;
  seq integer;
  payload jsonb;
begin
  select * into run from public.workflow_runs
  where id = p_workflow_run_id
    and workspace_id = p_workspace_id
    and project_id = p_project_id
  for update;
  if not found then
    raise exception 'WORKFLOW_NOT_FOUND' using errcode = 'P0002';
  end if;

  select * into existing from public.workflow_run_artifacts
  where workspace_id = p_workspace_id
    and project_id = p_project_id
    and workflow_run_id = p_workflow_run_id
    and artifact_type = p_artifact_type;
  if found then
    if p_artifact_id is distinct from coalesce(
      existing.transcript_id, existing.visual_manifest_id, existing.proposal_id,
      existing.decision_id, existing.timeline_revision_id,
      existing.render_job_id, existing.export_id
    ) or existing.artifact_revision is distinct from p_artifact_revision then
      raise exception 'WORKFLOW_ARTIFACT_CONFLICT' using errcode = 'P0001';
    end if;
    return next existing;
    return;
  end if;

  if run.state in ('export_ready', 'failed', 'cancelled', 'superseded') then
    raise exception 'WORKFLOW_TERMINAL' using errcode = 'P0001';
  end if;

  if p_artifact_type = 'transcript' then
    select uploaded_video_id into artifact_upload_id from public.transcripts
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or artifact_upload_id <> run.uploaded_video_id then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      transcript_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'visual_manifest' then
    select uploaded_video_id into artifact_upload_id from public.video_manifests
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or artifact_upload_id <> run.uploaded_video_id then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      visual_manifest_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'proposal' then
    select workflow_run_id into proposal_run_id from public.ai_edit_proposals
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id
    for update;
    if not found or proposal_run_id is not null and proposal_run_id <> p_workflow_run_id then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    update public.ai_edit_proposals set workflow_run_id = p_workflow_run_id
    where id = p_artifact_id;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      proposal_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'decision' then
    select proposal_id into related_proposal_id from public.ai_edit_proposal_decisions
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or not exists (
      select 1 from public.ai_edit_proposals
      where id = related_proposal_id and workflow_run_id = p_workflow_run_id
        and workspace_id = p_workspace_id and project_id = p_project_id
    ) then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      decision_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'timeline_revision' then
    select proposal_id into related_proposal_id from public.timeline_revisions
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or not exists (
      select 1 from public.ai_edit_proposals
      where id = related_proposal_id and workflow_run_id = p_workflow_run_id
        and workspace_id = p_workspace_id and project_id = p_project_id
    ) then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      timeline_revision_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'draft_render_job' then
    select workflow_run_id into proposal_run_id from public.render_jobs
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or proposal_run_id is distinct from p_workflow_run_id then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      render_job_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  elsif p_artifact_type = 'draft_export' then
    select render_job_id into related_render_id from public.exports
    where id = p_artifact_id and workspace_id = p_workspace_id and project_id = p_project_id;
    if not found or not exists (
      select 1 from public.render_jobs
      where id = related_render_id and workflow_run_id = p_workflow_run_id
        and workspace_id = p_workspace_id and project_id = p_project_id
    ) then
      raise exception 'WORKFLOW_ARTIFACT_LINEAGE_INVALID' using errcode = 'P0001';
    end if;
    insert into public.workflow_run_artifacts (
      workspace_id, project_id, workflow_run_id, artifact_type,
      export_id, artifact_revision
    ) values (
      p_workspace_id, p_project_id, p_workflow_run_id, p_artifact_type,
      p_artifact_id, p_artifact_revision
    ) returning * into linked;
  else
    raise exception 'WORKFLOW_ARTIFACT_TYPE_INVALID' using errcode = '22023';
  end if;

  update public.workflow_runs set revision = revision + 1 where id = run.id;
  select coalesce(max(sequence_number), 0) + 1 into seq
  from public.workflow_events where workflow_run_id = run.id;
  payload := jsonb_build_object(
    'workflow_run_id', run.id,
    'artifact_type', p_artifact_type,
    'artifact_id', p_artifact_id,
    'artifact_revision', p_artifact_revision
  );
  insert into public.workflow_events (
    workspace_id, project_id, workflow_run_id, sequence_number,
    event_type, event_key, payload
  ) values (
    p_workspace_id, p_project_id, run.id, seq,
    'workflow.artifact.linked',
    'artifact-linked:' || run.id || ':' || p_artifact_type,
    payload
  );
  return next linked;
end;
$$;
