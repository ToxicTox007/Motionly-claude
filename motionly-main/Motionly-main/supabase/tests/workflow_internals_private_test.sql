-- Workflow internals are private to the API (202610230301).
begin;
select plan(5);

select ok(
  not exists (
    select 1
    from unnest(array[
      'transactional_outbox', 'workflow_run_commands', 'workflow_step_attempts'
    ]) as target(table_name)
    where has_table_privilege('authenticated', format('public.%I', table_name), 'SELECT')
       or has_table_privilege('anon', format('public.%I', table_name), 'SELECT')
  ),
  'browser roles cannot read the outbox, run commands or step attempts'
);

select ok(
  not exists (
    select 1 from pg_policy p
    join pg_class c on c.oid = p.polrelid
    where c.relnamespace = 'public'::regnamespace
      and c.relname in ('transactional_outbox', 'workflow_run_commands', 'workflow_step_attempts')
      and p.polname like '%members_read%'
  ),
  'no member-read policies remain on workflow internals'
);

select ok(
  not exists (
    select 1
    from unnest(array[
      'workflow_runs', 'workflow_steps', 'workflow_events', 'workflow_run_artifacts'
    ]) as target(table_name)
    where not has_table_privilege('authenticated', format('public.%I', table_name), 'SELECT')
  ),
  'members still read user-facing workflow state'
);

select ok(
  has_table_privilege('service_role', 'public.transactional_outbox', 'SELECT')
  and has_table_privilege('service_role', 'public.transactional_outbox', 'UPDATE')
  and has_table_privilege('service_role', 'public.workflow_step_attempts', 'SELECT'),
  'the API keeps full access to workflow internals'
);

-- Behaviour: a signed-in member of the workspace sees nothing in the outbox.
insert into auth.users (id, email, raw_user_meta_data)
values ('2e000000-0000-4000-8000-0000000000f1', 'outbox-member@test.local', '{}');
set local role authenticated;
select set_config('request.jwt.claim.sub', '2e000000-0000-4000-8000-0000000000f1', true);
select set_config(
  'request.jwt.claims',
  '{"sub":"2e000000-0000-4000-8000-0000000000f1","role":"authenticated"}',
  true
);
do $$
begin
  perform 1 from public.transactional_outbox limit 1;
  perform set_config('motionly_test.outbox_read', 'allowed', true);
exception when insufficient_privilege then
  perform set_config('motionly_test.outbox_read', 'denied', true);
end
$$;
reset role;
select is(current_setting('motionly_test.outbox_read'), 'denied',
  'a signed-in member querying the outbox is refused');

select * from finish();
rollback;
