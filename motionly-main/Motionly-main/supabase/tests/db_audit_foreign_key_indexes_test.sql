-- Database audit DB-06 / DB-18 (migration 202610280203): every foreign key
-- in public is index-backed and no index duplicates another.

begin;
select plan(4);

-- A foreign key is index-backed when an index leads with exactly its columns,
-- or (composite tenant keys) leads with its one non-tenant column.
create temporary table db_audit_unindexed_fks as
with fk as (
  select c.conrelid, c.conname, c.conkey,
    (select array_agg(a.attname order by u.ord)
     from unnest(c.conkey) with ordinality u(attnum, ord)
     join pg_attribute a on a.attrelid = c.conrelid and a.attnum = u.attnum) as cols
  from pg_constraint c
  where c.connamespace = 'public'::regnamespace and c.contype = 'f'
)
select fk.conrelid::regclass::text as table_name, fk.conname
from fk
where not exists (
    select 1 from pg_index i
    where i.indrelid = fk.conrelid
      and (i.indkey::int2[])[0:array_length(fk.conkey, 1) - 1] @> fk.conkey
      and (i.indkey::int2[])[0:array_length(fk.conkey, 1) - 1] <@ fk.conkey
  )
  and not exists (
    select 1 from pg_index i
    join pg_attribute a on a.attrelid = i.indrelid and a.attnum = (i.indkey::int2[])[0]
    where i.indrelid = fk.conrelid
      and a.attname = coalesce(
        (select x from unnest(fk.cols) x where x not in ('workspace_id', 'project_id') limit 1),
        'project_id')
  );

select is(
  (select count(*)::int from db_audit_unindexed_fks), 0,
  'every foreign key in public has a supporting index'
);

select is(
  (select count(*)::int from (
     select 1
     from pg_index i
     join pg_class c on c.oid = i.indrelid
     where c.relnamespace = 'public'::regnamespace
     group by i.indrelid, i.indkey::text, i.indclass::text,
              coalesce(pg_get_expr(i.indexprs, i.indrelid), ''),
              coalesce(pg_get_expr(i.indpred, i.indrelid), '')
     having count(*) > 1) dup),
  0,
  'no index duplicates another index on the same table'
);

select ok(
  exists (select 1 from pg_indexes where schemaname = 'public'
          and indexname = 'transcript_words_project_id_fk_idx')
  and exists (select 1 from pg_indexes where schemaname = 'public'
              and indexname = 'caption_segments_project_id_fk_idx'),
  'the project cascades into the largest tables are index-backed'
);

-- The constraint-backing copies of the removed duplicates remain.
select ok(
  exists (select 1 from pg_constraint where conname = 'transcript_words_transcript_id_word_index_key')
  and exists (select 1 from pg_constraint where conname = 'caption_segments_caption_track_id_segment_index_key')
  and exists (select 1 from pg_indexes where indexname = 'timeline_revisions_plan_revision_unique_idx'),
  'unique constraints and ON CONFLICT targets are kept'
);

select * from finish();
rollback;
