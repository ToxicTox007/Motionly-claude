-- Restore Task 5's intended privilege boundary explicitly: members may read
-- through RLS, only service-role RPCs/workers may append revisions.

revoke all on public.timeline_revisions from public, anon, authenticated;
grant select on public.timeline_revisions to authenticated;
grant all on public.timeline_revisions to service_role;
