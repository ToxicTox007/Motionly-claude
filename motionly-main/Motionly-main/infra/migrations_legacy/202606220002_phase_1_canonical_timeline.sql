-- Phase 1: migrate every legacy edit-plan version to a versioned canonical timeline.
-- The timeline remains inside the versioned plan JSON so snapshot/restore stays atomic.

create or replace function public.motionly_legacy_plan_timeline(plan_document jsonb)
returns jsonb
language sql
immutable
as $$
  with source_cuts as (
    select
      ordinal - 1 as clip_index,
      (item->>'start_ms')::integer as source_start_ms,
      (item->>'end_ms')::integer as source_end_ms,
      coalesce(nullif(item->>'reason', ''), 'legacy timeline clip') as reason
    from jsonb_array_elements(coalesce(plan_document->'cuts', '[]'::jsonb))
      with ordinality as cuts(item, ordinal)
  ), mapped as (
    select
      *,
      coalesce(
        sum(source_end_ms - source_start_ms) over (
          order by clip_index rows between unbounded preceding and 1 preceding
        ),
        0
      )::integer as output_start_ms
    from source_cuts
    where source_end_ms > source_start_ms
  )
  select jsonb_build_object(
    'schema_version', 1,
    'duration_ms', greatest(
      1,
      coalesce(sum(source_end_ms - source_start_ms), 0)::integer
    ),
    'clips', coalesce(
      jsonb_agg(
        jsonb_build_object(
          'id', 'cut-' || clip_index,
          'source_video_id', null,
          'source_start_ms', source_start_ms,
          'source_end_ms', source_end_ms,
          'output_start_ms', output_start_ms,
          'output_end_ms', output_start_ms + source_end_ms - source_start_ms,
          'reason', reason
        )
        order by clip_index
      ),
      '[]'::jsonb
    ),
    'captions', '[]'::jsonb,
    'broll', '[]'::jsonb,
    'audio', jsonb_build_object('source_stems', null, 'music', null, 'sfx', '[]'::jsonb),
    'manual_layers', coalesce(
      (
        select jsonb_agg(element)
        from jsonb_array_elements(coalesce(plan_document->'manual_elements', '[]'::jsonb)) element
        where element->>'type' <> 'transition'
      ),
      '[]'::jsonb
    ),
    'transitions', coalesce(
      (
        select jsonb_agg(element)
        from jsonb_array_elements(coalesce(plan_document->'manual_elements', '[]'::jsonb)) element
        where element->>'type' = 'transition'
      ),
      '[]'::jsonb
    ),
    'effects', jsonb_build_object(
      'color_grade', coalesce(plan_document->>'color_grade', 'clean_founder'),
      'effects', coalesce(plan_document->'effects', '[]'::jsonb),
      'zoom_moments', coalesce(plan_document->'zoom_moments', '[]'::jsonb),
      'keyframes', '[]'::jsonb,
      'config', null
    ),
    'metadata', jsonb_build_object(
      'authority', 'project_timeline',
      'migrated_from_legacy_edit_plan', true
    )
  )
  from mapped;
$$;

update public.edit_plans
set plan = jsonb_set(
  plan,
  '{timeline}',
  public.motionly_legacy_plan_timeline(plan),
  true
)
where not (plan ? 'timeline');

update public.edit_plan_snapshots
set plan = jsonb_set(
  plan,
  '{timeline}',
  public.motionly_legacy_plan_timeline(plan),
  true
)
where not (plan ? 'timeline');

comment on function public.motionly_legacy_plan_timeline(jsonb) is
  'Migrates legacy edit-plan JSON into canonical timeline schema version 1.';
