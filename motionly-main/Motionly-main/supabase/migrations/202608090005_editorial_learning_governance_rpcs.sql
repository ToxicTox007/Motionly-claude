-- Atomically persist consent policy changes, immutable normalized deltas, frozen
-- datasets, and per-user deployments. The API invokes these only with service_role.

create or replace function public.motionly_set_editorial_learning_policy(
  p_workspace_id uuid,
  p_user_id uuid,
  p_memory_enabled boolean,
  p_training_enabled boolean,
  p_policy_version text,
  p_retention_days integer default null,
  p_expected_revision integer default null
) returns setof public.editorial_learning_policies
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  policy_row public.editorial_learning_policies;
  event_type_value text;
begin
  if p_policy_version is null or length(trim(p_policy_version)) = 0 then
    raise exception 'EDITORIAL_POLICY_VERSION_REQUIRED' using errcode = '22023';
  end if;
  if p_retention_days is not null and p_retention_days not between 1 and 365 then
    raise exception 'EDITORIAL_RETENTION_INVALID' using errcode = '22023';
  end if;

  select * into policy_row
    from public.editorial_learning_policies
    where workspace_id = p_workspace_id and user_id = p_user_id
    for update;

  if not found then
    if p_expected_revision is not null and p_expected_revision <> 0 then
      raise exception 'EDITORIAL_POLICY_STALE' using errcode = '40001';
    end if;
    insert into public.editorial_learning_policies(
      workspace_id, user_id, memory_enabled, training_enabled, retention_days, policy_version, revision, revoked_at
    ) values (
      p_workspace_id, p_user_id, p_memory_enabled, p_training_enabled,
      coalesce(p_retention_days, 90), trim(p_policy_version), 1, null
    ) returning * into policy_row;
  else
    if p_expected_revision is not null and p_expected_revision <> policy_row.revision then
      raise exception 'EDITORIAL_POLICY_STALE' using errcode = '40001';
    end if;
    update public.editorial_learning_policies set
      memory_enabled = p_memory_enabled,
      training_enabled = p_training_enabled,
      retention_days = coalesce(p_retention_days, policy_row.retention_days),
      policy_version = trim(p_policy_version),
      revision = policy_row.revision + 1,
      revoked_at = null,
      updated_at = now()
    where id = policy_row.id
    returning * into policy_row;
  end if;

  event_type_value := case when policy_row.memory_enabled or policy_row.training_enabled
    then 'opt_in' else 'opt_out' end;
  insert into public.editorial_learning_policy_events(
    workspace_id, user_id, policy_id, revision, event_type, reason
  ) values (
    p_workspace_id, p_user_id, policy_row.id, policy_row.revision, event_type_value, 'user_request'
  );
  return query select * from public.editorial_learning_policies where id = policy_row.id;
end; $$;

create or replace function public.motionly_capture_editorial_feedback_delta(
  p_workspace_id uuid,
  p_project_id uuid,
  p_user_id uuid,
  p_source_event_id uuid,
  p_layer text,
  p_delta jsonb,
  p_normalizer_version text,
  p_content_hash text,
  p_memory_eligible boolean,
  p_training_eligible boolean
) returns setof public.editorial_feedback_deltas
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  policy_row public.editorial_learning_policies;
  delta_row public.editorial_feedback_deltas;
begin
  select * into policy_row
    from public.editorial_learning_policies
    where workspace_id = p_workspace_id and user_id = p_user_id
    for key share;
  if not found or policy_row.revoked_at is not null
    or not (policy_row.memory_enabled or policy_row.training_enabled) then
    return;
  end if;
  if p_layer is null or length(trim(p_layer)) = 0
    or p_normalizer_version is null or length(trim(p_normalizer_version)) = 0 then
    raise exception 'EDITORIAL_DELTA_INVALID' using errcode = '22023';
  end if;

  insert into public.editorial_feedback_deltas(
    workspace_id, project_id, user_id, source_event_id, policy_id, policy_revision,
    layer, normalizer_version, delta, content_hash, memory_eligible, training_eligible
  ) values (
    p_workspace_id, p_project_id, p_user_id, p_source_event_id, policy_row.id, policy_row.revision,
    trim(p_layer), trim(p_normalizer_version), p_delta, p_content_hash,
    p_memory_eligible and policy_row.memory_enabled,
    p_training_eligible and policy_row.training_enabled
  ) on conflict (source_event_id) do update set source_event_id = excluded.source_event_id
  returning * into delta_row;

  return query select * from public.editorial_feedback_deltas where id = delta_row.id;
