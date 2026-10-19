-- Task 4: Motionly no longer publishes to social platforms and no longer
-- collects post-performance metrics; users play, download, share and export
-- their videos instead. Drop the social publishing and performance-feedback
-- objects. Localized publish packages (translated captions and title and
-- description suggestions for exports) are a separate export feature and stay.
--
-- Plain DROP (no IF EXISTS, no CASCADE): every object was created by an
-- earlier migration, and an unexpected dependency must stop the release.

begin;

-- motionly_audit_task3_tenant_integrity() resolves every manifest table by
-- name, so constraints of the dropped tables must leave the manifest first.
delete from public.tenant_integrity_findings
 where constraint_name in (
   select constraint_name
     from public.tenant_integrity_constraint_manifest
    where table_name in ('publish_jobs', 'publish_connections')
 );
delete from public.tenant_integrity_constraint_manifest
 where table_name in ('publish_jobs', 'publish_connections');

do $task4$
declare
  target regprocedure;
begin
  for target in
    select p.oid::regprocedure
      from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'public'
       and p.proname in (
         'motionly_capture_performance_metric',
         'motionly_revoke_performance_feedback',
         'motionly_set_performance_feedback_policy'
       )
  loop
    execute format('drop function %s', target);
  end loop;
end
$task4$;

-- Children before parents: metrics reference publish jobs, policy events
-- reference policies, and publish jobs reference connections.
drop table public.performance_feedback_metrics;
drop table public.performance_feedback_policy_events;
drop table public.performance_feedback_policies;
drop table public.publish_jobs;
drop table public.publish_connections;

commit;
