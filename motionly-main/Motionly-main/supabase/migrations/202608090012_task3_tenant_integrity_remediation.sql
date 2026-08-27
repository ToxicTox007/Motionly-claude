-- Historical Task 3 tenant-integrity maintenance. This migration deliberately does
-- not alter legacy domain rows and does not validate any deferred constraint during
-- application. Operators must run the service-role audit, resolve findings, and
-- explicitly invoke the guarded validation function in a scheduled window.

create table if not exists public.tenant_integrity_constraint_manifest (
  constraint_name text primary key,
  table_name text not null,
  constraint_kind text not null check (constraint_kind in ('foreign_key', 'check')),
  validation_order integer not null unique check (validation_order > 0)
);

insert into public.tenant_integrity_constraint_manifest (
  constraint_name, table_name, constraint_kind, validation_order
) values
  ('task3_uploads_project_tenant_fk', 'uploaded_videos', 'foreign_key', 10),
  ('task3_projects_active_video_tenant_fk', 'projects', 'foreign_key', 20),
  ('task3_upload_events_video_tenant_fk', 'upload_events', 'foreign_key', 30),
  ('task3_transcripts_video_tenant_fk', 'transcripts', 'foreign_key', 40),
  ('task3_transcript_words_transcript_tenant_fk', 'transcript_words', 'foreign_key', 50),
  ('task3_caption_tracks_transcript_tenant_fk', 'caption_tracks', 'foreign_key', 60),
  ('task3_caption_segments_track_tenant_fk', 'caption_segments', 'foreign_key', 70),
  ('task3_edit_plans_transcript_tenant_fk', 'edit_plans', 'foreign_key', 80),
  ('task3_plan_snapshots_plan_tenant_fk', 'edit_plan_snapshots', 'foreign_key', 90),
  ('task3_plan_snapshots_transcript_tenant_fk', 'edit_plan_snapshots', 'foreign_key', 100),
  ('task3_render_jobs_video_tenant_fk', 'render_jobs', 'foreign_key', 110),
  ('task3_render_jobs_caption_track_tenant_fk', 'render_jobs', 'foreign_key', 120),
  ('task3_render_events_job_tenant_fk', 'render_job_events', 'foreign_key', 130),
  ('task3_exports_render_job_tenant_fk', 'exports', 'foreign_key', 140),
  ('task3_exports_restored_from_tenant_fk', 'exports', 'foreign_key', 150),
  ('task3_stock_assets_project_tenant_fk', 'stock_assets', 'foreign_key', 160),
  ('task3_broll_slots_plan_tenant_fk', 'broll_slots', 'foreign_key', 170),
  ('task3_broll_slots_asset_tenant_fk', 'broll_slots', 'foreign_key', 180),
  ('task3_music_tracks_project_tenant_fk', 'music_tracks', 'foreign_key', 190),
  ('task3_music_selections_plan_tenant_fk', 'project_music_selections', 'foreign_key', 200),
  ('task3_music_selections_track_tenant_fk', 'project_music_selections', 'foreign_key', 210),
  ('task3_visual_presets_project_tenant_fk', 'visual_presets', 'foreign_key', 220),
  ('task3_visual_presets_plan_tenant_fk', 'visual_presets', 'foreign_key', 230),
  ('task3_control_settings_project_tenant_fk', 'project_control_settings', 'foreign_key', 240),
  ('task3_artifact_versions_project_tenant_fk', 'artifact_versions', 'foreign_key', 250),
  ('task3_video_manifests_upload_tenant_fk', 'video_manifests', 'foreign_key', 260),
  ('task3_segmentation_mattes_upload_tenant_fk', 'segmentation_mattes', 'foreign_key', 270),
  ('task3_phase5_resources_project_tenant_fk', 'phase5_resources', 'foreign_key', 280),
  ('task3_publish_jobs_export_tenant_fk', 'publish_jobs', 'foreign_key', 290),
  ('task3_publish_jobs_connection_tenant_fk', 'publish_jobs', 'foreign_key', 300),
  ('task3_ai_feedback_project_tenant_fk', 'ai_edit_feedback_events', 'foreign_key', 310),
  ('task3_ai_feedback_plan_tenant_fk', 'ai_edit_feedback_events', 'foreign_key', 320),
  ('task3_provider_slo_project_requires_workspace', 'provider_slo_events', 'check', 330),
  ('task3_provider_slo_project_tenant_fk', 'provider_slo_events', 'foreign_key', 340),
  ('task3_cost_events_render_requires_project', 'cost_attribution_events', 'check', 350),
  ('task3_cost_events_export_requires_project', 'cost_attribution_events', 'check', 360),
  ('task3_cost_events_project_tenant_fk', 'cost_attribution_events', 'foreign_key', 370),
  ('task3_cost_events_render_tenant_fk', 'cost_attribution_events', 'foreign_key', 380),
  ('task3_cost_events_export_tenant_fk', 'cost_attribution_events', 'foreign_key', 390)
