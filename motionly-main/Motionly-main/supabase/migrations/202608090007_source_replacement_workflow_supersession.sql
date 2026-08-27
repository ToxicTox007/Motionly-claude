-- Task 18: source replacement must stop every in-flight workflow pinned to the
-- old immutable media revision. Existing terminal workflow history remains
-- immutable; current media recovery creates a fresh canonical run through the
-- existing first-draft reconciler.

begin;

create or replace function public.motionly_trigger_media_replace_invalidation()
returns trigger
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
  source_replaced boolean;
  changed_run public.workflow_runs;
  event_row public.workflow_events;
  event_payload jsonb;
  next_sequence integer;
begin
  source_replaced :=
    new.storage_path is distinct from old.storage_path
    or new.byte_size is distinct from old.byte_size
    or new.duration_ms is distinct from old.duration_ms
    or new.source_object_reference_id is distinct from old.source_object_reference_id
    or new.verified_size_bytes is distinct from old.verified_size_bytes
    or new.verified_sha256 is distinct from old.verified_sha256;

  if not source_replaced then
    return new;
  end if;

  -- Keep the Task 5 contract: every old derivative is visibly stale.
  perform public.motionly_invalidate_on_media_replace(new.project_id);

  -- Terminalize every active workflow that is pinned to an older source
  -- revision. The existing terminal-sibling trigger fences active worker
  -- attempts before this function cancels all undispatched transport messages.
  for changed_run in
    update public.workflow_runs
    set state = 'superseded',
        current_step_key = null,
        completed_at = clock_timestamp(),
        revision = revision + 1,
        updated_at = clock_timestamp()
    where workspace_id = new.workspace_id
      and project_id = new.project_id
      and uploaded_video_id = new.id
      and source_media_revision <> new.media_revision
      and state not in ('export_ready', 'failed', 'cancelled', 'superseded')
    returning *
  loop
    -- A source replacement makes every not-yet-delivered message obsolete.
    -- A lease that races this update is fenced because ACK requires `leased`.
    update public.transactional_outbox
    set state = 'dead_letter',
        dead_lettered_at = clock_timestamp(),
        last_error_code = 'workflow_source_superseded',
        last_error_message = 'Source media was replaced before this message could run.',
        lease_owner = null,
        lease_token = null,
        lease_expires_at = null
    where workspace_id = changed_run.workspace_id
      and project_id = changed_run.project_id
      and workflow_run_id = changed_run.id
      and state in ('pending', 'failed', 'leased');

    -- The orchestration protocol already treats workflow.cancelled as a
    -- durable cancellation signal. `state=superseded` preserves the exact
    -- canonical reason without adding a new, unrecognized transport event.
    event_payload := jsonb_build_object(
      'workflow_run_id', changed_run.id,
      'state', 'superseded'
    );
    select coalesce(max(sequence_number), 0) + 1 into next_sequence
    from public.workflow_events
    where workspace_id = changed_run.workspace_id
      and project_id = changed_run.project_id
      and workflow_run_id = changed_run.id;

    insert into public.workflow_events (
      workspace_id, project_id, workflow_run_id, sequence_number,
      event_type, event_key, payload
    ) values (
      changed_run.workspace_id,
      changed_run.project_id,
      changed_run.id,
      next_sequence,
      'workflow.source.superseded',
      'workflow-source-replaced:' || changed_run.id || ':' || new.media_revision,
      event_payload
    ) returning * into event_row;

    insert into public.transactional_outbox (
      workspace_id, project_id, workflow_run_id, workflow_event_id,
      destination, event_type, deduplication_key, payload_hash, payload
    ) values (
      changed_run.workspace_id,
      changed_run.project_id,
      changed_run.id,
      event_row.id,
      'temporal',
      'workflow.cancelled',
      'workflow-source-replaced:' || changed_run.id || ':' || new.media_revision,
      encode(extensions.digest(event_payload::text, 'sha256'), 'hex'),
      event_payload
    );
  end loop;

  return new;
end;
$$;

revoke all on function public.motionly_trigger_media_replace_invalidation()
from public, anon, authenticated;

commit;
