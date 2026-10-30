-- Database audit DB-01 / DB-08 (migration 202610280201): anon and
-- authenticated reach only the SECURITY DEFINER functions and tables the
-- browser uses, and new objects are not born exposed to them.

begin;
select plan(12);

-- DB-01: the only SECURITY DEFINER functions the API roles may call.
select is(
  (select coalesce(array_agg(p.oid::regprocedure::text order by p.oid::regprocedure::text), '{}')
   from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'public'
     and p.prosecdef
     and has_function_privilege('anon', p.oid, 'EXECUTE')),
  array['is_workspace_member(uuid)'],
  'anon can EXECUTE no SECURITY DEFINER function except the RLS helper'
);
select is(
  (select coalesce(array_agg(p.oid::regprocedure::text order by p.oid::regprocedure::text), '{}')
   from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'public'
     and p.prosecdef
     and has_function_privilege('authenticated', p.oid, 'EXECUTE')),
  array['is_workspace_member(uuid)', 'motionly_member_workspace_ids()', 'update_own_profile(text,text,text)'],
  'authenticated can EXECUTE only the RLS helpers and update_own_profile'
);
select ok(
  not has_function_privilege('anon', 'public.motionly_invalidate_on_media_replace(uuid)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'public.motionly_invalidate_on_media_replace(uuid)', 'EXECUTE')
  and has_function_privilege('service_role', 'public.motionly_invalidate_on_media_replace(uuid)', 'EXECUTE'),
  'motionly_invalidate_on_media_replace is service-role only'
);
select ok(
  not has_function_privilege('anon', 'public.motionly_invalidate_stale_derivatives(uuid,uuid)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'public.motionly_invalidate_stale_derivatives(uuid,uuid)', 'EXECUTE')
  and has_function_privilege('service_role', 'public.motionly_invalidate_stale_derivatives(uuid,uuid)', 'EXECUTE'),
  'motionly_invalidate_stale_derivatives is service-role only'
);
select ok(
  not has_function_privilege('anon', 'public.motionly_claim_workflow_cancellations(integer)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'public.motionly_claim_workflow_cancellations(integer)', 'EXECUTE')
  and has_function_privilege('service_role', 'public.motionly_claim_workflow_cancellations(integer)', 'EXECUTE'),
  'motionly_claim_workflow_cancellations is service-role only'
);

-- Revoked trigger functions still fire: an uploaded_videos insert as the
-- service role runs the SECURITY DEFINER revision trigger.
insert into auth.users (id, email, raw_user_meta_data)
values ('0a000000-0000-4000-8000-000000000001', 'db-audit-priv@test.local', '{}');
insert into public.workspaces (id, name, owner_user_id)
values ('0a000000-0000-4000-8000-0000000000a1', 'DB audit privileges', '0a000000-0000-4000-8000-000000000001');
insert into public.projects (id, workspace_id, created_by, title)
values ('0a000000-0000-4000-8000-0000000000b1', '0a000000-0000-4000-8000-0000000000a1',
        '0a000000-0000-4000-8000-000000000001', 'Privileges');
set local role service_role;
select lives_ok(
  $$insert into public.uploaded_videos (
      id, workspace_id, project_id, storage_bucket, storage_path, original_filename,
      mime_type, byte_size, file_size_bytes)
    values ('0a000000-0000-4000-8000-0000000000c1', '0a000000-0000-4000-8000-0000000000a1',
      '0a000000-0000-4000-8000-0000000000b1', 'raw-uploads', 'db-audit/priv.mp4', 'priv.mp4',
      'video/mp4', 10, 10)$$,
  'trigger functions without anon/authenticated EXECUTE still fire'
);
reset role;

-- An anonymous caller is refused before the function body runs.
set local role anon;
select throws_ok(
  $$select * from public.motionly_claim_workflow_cancellations(10)$$,
  '42501', null,
  'anon cannot claim workflow cancellation requests'
);
select throws_ok(
  $$select public.motionly_invalidate_on_media_replace('0a000000-0000-4000-8000-0000000000b1')$$,
  '42501', null,
  'anon cannot mark a project''s derivatives stale'
);
reset role;

-- DB-08: no API-role privilege on the inference registry or mood synonyms.
select is(
  (select count(*)::int
   from information_schema.role_table_grants
   where table_schema = 'public'
     and grantee in ('anon', 'authenticated')
     and table_name in ('ai_adapters', 'ai_models', 'ai_routes', 'ai_secrets',
                        'ai_registry_version', 'music_mood_synonyms')),
  0,
  'anon/authenticated hold no privilege on the registry tables or mood synonyms'
);
set local role anon;
select throws_ok(
  $$truncate public.ai_secrets$$,
  '42501', null,
  'anon cannot TRUNCATE ai_secrets'
);
reset role;

-- New objects are not born exposed.
select is(
  (select count(*)::int
   from pg_default_acl d
   join pg_namespace n on n.oid = d.defaclnamespace
   cross join lateral aclexplode(d.defaclacl) a
   where n.nspname = 'public'
     and pg_get_userbyid(d.defaclrole) = 'postgres'
     and a.grantee in ('anon'::regrole::oid, 'authenticated'::regrole::oid)),
  0,
  'default privileges for postgres in public grant nothing to anon/authenticated'
);
create table public.db_audit_probe (id int primary key);
create function public.db_audit_probe_fn() returns int language sql as $$ select 1 $$;
select ok(
  not has_table_privilege('anon', 'public.db_audit_probe', 'SELECT')
  and not has_table_privilege('authenticated', 'public.db_audit_probe', 'SELECT')
  and not has_function_privilege('anon', 'public.db_audit_probe_fn()', 'EXECUTE')
  and not has_function_privilege('authenticated', 'public.db_audit_probe_fn()', 'EXECUTE'),
  'a newly created table and function grant nothing to anon/authenticated'
);

select * from finish();
rollback;
