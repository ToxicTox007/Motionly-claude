-- Phase 7: outcome-based usage and idempotent acceptance analytics.

alter table public.usage_ledger drop constraint if exists usage_ledger_event_type_check;
alter table public.usage_ledger
  add constraint usage_ledger_event_type_check
  check (event_type in ('render_started', 'render_succeeded', 'premium_action')) not valid;

create unique index if not exists usage_ledger_outcome_source_unique_idx
  on public.usage_ledger(workspace_id, event_type, source_id);

with ranked as (
  select id, row_number() over (
    partition by workspace_id, event_type, decision_id
    order by created_at desc, id desc
  ) as duplicate_rank
  from public.ai_edit_feedback_events
  where decision_id is not null
)
delete from public.ai_edit_feedback_events events
using ranked
where events.id = ranked.id and ranked.duplicate_rank > 1;

create unique index if not exists ai_edit_feedback_decision_outcome_unique_idx
  on public.ai_edit_feedback_events(workspace_id, decision_id)
  where event_type = 'kept' and decision_id is not null;
