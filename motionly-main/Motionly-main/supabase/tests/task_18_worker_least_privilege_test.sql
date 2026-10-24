-- Task 18 follow-up (202610230101): motionly_worker has least privilege.
--
-- The worker reads and writes the job tables workers use and calls the job
-- RPCs; it can no longer touch identity, membership, billing or credential
-- tables, cannot run the team/billing SECURITY DEFINER RPCs, and gets nothing
-- automatically from future migrations.
begin;
select plan(16);

-- Identity, membership, billing and credential tables: no access at all.
select is(
  (
    select coalesce(array_agg(t order by t), '{}')
    from unnest(array[
      'workspaces', 'workspace_members', 'workspace_invitations',
      'workspace_api_keys', 'profiles', 'billing_adjustments',
      'gateway_webhook_events', 'admin_audit_events', 'password_reset_requests'
    ]) as t
    where has_table_privilege('motionly_worker', format('public.%I', t), 'SELECT')
       or has_table_privilege('motionly_worker', format('public.%I', t), 'INSERT')
       or has_table_privilege('motionly_worker', format('public.%I', t), 'UPDATE')
       or has_table_privilege('motionly_worker', format('public.%I', t), 'DELETE')
  ),
  '{}'::text[],
  'the worker has no privilege on identity, membership, billing or credential tables'
);
select ok(
  has_table_privilege('motionly_worker', 'public.subscriptions', 'SELECT')
  and not has_table_privilege('motionly_worker', 'public.subscriptions', 'INSERT')
  and not has_table_privilege('motionly_worker', 'public.subscriptions', 'UPDATE')
  and not has_table_privilege('motionly_worker', 'public.subscriptions', 'DELETE'),
  'the worker may read plans (feature quotas) but never change a subscription'
);

-- Job tables keep what workers need.
select ok(
  has_table_privilege('motionly_worker', 'public.render_jobs', 'SELECT')
  and has_table_privilege('motionly_worker', 'public.render_jobs', 'UPDATE')
  and has_table_privilege('motionly_worker', 'public.render_job_events', 'INSERT')
  and has_table_privilege('motionly_worker', 'public.transcripts', 'INSERT')
  and has_table_privilege('motionly_worker', 'public.caption_segments', 'INSERT')
  and has_table_privilege('motionly_worker', 'public.segmentation_mattes', 'UPDATE')
  and has_table_privilege('motionly_worker', 'public.usage_ledger', 'INSERT'),
  'the worker keeps its job-table privileges'
);
select ok(
  not has_table_privilege('motionly_worker', 'public.render_jobs', 'DELETE')
  and not has_table_privilege('motionly_worker', 'public.uploaded_videos', 'DELETE')
  and not has_table_privilege('motionly_worker', 'public.workflow_runs', 'UPDATE'),
  'job tables are granted per privilege, not wholesale'
);

-- SECURITY DEFINER functions: exactly the job RPCs (plus the RLS helper).
select is(
  (
    select coalesce(array_agg(p.proname::text order by p.proname), '{}')
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.prosecdef
      and has_function_privilege('motionly_worker', p.oid, 'EXECUTE')
  ),
  array[
    'is_workspace_member',
    'motionly_begin_workflow_step',
    'motionly_capture_editorial_feedback_delta',
    'motionly_commit_storage_quota',
    'motionly_complete_render',
    'motionly_complete_workflow_step',
    'motionly_create_active_edit_plan',
    'motionly_create_ai_edit_proposal',
    'motionly_fail_workflow_step',
    'motionly_prepare_workflow_step',
    'motionly_reserve_storage_quota',
    'motionly_transition_ai_edit_proposal',
    'motionly_upsert_base_export'
  ]::text[],
  'the worker may execute only the job RPCs among SECURITY DEFINER functions'
);
select ok(
  not exists (
    select 1 from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname in (
        'motionly_transfer_workspace_ownership', 'motionly_change_member_role',
        'motionly_remove_workspace_member', 'motionly_accept_workspace_invitation',
        'motionly_create_workspace_invitation', 'motionly_leave_workspace',
        'motionly_delete_account_workspaces', 'update_own_profile', 'handle_new_user'
      )
      and has_function_privilege('motionly_worker', p.oid, 'EXECUTE')
  ),
  'the worker cannot run team, account or profile RPCs'
);
select ok(
  has_function_privilege('motionly_worker', 'public.compute_mood_synonyms(text[])', 'EXECUTE'),
  'SECURITY INVOKER helpers stay executable (they run with the worker''s own privileges)'
);

-- Nothing is granted automatically to future objects.
select is(
  (select count(*)::integer from pg_default_acl d
   where d.defaclnamespace = 'public'::regnamespace
     and exists (
       select 1 from aclexplode(d.defaclacl) a
       where a.grantee = 'motionly_worker'::regrole
     )),
  0,
  'no default privileges grant anything to motionly_worker'
);
create table public.task18_least_privilege_probe (id integer);
create function public.task18_least_privilege_probe_fn() returns integer
  language sql security definer set search_path = public as $$ select 1 $$;