on conflict (constraint_name) do nothing;

create table if not exists public.tenant_integrity_audit_runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  finding_count integer not null default 0 check (finding_count >= 0),
  open_finding_count integer not null default 0 check (open_finding_count >= 0)
);

create table if not exists public.tenant_integrity_findings (
  id uuid primary key default gen_random_uuid(),
  audit_run_id uuid not null references public.tenant_integrity_audit_runs(id) on delete restrict,
  constraint_name text not null references public.tenant_integrity_constraint_manifest(constraint_name) on delete restrict,
  child_table text not null,
  child_id text not null,
  workspace_id uuid,
  project_id uuid,
  reference_key jsonb not null default '{}'::jsonb check (jsonb_typeof(reference_key) = 'object'),
  state text not null default 'open' check (state in ('open', 'resolved')),
  first_detected_at timestamptz not null default now(),
  last_detected_at timestamptz not null default now(),
  resolved_at timestamptz,
  resolution_note text,
  unique (constraint_name, child_table, child_id)
);

create table if not exists public.tenant_integrity_remediation_events (
  id uuid primary key default gen_random_uuid(),
  finding_id uuid not null references public.tenant_integrity_findings(id) on delete restrict,
  action text not null check (action in ('repair_planned', 'quarantine_planned', 'manual_repair_complete')),
  operator_note text not null check (length(trim(operator_note)) between 1 and 500),
  created_at timestamptz not null default now()
);

create index if not exists tenant_integrity_findings_open_idx
  on public.tenant_integrity_findings (constraint_name, last_detected_at desc)
  where state = 'open';

alter table public.tenant_integrity_constraint_manifest enable row level security;
alter table public.tenant_integrity_audit_runs enable row level security;
alter table public.tenant_integrity_findings enable row level security;
alter table public.tenant_integrity_remediation_events enable row level security;
revoke all on public.tenant_integrity_constraint_manifest, public.tenant_integrity_audit_runs,
  public.tenant_integrity_findings, public.tenant_integrity_remediation_events
  from public, anon, authenticated;
grant all on public.tenant_integrity_constraint_manifest, public.tenant_integrity_audit_runs,
  public.tenant_integrity_findings, public.tenant_integrity_remediation_events to service_role;

create or replace function public.motionly_audit_task3_tenant_integrity()
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  audit_id uuid := gen_random_uuid();
  target record;
  child_not_null text;
  parent_join text;
  reference_pairs text;
  child_relation text;
  parent_relation text;
