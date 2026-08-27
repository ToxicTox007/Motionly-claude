begin;
select plan(8);

select has_table('public', 'client_review_revisions', 'review revision ledger exists');
select has_table('public', 'client_review_comments', 'review comment ledger exists');
select has_table('public', 'client_review_comment_assignees', 'review assignment ledger exists');
select has_table('public', 'client_review_share_links', 'review share-link ledger exists');
select ok(
  (select relrowsecurity from pg_class where oid = 'public.client_review_revisions'::regclass)
  and (select relrowsecurity from pg_class where oid = 'public.client_review_comments'::regclass)
  and (select relrowsecurity from pg_class where oid = 'public.client_review_comment_assignees'::regclass)
  and (select relrowsecurity from pg_class where oid = 'public.client_review_share_links'::regclass),
  'all client-review tables enforce RLS'
);
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.client_review_comments'::regclass
      and conname = 'client_review_comments_review_tenant_fk'
  ),
  'comments cannot cross the review workspace/project boundary'
);
select ok(
  not has_table_privilege('authenticated', 'public.client_review_revisions', 'SELECT')
    and not has_table_privilege('anon', 'public.client_review_share_links', 'SELECT'),
  'browser roles cannot access review collaboration data directly'
);
select ok(
  has_table_privilege('service_role', 'public.client_review_revisions', 'INSERT')
    and has_table_privilege('service_role', 'public.client_review_share_links', 'UPDATE'),
  'service role retains the API-mediated collaboration path'
);

select * from finish();
rollback;
