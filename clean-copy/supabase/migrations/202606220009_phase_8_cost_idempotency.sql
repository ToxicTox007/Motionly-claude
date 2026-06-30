-- Follow-up for environments where the Phase 8 operations migration was applied before
-- deterministic cost-event identities were introduced.

alter table public.cost_attribution_events add column if not exists source_id text;
update public.cost_attribution_events
set source_id = md5(id::text)
where source_id is null;
alter table public.cost_attribution_events alter column source_id set not null;
create unique index if not exists cost_attribution_source_unique_idx
  on public.cost_attribution_events(workspace_id, provider, category, source_id);
