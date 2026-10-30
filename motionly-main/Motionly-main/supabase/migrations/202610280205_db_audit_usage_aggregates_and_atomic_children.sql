-- Database audit DB-07 / DB-10 / DB-20: usage is summed in the database, and
-- child rows are replaced atomically.
--
-- DB-07  Feature quotas summed cost_attribution_events.quantity in Python over
--        at most 10,000 rows (silently low beyond that), and the render
--        allowance summed usage_ledger rows without a limit. Both now read one
--        exact SUM from these functions; the API fails closed when they fail.
--        The admin cost summary likewise read at most 10,000 rows; it is now
--        one aggregate.
-- DB-10  Saving a transcript or caption track was three PostgREST calls:
--        update the parent, delete every child row, insert the new children.
--        A failure after the delete left a transcript with no words or a track
--        with no segments, and two concurrent saves could interleave. The
--        delete and insert now run in one transaction, under a row lock on the
--        parent, scoped to the tenant.
--
-- The functions are service-role only, plus what media workers call (below).
-- Forward-only. Covered by
-- supabase/tests/db_audit_usage_aggregates_and_atomic_children_test.sql.

begin;

create or replace function public.motionly_sum_cost_usage(
  p_workspace_id uuid,
  p_category text,
  p_period_start timestamptz,
  p_period_end timestamptz,
  p_provider text default null
)
returns numeric
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select coalesce(sum(event.quantity), 0)
  from public.cost_attribution_events event
  where event.workspace_id = p_workspace_id
    and event.category = p_category
    and event.created_at >= p_period_start
    and event.created_at < p_period_end
    and (p_provider is null or event.provider = p_provider);
$$;

create or replace function public.motionly_sum_usage_ledger(
  p_workspace_id uuid,
  p_event_type text,
  p_period_start timestamptz,
  p_period_end timestamptz
)
returns bigint
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select coalesce(sum(entry.quantity), 0)::bigint
  from public.usage_ledger entry
  where entry.workspace_id = p_workspace_id
    and entry.event_type = p_event_type
    and entry.created_at >= p_period_start
    and entry.created_at < p_period_end;
$$;

create or replace function public.motionly_cost_summary(
  p_render_job_id uuid default null
)
returns jsonb
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  with costs as (
    select event.category, event.provider, event.export_id, event.approved_at,
           event.quantity * event.unit_cost_usd as cost
    from public.cost_attribution_events event
    where p_render_job_id is null or event.render_job_id = p_render_job_id
  )
  select jsonb_build_object(
    'total_usd', coalesce((select sum(cost) from costs), 0),
    'by_category', coalesce(
      (select jsonb_object_agg(category, total)
       from (select category, sum(cost) as total from costs group by category) grouped),
      '{}'::jsonb),
    'by_provider', coalesce(
      (select jsonb_object_agg(provider, total)
       from (select provider, sum(cost) as total from costs group by provider) grouped),
      '{}'::jsonb),
    'approved_exports', (
      select count(distinct export_id)
      from costs
      where approved_at is not null and export_id is not null)
  );
$$;

create or replace function public.motionly_replace_transcript_words(
  p_workspace_id uuid,
  p_project_id uuid,
  p_transcript_id uuid,
  p_words jsonb
)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_count integer;
begin
  if p_words is null or jsonb_typeof(p_words) <> 'array' then
    raise exception 'TRANSCRIPT_WORDS_INVALID' using errcode = '22023';
  end if;
  perform 1
  from public.transcripts transcript
  where transcript.id = p_transcript_id
    and transcript.workspace_id = p_workspace_id
    and transcript.project_id = p_project_id
  for update;
  if not found then
    raise exception 'TRANSCRIPT_NOT_FOUND' using errcode = 'P0002';
  end if;

  delete from public.transcript_words where transcript_id = p_transcript_id;
  insert into public.transcript_words (
    workspace_id, project_id, transcript_id, word_index, text, start_ms, end_ms, confidence
  )
  select p_workspace_id, p_project_id, p_transcript_id,
         word.word_index, word.text, word.start_ms, word.end_ms, word.confidence
  from jsonb_to_recordset(p_words) as word(
    word_index integer, text text, start_ms integer, end_ms integer, confidence numeric
  );
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.motionly_replace_caption_segments(
  p_workspace_id uuid,
  p_project_id uuid,
  p_caption_track_id uuid,
  p_segments jsonb
)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_count integer;
begin
  if p_segments is null or jsonb_typeof(p_segments) <> 'array' then
    raise exception 'CAPTION_SEGMENTS_INVALID' using errcode = '22023';
  end if;
  perform 1
  from public.caption_tracks track
  where track.id = p_caption_track_id
    and track.workspace_id = p_workspace_id
    and track.project_id = p_project_id
  for update;
  if not found then
    raise exception 'CAPTION_TRACK_NOT_FOUND' using errcode = 'P0002';
  end if;

  delete from public.caption_segments where caption_track_id = p_caption_track_id;
  insert into public.caption_segments (
    workspace_id, project_id, caption_track_id, segment_index, start_ms, end_ms,
    text, words, line_count, highlight_words
  )
  select p_workspace_id, p_project_id, p_caption_track_id,
         segment.segment_index, segment.start_ms, segment.end_ms, segment.text,
         coalesce(segment.words, '[]'::jsonb),
         coalesce(segment.line_count, 1),
         coalesce(segment.highlight_words, '[]'::jsonb)
  from jsonb_to_recordset(p_segments) as segment(
    segment_index integer, start_ms integer, end_ms integer, text text,
    words jsonb, line_count integer, highlight_words jsonb
  );
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.motionly_sum_cost_usage(uuid, text, timestamptz, timestamptz, text)
  from public, anon, authenticated;
revoke all on function public.motionly_sum_usage_ledger(uuid, text, timestamptz, timestamptz)
  from public, anon, authenticated;
revoke all on function public.motionly_cost_summary(uuid) from public, anon, authenticated;
revoke all on function public.motionly_replace_transcript_words(uuid, uuid, uuid, jsonb)
  from public, anon, authenticated;
revoke all on function public.motionly_replace_caption_segments(uuid, uuid, uuid, jsonb)
  from public, anon, authenticated;

grant execute on function public.motionly_sum_cost_usage(uuid, text, timestamptz, timestamptz, text)
  to service_role;
grant execute on function public.motionly_sum_usage_ledger(uuid, text, timestamptz, timestamptz)
  to service_role;
grant execute on function public.motionly_cost_summary(uuid) to service_role;
grant execute on function public.motionly_replace_transcript_words(uuid, uuid, uuid, jsonb)
  to service_role;
grant execute on function public.motionly_replace_caption_segments(uuid, uuid, uuid, jsonb)
  to service_role;

-- Media workers run the same repositories with the restricted motionly_worker
-- credential: transcription saves words, and the model gateway's quota guard
-- and the render allowance read usage. They held SELECT/INSERT/DELETE on the
-- underlying tables already; these grants keep exactly that reach.
grant execute on function public.motionly_sum_cost_usage(uuid, text, timestamptz, timestamptz, text) to motionly_worker;
grant execute on function public.motionly_sum_usage_ledger(uuid, text, timestamptz, timestamptz) to motionly_worker;
grant execute on function public.motionly_replace_transcript_words(uuid, uuid, uuid, jsonb) to motionly_worker;

commit;
