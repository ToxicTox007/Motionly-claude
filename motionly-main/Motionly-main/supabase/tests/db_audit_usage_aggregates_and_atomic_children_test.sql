-- Database audit DB-07 / DB-10 (migration 202610280205): exact usage sums and
-- atomic, tenant-scoped replacement of transcript words and caption segments.

begin;
select plan(14);

insert into auth.users (id, email, raw_user_meta_data)
values ('0d000000-0000-4000-8000-000000000001', 'dbaudit-usage@test.local', '{}');
insert into public.workspaces (id, name, owner_user_id) values
  ('0d000000-0000-4000-8000-0000000000a1', 'Usage', '0d000000-0000-4000-8000-000000000001'),
  ('0d000000-0000-4000-8000-0000000000a2', 'Other', '0d000000-0000-4000-8000-000000000001');
insert into public.projects (id, workspace_id, created_by, title) values
  ('0d000000-0000-4000-8000-0000000000b1', '0d000000-0000-4000-8000-0000000000a1',
   '0d000000-0000-4000-8000-000000000001', 'Usage');

-- 12,000 cost rows: more than the old 10,000-row read could see.
insert into public.cost_attribution_events (
  workspace_id, provider, category, quantity, unit, unit_cost_usd, source_id, created_at)
select '0d000000-0000-4000-8000-0000000000a1',
       case when n % 2 = 0 then 'provider-a' else 'provider-b' end,
       'tokens', 1, 'tokens', 0.001, 'usage-' || n, now() - interval '1 hour'
from generate_series(1, 12000) n;
insert into public.cost_attribution_events (
  workspace_id, provider, category, quantity, unit, unit_cost_usd, source_id, created_at)
values ('0d000000-0000-4000-8000-0000000000a1', 'provider-a', 'tokens', 500, 'tokens', 0.001,
        'usage-last-month', now() - interval '40 days');

set local role service_role;
select is(
  public.motionly_sum_cost_usage('0d000000-0000-4000-8000-0000000000a1', 'tokens',
    now() - interval '1 day', now() + interval '1 minute'),
  12000::numeric, 'the cost sum counts every row in the period, beyond 10,000'
);
select is(
  public.motionly_sum_cost_usage('0d000000-0000-4000-8000-0000000000a1', 'tokens',
    now() - interval '1 day', now() + interval '1 minute', 'provider-a'),
  6000::numeric, 'provider scope counts only that provider'
);
select is(
  public.motionly_sum_cost_usage('0d000000-0000-4000-8000-0000000000a2', 'tokens',
    now() - interval '1 day', now() + interval '1 minute'),
  0::numeric, 'another workspace''s usage is not counted'
);
select ok(
  (public.motionly_cost_summary() ->> 'total_usd')::numeric >= 12.5
  and (public.motionly_cost_summary() -> 'by_provider' ->> 'provider-a')::numeric >= 6.5,
  'the cost summary aggregates every row'
);
reset role;

insert into public.usage_ledger (workspace_id, event_type, quantity, source_id) values
  ('0d000000-0000-4000-8000-0000000000a1', 'render_succeeded', 1, gen_random_uuid()),
  ('0d000000-0000-4000-8000-0000000000a1', 'render_succeeded', 1, gen_random_uuid()),
  ('0d000000-0000-4000-8000-0000000000a1', 'premium_action', 1, gen_random_uuid());
set local role service_role;
select is(
  public.motionly_sum_usage_ledger('0d000000-0000-4000-8000-0000000000a1', 'render_succeeded',
    now() - interval '1 day', now() + interval '1 minute'),
  2::bigint, 'the usage ledger sum counts one event type'
);
reset role;

-- Atomic replacement.
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
  mime_type, byte_size, file_size_bytes)
values ('0d000000-0000-4000-8000-0000000000c1', '0d000000-0000-4000-8000-0000000000a1',
  '0d000000-0000-4000-8000-0000000000b1', 'raw-uploads', 'dbaudit/usage.mp4', 'usage.mp4',
  'video/mp4', 10, 10);
insert into public.transcripts (id, workspace_id, project_id, uploaded_video_id, provider, model)
values ('0d000000-0000-4000-8000-0000000000d1', '0d000000-0000-4000-8000-0000000000a1',
  '0d000000-0000-4000-8000-0000000000b1', '0d000000-0000-4000-8000-0000000000c1', 'fixture', 'fixture');
