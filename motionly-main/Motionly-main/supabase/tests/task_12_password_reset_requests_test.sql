begin;
select plan(12);

select has_table(
  'public', 'password_reset_requests',
  'password reset requests are persisted'
);
select has_table(
  'public', 'admin_audit_events',
  'admin audit events are persisted'
);

select ok(
  (select count(*) from information_schema.columns
   where table_schema = 'public'
     and table_name = 'password_reset_requests'
     and column_name = any(array[
       'id', 'user_id', 'email', 'status', 'viewer_hash',
       'handled_by', 'handled_at', 'created_at'
     ])) = 8,
  'reset requests persist id, user, email, status, viewer hash, and handling'
);
select ok(
  (select count(*) from information_schema.columns
   where table_schema = 'public'
     and table_name = 'admin_audit_events'
     and column_name = any(array[
       'id', 'actor_user_id', 'action', 'target_user_id', 'details', 'created_at'
     ])) = 6,
  'audit events persist actor, action, target, details, and time'
);

select col_is_null(
  'public', 'password_reset_requests', 'user_id',
  'the reset request user is nullable (unknown emails are recorded too)'
);

select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.password_reset_requests'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) like '%link_issued%'
      and pg_get_constraintdef(oid) like '%dismissed%'
  ),
  'reset request status is constrained to the known lifecycle values'
);

-- RLS is on for both tables.
select ok(
  (select relrowsecurity from pg_class
   where oid = 'public.password_reset_requests'::regclass)
  and (select relrowsecurity from pg_class
       where oid = 'public.admin_audit_events'::regclass),
  'both Task 12 tables enforce RLS'
);

-- anon and authenticated are denied every privilege; only service_role writes.
select ok(
  not has_table_privilege('anon', 'public.password_reset_requests', 'SELECT')
  and not has_table_privilege('anon', 'public.password_reset_requests', 'INSERT')
  and not has_table_privilege('authenticated', 'public.password_reset_requests', 'SELECT')
  and not has_table_privilege('authenticated', 'public.password_reset_requests', 'INSERT'),
  'browser roles cannot read or write reset requests'
);
select ok(
  not has_table_privilege('anon', 'public.admin_audit_events', 'SELECT')
  and not has_table_privilege('anon', 'public.admin_audit_events', 'INSERT')
  and not has_table_privilege('authenticated', 'public.admin_audit_events', 'SELECT')
  and not has_table_privilege('authenticated', 'public.admin_audit_events', 'INSERT'),
  'browser roles cannot read or write audit events'
);
select ok(
  has_table_privilege('service_role', 'public.password_reset_requests', 'INSERT')
  and has_table_privilege('service_role', 'public.password_reset_requests', 'SELECT')
  and has_table_privilege('service_role', 'public.admin_audit_events', 'INSERT')
  and has_table_privilege('service_role', 'public.admin_audit_events', 'SELECT'),
  'the service role may read and write both tables'
);

-- The audit log is append-oriented: a reset request keeps a viewer hash, not
-- the raw address.
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.password_reset_requests'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) like '%[0-9a-f]{12}%'
  ),
  'the viewer hash is a fixed-width digest, never a raw address'
);

select ok(
  to_regclass('public.password_reset_requests_pending_idx') is not null
  and to_regclass('public.admin_audit_events_recent_idx') is not null,
  'pending requests and recent audit events are indexed for the admin page'
);

select * from finish();
rollback;