begin
  insert into public.tenant_integrity_audit_runs (id) values (audit_id);

  -- A finding is only resolved by a complete later scan that no longer finds its
  -- row. No domain row is updated, deleted, or reassigned by this audit.
  update public.tenant_integrity_findings finding
  set state = 'resolved', resolved_at = now(), resolution_note = 'verified_clean_after_rescan'
  where finding.state = 'open'
    and finding.constraint_name in (
      select constraint_name from public.tenant_integrity_constraint_manifest
    );

  for target in
    select manifest.constraint_name, manifest.table_name, catalog_constraint.conrelid, catalog_constraint.confrelid,
           catalog_constraint.conkey, catalog_constraint.confkey
    from public.tenant_integrity_constraint_manifest manifest
    join pg_constraint catalog_constraint
      on catalog_constraint.conname = manifest.constraint_name
     and catalog_constraint.conrelid = format('public.%I', manifest.table_name)::regclass
    where manifest.constraint_kind = 'foreign_key'
      and catalog_constraint.contype = 'f'
    order by manifest.validation_order
  loop
    if not exists (
      select 1
      from pg_attribute attribute
      where attribute.attrelid = target.conrelid
        and attribute.attname = 'id'
        and attribute.attnum > 0
        and not attribute.attisdropped
    ) then
      raise exception 'TASK3_AUDIT_CHILD_ID_REQUIRED: %', target.table_name using errcode = 'P0001';
    end if;

    select string_agg(format('child.%I is not null', child_attribute.attname), ' and ' order by key.ordinality),
           string_agg(
             format('parent.%I = child.%I', parent_attribute.attname, child_attribute.attname),
             ' and ' order by key.ordinality
           ),
           string_agg(
             format('%L, child.%I::text', child_attribute.attname, child_attribute.attname),
             ', ' order by key.ordinality
           )
      into child_not_null, parent_join, reference_pairs
    from unnest(target.conkey) with ordinality as key(attnum, ordinality)
    join pg_attribute child_attribute
      on child_attribute.attrelid = target.conrelid
     and child_attribute.attnum = key.attnum
    join pg_attribute parent_attribute
      on parent_attribute.attrelid = target.confrelid
     and parent_attribute.attnum = target.confkey[key.ordinality];

    child_relation := format('public.%I', target.table_name);
    select format('%I.%I', namespace.nspname, relation.relname)
      into parent_relation
      from pg_class relation
      join pg_namespace namespace on namespace.oid = relation.relnamespace
      where relation.oid = target.confrelid;

    execute format(
      $audit$
        insert into public.tenant_integrity_findings(
          audit_run_id, constraint_name, child_table, child_id, workspace_id, project_id,
          reference_key, state, first_detected_at, last_detected_at, resolved_at, resolution_note
        )
        select
          %L::uuid, %L, %L, child.id::text,
          (to_jsonb(child)->>'workspace_id')::uuid,
          (to_jsonb(child)->>'project_id')::uuid,
          jsonb_build_object(%s), 'open', now(), now(), null, null
        from %s child
        where %s
          and not exists (select 1 from %s parent where %s)
        on conflict (constraint_name, child_table, child_id) do update set
          audit_run_id = excluded.audit_run_id,
          workspace_id = excluded.workspace_id,
          project_id = excluded.project_id,
          reference_key = excluded.reference_key,
          state = 'open',
          last_detected_at = excluded.last_detected_at,
          resolved_at = null,
          resolution_note = null
      $audit$,
      audit_id, target.constraint_name, target.table_name, reference_pairs,
      child_relation, child_not_null, parent_relation, parent_join
    );
  end loop;

  -- NOT VALID CHECK constraints are audited with their original predicate. These
  -- records are metadata-only and do not quarantine or delete customer data.
  insert into public.tenant_integrity_findings(
    audit_run_id, constraint_name, child_table, child_id, workspace_id, project_id,
    reference_key, state, first_detected_at, last_detected_at, resolved_at, resolution_note
  )
  select audit_id, 'task3_provider_slo_project_requires_workspace', 'provider_slo_events', event.id::text,
         event.workspace_id, event.project_id,
         jsonb_build_object('project_id', event.project_id::text), 'open', now(), now(), null, null
  from public.provider_slo_events event
  where event.project_id is not null and event.workspace_id is null
  on conflict (constraint_name, child_table, child_id) do update set
    audit_run_id = excluded.audit_run_id, workspace_id = excluded.workspace_id,
    project_id = excluded.project_id, reference_key = excluded.reference_key, state = 'open',
    last_detected_at = excluded.last_detected_at, resolved_at = null, resolution_note = null;

  insert into public.tenant_integrity_findings(
    audit_run_id, constraint_name, child_table, child_id, workspace_id, project_id,
    reference_key, state, first_detected_at, last_detected_at, resolved_at, resolution_note
  )
  select audit_id, 'task3_cost_events_render_requires_project', 'cost_attribution_events', event.id::text,
         event.workspace_id, event.project_id,
         jsonb_build_object('render_job_id', event.render_job_id::text), 'open', now(), now(), null, null
  from public.cost_attribution_events event
  where event.render_job_id is not null and event.project_id is null
  on conflict (constraint_name, child_table, child_id) do update set
    audit_run_id = excluded.audit_run_id, workspace_id = excluded.workspace_id,
    project_id = excluded.project_id, reference_key = excluded.reference_key, state = 'open',
    last_detected_at = excluded.last_detected_at, resolved_at = null, resolution_note = null;

  insert into public.tenant_integrity_findings(
    audit_run_id, constraint_name, child_table, child_id, workspace_id, project_id,
    reference_key, state, first_detected_at, last_detected_at, resolved_at, resolution_note
  )
  select audit_id, 'task3_cost_events_export_requires_project', 'cost_attribution_events', event.id::text,
         event.workspace_id, event.project_id,
         jsonb_build_object('export_id', event.export_id::text), 'open', now(), now(), null, null
  from public.cost_attribution_events event
  where event.export_id is not null and event.project_id is null
  on conflict (constraint_name, child_table, child_id) do update set
    audit_run_id = excluded.audit_run_id, workspace_id = excluded.workspace_id,
    project_id = excluded.project_id, reference_key = excluded.reference_key, state = 'open',
    last_detected_at = excluded.last_detected_at, resolved_at = null, resolution_note = null;

  update public.tenant_integrity_audit_runs run
  set completed_at = now(),
      finding_count = (
        select count(*) from public.tenant_integrity_findings finding where finding.audit_run_id = audit_id
      ),
      open_finding_count = (
        select count(*) from public.tenant_integrity_findings finding where finding.state = 'open'
      )
  where run.id = audit_id;
  return audit_id;