end; $$;

create or replace function public.motionly_freeze_editorial_training_dataset(
  p_workspace_id uuid,
  p_project_id uuid,
  p_user_id uuid,
  p_minimum_examples integer
) returns setof public.editorial_training_datasets
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  policy_row public.editorial_learning_policies;
  dataset_row public.editorial_training_datasets;
  item_count integer;
  manifest_value text;
begin
  if p_minimum_examples < 1 then
    raise exception 'TRAINING_MINIMUM_INVALID' using errcode = '22023';
  end if;
  select * into policy_row
    from public.editorial_learning_policies
    where workspace_id = p_workspace_id and user_id = p_user_id
    for update;
  if not found or policy_row.revoked_at is not null or not policy_row.training_enabled then
    raise exception 'TRAINING_CONSENT_REQUIRED' using errcode = '42501';
  end if;

  select count(*), encode(extensions.digest(coalesce(string_agg(id::text, ':' order by id::text), ''), 'sha256'), 'hex')
    into item_count, manifest_value
    from public.editorial_feedback_deltas
    where workspace_id = p_workspace_id
      and project_id = p_project_id
      and user_id = p_user_id
      and training_eligible
      and revoked_at is null;
  if item_count < p_minimum_examples then
    raise exception 'TRAINING_EXAMPLES_INSUFFICIENT' using errcode = 'P0001';
  end if;

  insert into public.editorial_training_datasets(
    workspace_id, project_id, user_id, status, manifest_hash
  ) values (
    p_workspace_id, p_project_id, p_user_id, 'frozen', manifest_value
  ) returning * into dataset_row;

  insert into public.editorial_training_dataset_items(
    dataset_id, delta_id, policy_revision, split, item_hash
  )
  select
    dataset_row.id,
    delta.id,
    delta.policy_revision,
    'train',
    encode(extensions.digest(dataset_row.id::text || ':' || delta.id::text || ':' || delta.policy_revision::text, 'sha256'), 'hex')
  from public.editorial_feedback_deltas delta
  where delta.workspace_id = p_workspace_id
    and delta.project_id = p_project_id
    and delta.user_id = p_user_id
    and delta.training_eligible
    and delta.revoked_at is null;

  return query select * from public.editorial_training_datasets where id = dataset_row.id;
end; $$;

create or replace function public.motionly_promote_editorial_learning_dataset(
  p_workspace_id uuid,
  p_project_id uuid,
  p_user_id uuid,
  p_dataset_id uuid,
  p_expected_revision integer
) returns setof public.editorial_learning_deployments
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  policy_row public.editorial_learning_policies;
  deployment_row public.editorial_learning_deployments;
