# Performance Query Plan Evidence

## 2026-07-06 Phase 2 Current Local EXPLAIN Recheck

Command:

```bash
docker exec -i supabase_db_motionly psql -U postgres -d postgres -v ON_ERROR_STOP=1 < scripts/phase2_explain_synthetic.sql
```

Local proof method:

- Local Supabase database on PostgreSQL 17.6.
- Inserted 20,000 synthetic rows per measured high-volume surface inside a
  single transaction.
- Ran `analyze` for each measured table.
- Captured `explain (analyze, buffers)` for the current indexed query paths.
- Rolled the transaction back, so proof rows were not persisted.

Result:

| Query Surface | Plan | Execution Time | Buffer Activity |
| --- | --- | ---: | --- |
| Worker heartbeat freshness | `Limit -> Index Scan` using `worker_heartbeats_last_seen_at_desc_idx` | 0.055 ms | 5 shared hits |
| Active upload lookup | `Limit -> Index Scan` using `uploaded_videos_workspace_project_updated_idx` | 0.026 ms | 3 shared hits |
| Latest plan by transcript | `Limit -> Index Scan` using `edit_plans_workspace_project_transcript_updated_idx` | 0.029 ms | 4 shared hits |
| Publish connections list | `Limit -> Index Scan` using `publish_connections_workspace_created_idx` | 0.069 ms | 4 shared hits |

Additional current verification:

- `supabase test db --local supabase/tests/phase_2_query_plan_indexes_test.sql supabase/tests/phase_9_rls_test.sql`
  passed 17 pgTAP tests.
- `supabase db lint --local --fail-on error` reported no schema errors.

## 2026-07-05 Phase 2 Worker Heartbeats

Query:

```sql
select worker_id, worker_role, queues, version, metadata, last_seen_at
from public.worker_heartbeats
order by last_seen_at desc
limit 100;
```

Local proof method:

- Local Supabase database on PostgreSQL 17.
- Inserted 20,000 synthetic `worker_heartbeats` rows inside a transaction.
- Ran `analyze public.worker_heartbeats`.
- Captured `explain (analyze, buffers, format json)` before and after creating
  `worker_heartbeats_last_seen_at_desc_idx`.
- Rolled the transaction back, so synthetic rows and the temporary proof index
  were not persisted.

Result:

| State | Plan | Execution Time | Buffer Activity |
| --- | --- | ---: | --- |
| Before index | `Limit -> Sort`, top-N heapsort | 7.489 ms | 287 shared hits |
| After index | `Limit -> Index Scan` using `worker_heartbeats_last_seen_at_desc_idx` | 0.092 ms | 3 shared hits, 2 shared reads |

Decision:

- Added migration `202607050002_phase_2_worker_heartbeat_index.sql`.
- Updated the heartbeat read path to request explicit columns instead of `select=*`.

## 2026-07-05 Phase 2 Uploads And Cost Attribution

Upload active lookup query:

```sql
select id, project_id, workspace_id, storage_bucket, storage_path,
       original_filename, mime_type, file_size_bytes, byte_size, status,
       thumbnail_path, metadata, error_code, error_message, created_at, updated_at
from public.uploaded_videos
where project_id = $1
  and workspace_id = $2
order by updated_at desc
limit 1;
```

Local proof method:

- Local Supabase database on PostgreSQL 17.
- Inserted one workspace/project and 20,001 synthetic `uploaded_videos` rows
  inside a transaction.
- Ran `analyze public.uploaded_videos`.
- Captured `explain (analyze, buffers, format json)` before and after creating
  `uploaded_videos_workspace_project_updated_idx`.
- Rolled the transaction back.

Result:

| State | Plan | Execution Time | Buffer Activity |
| --- | --- | ---: | --- |
| Before index | `Limit -> Sort`, top-N heapsort | 10.170 ms | 465 shared hits |
| After index | `Limit -> Index Scan` using `uploaded_videos_workspace_project_updated_idx` | 0.057 ms | 1 shared hit, 3 shared reads |

Cost usage query:

```sql
select quantity
from public.cost_attribution_events
where workspace_id = $1
  and provider = 'openai'
  and category = 'tokens'
  and created_at >= now() - interval '1440 minutes'
  and created_at < now()
limit 10000;
```

Local proof method:

- Inserted one workspace/project and 120,000 synthetic
  `cost_attribution_events` rows inside a transaction.
- Ran `analyze public.cost_attribution_events`.
- Captured `explain (analyze, buffers, format json)` before and after creating
  `cost_attribution_workspace_provider_category_created_idx`.
- Rolled the transaction back.

Result:

