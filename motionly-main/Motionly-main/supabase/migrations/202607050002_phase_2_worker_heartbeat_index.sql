-- Phase 2 database performance: support freshness-ordered worker heartbeat reads.

create index if not exists worker_heartbeats_last_seen_at_desc_idx
  on public.worker_heartbeats(last_seen_at desc);