insert into public.transcript_words (workspace_id, project_id, transcript_id, word_index, text, start_ms, end_ms)
values ('0d000000-0000-4000-8000-0000000000a1', '0d000000-0000-4000-8000-0000000000b1',
  '0d000000-0000-4000-8000-0000000000d1', 0, 'old', 0, 100);
insert into public.caption_tracks (id, workspace_id, project_id, transcript_id, preset)
values ('0d000000-0000-4000-8000-0000000000e1', '0d000000-0000-4000-8000-0000000000a1',
  '0d000000-0000-4000-8000-0000000000b1', '0d000000-0000-4000-8000-0000000000d1', 'luxury_minimal');

set local role service_role;
select is(
  public.motionly_replace_transcript_words('0d000000-0000-4000-8000-0000000000a1',
    '0d000000-0000-4000-8000-0000000000b1', '0d000000-0000-4000-8000-0000000000d1',
    '[{"word_index":0,"text":"new","start_ms":0,"end_ms":90,"confidence":0.9},
      {"word_index":1,"text":"words","start_ms":100,"end_ms":200,"confidence":null}]'),
  2, 'transcript words are replaced'
);
select is(
  (select array_agg(text order by word_index) from public.transcript_words
   where transcript_id = '0d000000-0000-4000-8000-0000000000d1'),
  array['new', 'words'], 'only the new words remain'
);
select throws_ok(
  $$select public.motionly_replace_transcript_words('0d000000-0000-4000-8000-0000000000a1',
      '0d000000-0000-4000-8000-0000000000b1', '0d000000-0000-4000-8000-0000000000d1',
      '[{"word_index":0,"text":"bad","start_ms":100,"end_ms":10}]')$$,
  '23514', null, 'an invalid word fails the whole replacement'
);
select is(
  (select array_agg(text order by word_index) from public.transcript_words
   where transcript_id = '0d000000-0000-4000-8000-0000000000d1'),
  array['new', 'words'], 'a failed replacement leaves the previous words intact'
);
select throws_ok(
  $$select public.motionly_replace_transcript_words('0d000000-0000-4000-8000-0000000000a2',
      '0d000000-0000-4000-8000-0000000000b1', '0d000000-0000-4000-8000-0000000000d1', '[]')$$,
  'P0002', 'TRANSCRIPT_NOT_FOUND', 'a transcript is replaced only within its own tenant'
);
select is(
  public.motionly_replace_caption_segments('0d000000-0000-4000-8000-0000000000a1',
    '0d000000-0000-4000-8000-0000000000b1', '0d000000-0000-4000-8000-0000000000e1',
    '[{"segment_index":0,"start_ms":0,"end_ms":900,"text":"Hello","words":[{"text":"Hello"}],
       "line_count":1,"highlight_words":["Hello"]}]'),
  1, 'caption segments are replaced'
);
select ok(
  (select words = '[{"text":"Hello"}]'::jsonb and highlight_words = '["Hello"]'::jsonb
   from public.caption_segments where caption_track_id = '0d000000-0000-4000-8000-0000000000e1'),
  'segment words and highlights are stored'
);
reset role;

select ok(
  not has_function_privilege('authenticated', 'public.motionly_replace_transcript_words(uuid,uuid,uuid,jsonb)', 'EXECUTE')
  and not has_function_privilege('anon', 'public.motionly_sum_cost_usage(uuid,text,timestamptz,timestamptz,text)', 'EXECUTE')
  and has_function_privilege('service_role', 'public.motionly_cost_summary(uuid)', 'EXECUTE')
  and not has_function_privilege('motionly_worker', 'public.motionly_cost_summary(uuid)', 'EXECUTE')
  and not has_function_privilege('motionly_worker', 'public.motionly_replace_caption_segments(uuid,uuid,uuid,jsonb)', 'EXECUTE'),
  'the aggregate and replacement functions are not callable from the browser'
);
select ok(
  has_function_privilege('motionly_worker', 'public.motionly_sum_cost_usage(uuid,text,timestamptz,timestamptz,text)', 'EXECUTE')
  and has_function_privilege('motionly_worker', 'public.motionly_sum_usage_ledger(uuid,text,timestamptz,timestamptz)', 'EXECUTE')
  and has_function_privilege('motionly_worker', 'public.motionly_replace_transcript_words(uuid,uuid,uuid,jsonb)', 'EXECUTE'),
  'media workers can read usage and save transcript words'
);

select * from finish();
rollback;