| State | Plan | Execution Time | Buffer Activity |
| --- | --- | ---: | --- |
| Before index | `Limit -> Index Scan` using `cost_attribution_workspace_created_idx` | 0.574 ms | 39 shared hits |
| After index | `Limit -> Index Scan` using `cost_attribution_workspace_provider_category_created_idx` | 0.121 ms | 29 shared hits, 3 shared reads |

Rejected candidate:

- `cost_attribution_events(render_job_id, workspace_id)` was tested against the
  `cost_summary(render_job_id=...)` read path with 30,000 synthetic rows.
- The planner stayed on `Limit -> Seq Scan` and execution remained 3.932 ms
  before and after the candidate index, so no migration was added for that
  candidate.

Decision:

- Added migration `202607050003_phase_2_upload_cost_indexes.sql`.
- Updated cost-summary reads to request explicit columns instead of `select=*`.

## 2026-07-05 Phase 2 Plans And Publishing

Latest plan by transcript query:

```sql
select id, workspace_id, project_id, transcript_id, version, schema_version,
       status, is_active, style, target_duration_s, broll_density, prompt,
       feedback, provider, model, planner_source, model_name, prompt_version,
       retry_count, fallback_reason, cache_key, schema_hash, latency_ms,
       cost_estimate_usd, validation_errors, summary, plan, created_at, updated_at
from public.edit_plans
where workspace_id = $1
  and project_id = $2
  and transcript_id = $3
order by updated_at desc
limit 1;
```

Local proof method:

- Inserted one workspace/project/upload/transcript and 20,000 synthetic
  `edit_plans` rows inside a transaction.
- Ran `analyze public.edit_plans`.
- Captured `explain (analyze, buffers, format json)` before and after creating
  `edit_plans_workspace_project_transcript_updated_idx`.
- Rolled the transaction back.

Result:

| State | Plan | Execution Time | Buffer Activity |
| --- | --- | ---: | --- |
| Before index | `Limit -> Sort`, top-N heapsort | 16.216 ms | 616 shared hits |
| After index | `Limit -> Index Scan` using `edit_plans_workspace_project_transcript_updated_idx` | 0.053 ms | 1 shared hit, 3 shared reads |

Publish connections list query:

```sql
select id, workspace_id, platform, access_token, refresh_token,
       token_expires_at, refresh_token_expires_at, scopes,
       external_account_id, account_name, connection_status,
       last_refresh_at, last_refresh_failure_reason, created_at
from public.publish_connections
where workspace_id = $1
order by created_at desc
limit 100;
```

Local proof method:

- Inserted one workspace and 20,000 synthetic `publish_connections` rows inside
  a transaction.
- Ran `analyze public.publish_connections`.
- Captured `explain (analyze, buffers, format json)` before and after creating
  `publish_connections_workspace_created_idx`.
- Rolled the transaction back.

Result:

| State | Plan | Execution Time | Buffer Activity |
| --- | --- | ---: | --- |
| Before index | `Limit -> Sort`, top-N heapsort | 9.480 ms | 397 shared hits |
| After index | `Limit -> Index Scan` using `publish_connections_workspace_created_idx` | 0.072 ms | 2 shared hits, 2 shared reads |

Decision:

- Added migration `202607050004_phase_2_plan_publish_indexes.sql`.
- Extended `supabase/tests/phase_2_query_plan_indexes_test.sql` to assert the
  plan and publish indexes exist.

## 2026-07-05 Phase 2 Existing Index Coverage

These Phase 2 plan items were measured with synthetic local data and already
used existing indexes, so no additional migration was added.

Local proof method:

- Inserted one workspace and 20,000 synthetic rows for each measured table
  inside a transaction.
- Ran `analyze` for each table.
- Captured `explain (analyze, buffers, format json)`.
- Rolled the transaction back.

Result:

| Query Surface | Existing Index Used | Execution Time | Buffer Activity |
| --- | --- | ---: | --- |
| Project list by workspace, `updated_at desc limit 100` | `projects_workspace_updated_idx` | 0.092 ms | 4 shared hits |
| Render jobs by project/workspace, `created_at desc limit 100` | `render_jobs_project_created_idx` | 0.117 ms | 5 shared hits |
| Publish jobs by project/workspace, `created_at desc limit 100` | `publish_jobs_project_created_idx` | 0.071 ms | 5 shared hits |
| Provider SLO by provider, `created_at desc limit 100` | `provider_slo_events_provider_created_idx` | 0.063 ms | 5 shared hits |

Decision:

- No new indexes were added for these query surfaces because the existing plans
  already used targeted indexes and the measured local execution was
  sub-millisecond.