select ok(
  not has_table_privilege('motionly_worker', 'public.task18_least_privilege_probe', 'SELECT')
  and not has_function_privilege(
    'motionly_worker', 'public.task18_least_privilege_probe_fn()', 'EXECUTE')
  and not has_function_privilege(
    'public', 'public.task18_least_privilege_probe_fn()', 'EXECUTE')
  and has_function_privilege(
    'service_role', 'public.task18_least_privilege_probe_fn()', 'EXECUTE'),
  'a new table or function is not reachable by the worker or PUBLIC (service_role keeps its default)'
);

-- Behaviour, as the worker. pgTAP lives in a schema the worker cannot use, so
-- observations are recorded with set_config and asserted after reset role.
insert into auth.users (id, email, raw_user_meta_data) values
  ('2e000000-0000-4000-8000-0000000000cc', 'least-privilege@test.local', '{}'),
  ('2e000000-0000-4000-8000-0000000000cd', 'least-privilege-2@test.local', '{}');
insert into public.projects (id, workspace_id, created_by, title)
select '4e000000-0000-4000-8000-0000000000cc', w.id, w.owner_user_id, 'Least privilege'
from public.workspaces w
where w.owner_user_id = '2e000000-0000-4000-8000-0000000000cc';
insert into public.uploaded_videos (
  id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
  mime_type, byte_size, file_size_bytes, status, workflow_version
)
select '5e000000-0000-4000-8000-0000000000cc', p.workspace_id, p.id, 'raw-uploads',
  'worker/least.mp4', 'least.mp4', 'video/mp4', 2048, 2048, 'processing', 'first-draft-v2'
from public.projects p where p.id = '4e000000-0000-4000-8000-0000000000cc';
select set_config(
  'motionly_test.workspace',
  (select workspace_id::text from public.projects
   where id = '4e000000-0000-4000-8000-0000000000cc'),
  true
);

set local role motionly_worker;
do $$
declare
  attempt record;
begin
  for attempt in
    select * from (values
      ('read_members', 'select count(*) from public.workspace_members'),
      ('add_member', format(
        'insert into public.workspace_members (workspace_id, user_id, role, status) '
        'values (%L, %L, ''owner'', ''active'')',
        current_setting('motionly_test.workspace'),
        '2e000000-0000-4000-8000-0000000000cd')),
      ('upgrade_plan', format(
        'update public.subscriptions set plan = ''agency'', status = ''active'' '
        'where workspace_id = %L', current_setting('motionly_test.workspace'))),
      ('transfer_owner', format(
        'select public.motionly_transfer_workspace_ownership(%L, %L, %L)',
        current_setting('motionly_test.workspace'),
        '2e000000-0000-4000-8000-0000000000cc',
        '2e000000-0000-4000-8000-0000000000cd'))
    ) as t(name, statement)
  loop
    begin
      execute attempt.statement;
      perform set_config('motionly_test.' || attempt.name, 'allowed', true);
    exception when insufficient_privilege then
      perform set_config('motionly_test.' || attempt.name, 'denied', true);
    end;
  end loop;
end
$$;
-- A job write whose SECURITY DEFINER trigger calls a helper the worker has no
-- direct grant on (motionly_invalidate_on_media_replace) still works.
with updated as (
  update public.uploaded_videos
  set storage_path = 'worker/least-replaced.mp4', byte_size = 4096, file_size_bytes = 4096
  where id = '5e000000-0000-4000-8000-0000000000cc'
  returning 1
)
select set_config('motionly_test.replaced_upload', (select count(*)::text from updated), true);
reset role;

select is(current_setting('motionly_test.read_members'), 'denied',
  'the worker cannot read workspace memberships');
select is(current_setting('motionly_test.add_member'), 'denied',
  'the worker cannot add itself (or anyone) to a workspace');
select is(current_setting('motionly_test.upgrade_plan'), 'denied',
  'the worker cannot change a subscription');
select is(current_setting('motionly_test.transfer_owner'), 'denied',
  'the worker cannot transfer workspace ownership');
select is(current_setting('motionly_test.replaced_upload')::integer, 1,
  'the worker can still replace upload media (its SECURITY DEFINER trigger chain runs)');
select is(
  (select storage_path from public.uploaded_videos
   where id = '5e000000-0000-4000-8000-0000000000cc'),
  'worker/least-replaced.mp4',
  'the worker''s upload update is stored'
);
select is(
  (select count(*)::integer from public.workspace_members
   where user_id = '2e000000-0000-4000-8000-0000000000cd'
     and workspace_id = current_setting('motionly_test.workspace')::uuid),
  0,
  'no membership was created by the worker'
);

select * from finish();
rollback;