begin
  select * into policy_row
    from public.editorial_learning_policies
    where workspace_id = p_workspace_id and user_id = p_user_id
    for key share;
  if not found or policy_row.revoked_at is not null or not policy_row.memory_enabled then
    raise exception 'EDITORIAL_MEMORY_CONSENT_REQUIRED' using errcode = '42501';
  end if;
  perform 1
    from public.editorial_training_datasets
    where id = p_dataset_id
      and workspace_id = p_workspace_id
      and project_id = p_project_id
      and user_id = p_user_id
      and status in ('frozen', 'shadow_passed')
      and revoked_at is null;
  if not found then
    raise exception 'EDITORIAL_DATASET_NOT_FOUND' using errcode = 'P0002';
  end if;

  insert into public.editorial_learning_deployments(workspace_id, project_id, user_id)
    values (p_workspace_id, p_project_id, p_user_id)
    on conflict (workspace_id, project_id, user_id) do nothing;
  select * into deployment_row
    from public.editorial_learning_deployments
    where workspace_id = p_workspace_id and project_id = p_project_id and user_id = p_user_id
    for update;
  if deployment_row.revision <> p_expected_revision then
    raise exception 'EDITORIAL_DEPLOYMENT_STALE' using errcode = '40001';
  end if;

  update public.editorial_learning_deployments set
    previous_dataset_id = deployment_row.active_dataset_id,
    active_dataset_id = p_dataset_id,
    state = 'active',
    traffic_percent = 100,
    revision = deployment_row.revision + 1,
    rolled_back_at = null,
    reason = null,
    updated_at = now()
  where workspace_id = p_workspace_id and project_id = p_project_id and user_id = p_user_id
  returning * into deployment_row;

  return query select * from public.editorial_learning_deployments
    where workspace_id = p_workspace_id and project_id = p_project_id and user_id = p_user_id;
end; $$;

create or replace function public.motionly_rollback_editorial_learning_deployment(
  p_workspace_id uuid,
  p_project_id uuid,
  p_user_id uuid,
  p_reason text default 'admin_rollback'
) returns setof public.editorial_learning_deployments
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  deployment_row public.editorial_learning_deployments;
begin
  insert into public.editorial_learning_deployments(workspace_id, project_id, user_id)
    values (p_workspace_id, p_project_id, p_user_id)
    on conflict (workspace_id, project_id, user_id) do nothing;
  select * into deployment_row
    from public.editorial_learning_deployments
    where workspace_id = p_workspace_id and project_id = p_project_id and user_id = p_user_id
    for update;

  update public.editorial_learning_deployments set
    active_dataset_id = deployment_row.previous_dataset_id,
    previous_dataset_id = null,
    state = case when deployment_row.previous_dataset_id is null then 'disabled' else 'active' end,
    traffic_percent = case when deployment_row.previous_dataset_id is null then 0 else 100 end,
    revision = deployment_row.revision + 1,
    rolled_back_at = now(),
    reason = coalesce(nullif(trim(p_reason), ''), 'admin_rollback'),
    updated_at = now()
  where workspace_id = p_workspace_id and project_id = p_project_id and user_id = p_user_id;

  insert into public.editorial_learning_policy_events(workspace_id, user_id, revision, event_type, reason)
    values (p_workspace_id, p_user_id, deployment_row.revision + 1, 'rollback', coalesce(nullif(trim(p_reason), ''), 'admin_rollback'));
  return query select * from public.editorial_learning_deployments
    where workspace_id = p_workspace_id and project_id = p_project_id and user_id = p_user_id;
end; $$;

revoke all on function public.motionly_set_editorial_learning_policy(uuid, uuid, boolean, boolean, text, integer, integer) from public, anon, authenticated;
revoke all on function public.motionly_capture_editorial_feedback_delta(uuid, uuid, uuid, uuid, text, jsonb, text, text, boolean, boolean) from public, anon, authenticated;
revoke all on function public.motionly_freeze_editorial_training_dataset(uuid, uuid, uuid, integer) from public, anon, authenticated;
revoke all on function public.motionly_promote_editorial_learning_dataset(uuid, uuid, uuid, uuid, integer) from public, anon, authenticated;
revoke all on function public.motionly_rollback_editorial_learning_deployment(uuid, uuid, uuid, text) from public, anon, authenticated;
grant execute on function public.motionly_set_editorial_learning_policy(uuid, uuid, boolean, boolean, text, integer, integer) to service_role;
grant execute on function public.motionly_capture_editorial_feedback_delta(uuid, uuid, uuid, uuid, text, jsonb, text, text, boolean, boolean) to service_role;
grant execute on function public.motionly_freeze_editorial_training_dataset(uuid, uuid, uuid, integer) to service_role;
grant execute on function public.motionly_promote_editorial_learning_dataset(uuid, uuid, uuid, uuid, integer) to service_role;
grant execute on function public.motionly_rollback_editorial_learning_deployment(uuid, uuid, uuid, text) to service_role;