end;
$$;

create or replace function public.motionly_record_tenant_integrity_remediation(
  p_finding_id uuid,
  p_action text,
  p_operator_note text
) returns public.tenant_integrity_remediation_events
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare event_row public.tenant_integrity_remediation_events;
begin
  if p_action not in ('repair_planned', 'quarantine_planned', 'manual_repair_complete') then
    raise exception 'TASK3_REMEDIATION_ACTION_INVALID' using errcode = '22023';
  end if;
  if p_operator_note is null or length(trim(p_operator_note)) not between 1 and 500 then
    raise exception 'TASK3_REMEDIATION_NOTE_REQUIRED' using errcode = '22023';
  end if;
  perform 1 from public.tenant_integrity_findings where id = p_finding_id and state = 'open' for key share;
  if not found then
    raise exception 'TASK3_FINDING_NOT_OPEN' using errcode = 'P0002';
  end if;
  insert into public.tenant_integrity_remediation_events(finding_id, action, operator_note)
    values (p_finding_id, p_action, trim(p_operator_note))
    returning * into event_row;
  return event_row;
end;
$$;

create or replace function public.motionly_assert_task3_tenant_integrity_clean(p_audit_run_id uuid)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare open_count integer;
begin
  select run.open_finding_count into open_count
  from public.tenant_integrity_audit_runs run
  where run.id = p_audit_run_id and run.completed_at is not null;
  if not found then
    raise exception 'TASK3_AUDIT_RUN_NOT_COMPLETE' using errcode = 'P0002';
  end if;
  if open_count <> 0 then
    raise exception 'TASK3_TENANT_INTEGRITY_FINDINGS_OPEN: %', open_count using errcode = 'P0001';
  end if;
end;
$$;

create or replace function public.motionly_validate_task3_tenant_integrity(p_acknowledgement text)
returns table(audit_run_id uuid, constraint_name text)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  fresh_audit_id uuid;
  target record;
begin
  if p_acknowledgement <> 'validate-task3-tenant-integrity' then
    raise exception 'TASK3_VALIDATION_ACK_REQUIRED' using errcode = '22023';
  end if;
  if not pg_try_advisory_xact_lock(hashtext('motionly.task3_tenant_integrity_validation')) then
    raise exception 'TASK3_VALIDATION_ALREADY_RUNNING' using errcode = '55P03';
  end if;

  fresh_audit_id := public.motionly_audit_task3_tenant_integrity();
  perform public.motionly_assert_task3_tenant_integrity_clean(fresh_audit_id);

  for target in
    select manifest.constraint_name, manifest.table_name
    from public.tenant_integrity_constraint_manifest manifest
    order by manifest.validation_order
  loop
    execute format('alter table public.%I validate constraint %I', target.table_name, target.constraint_name);
    audit_run_id := fresh_audit_id;
    constraint_name := target.constraint_name;
    return next;
  end loop;
end;
$$;

revoke all on function public.motionly_audit_task3_tenant_integrity() from public, anon, authenticated;
revoke all on function public.motionly_record_tenant_integrity_remediation(uuid, text, text) from public, anon, authenticated;
revoke all on function public.motionly_assert_task3_tenant_integrity_clean(uuid) from public, anon, authenticated;
revoke all on function public.motionly_validate_task3_tenant_integrity(text) from public, anon, authenticated;
grant execute on function public.motionly_audit_task3_tenant_integrity() to service_role;
grant execute on function public.motionly_record_tenant_integrity_remediation(uuid, text, text) to service_role;
grant execute on function public.motionly_assert_task3_tenant_integrity_clean(uuid) to service_role;
grant execute on function public.motionly_validate_task3_tenant_integrity(text) to service_role;
