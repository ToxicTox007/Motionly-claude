# Background job recovery runbook

How to see that background work is failing, and how to replay it safely. The job identities and
retry rules themselves are in the [job idempotency matrix](job-idempotency-matrix.md).

## Signals

Metric alerts ship as code in `infra/monitoring/prometheus-alerts.yml` (queue without a live worker,
stale oldest job, dead letters, failure bursts, stale heartbeats). They read the API's `GET /metrics`;
`motionly_queue_live_workers{queue}` counts live worker heartbeats per queue. Set
`REQUIRED_WORKER_QUEUES` (comma-separated) on the API so `GET /health/dependencies` reports a queue
without a worker as `degraded`.

Signals that exist only in logs:

| Log event                                                                   | Meaning                                                                                                         |
| --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `workflow_reconciliation_action` with `action` `reported_*`                 | A workflow state mismatch the reconciler could not repair. Inspect the run.                                     |
| `workflow_reconciliation_action` with `action` `repaired_failed_*`          | A run whose Temporal execution failed, timed out or disappeared was marked failed.                              |
| `temporal_dispatch_iteration_failed`, `workflow_reconciliation_pass_failed` | The dispatcher/reconciler hit a transient fault and is backing off (it no longer exits).                        |
| `temporal_cancellation_failed`                                              | A project-delete cancellation failed; it is retried with backoff.                                               |
| `workflow_failure_callback_failed`                                          | A worker could not record a workflow step failure after retries; the workflow fails after the 2 h outcome wait. |
| `job_failed`                                                                | A BullMQ job attempt failed (`error_code`, `attempts_made`).                                                    |
| `worker_redis_unreachable`, `worker_shutdown_forced`                        | A worker lost Redis (its liveness file stops) or was force-closed at shutdown.                                  |
| `retention_step_failed`, `retention_storage_deletion_failed`                | A retention step or a storage deletion failed; the run is recorded as `failed`.                                 |

## Replaying failed queue jobs

Finished BullMQ jobs are kept for history (completed 1 day, failed 7 days). A deliberate re-run from
the product — render retry, forced re-transcription, re-saving effects with `force=true` — now
replaces the finished job with the same id and runs it again. Plain duplicate requests while a job is
queued or running still collapse onto the existing job.

- Retry one failed or dead-lettered job: `POST /admin/queues/jobs/{queue_job_id}/retry`.
- Jobs failed with a permanent error (invalid payload, inference catalog mismatch, deleted matte) were
  discarded without retries. Fix the cause first, then re-run from the product.
- Renders orphaned by the job-id defect fixed in audit WJ-01 (status `queued`, no queue job):
  `python -m app.ops.requeue_orphaned_renders` lists them; add `--apply` to queue them.

## Storage deletion queue

A row whose lease expires 20 times without an acknowledgement is dead-lettered and kept with its last
error. Inspect and replay after fixing the cause (bucket, permissions):

```sql
select id, storage_bucket, storage_path, attempt_count, last_error_code, dead_lettered_at
from public.storage_deletion_queue
where dead_lettered_at is not null
order by dead_lettered_at desc;

update public.storage_deletion_queue
set dead_lettered_at = null, attempt_count = 0, last_error_code = null
where id = '<queue-id>';
```

The next retention run deletes it. Each run claims batches until the queue is empty or ten minutes per
drain have passed. `retention_runs` records every run; at most one is `running`, and a run left
`running` for six hours is closed as `failed` (`abandoned_run`).

## Workflow cancellations and outbox

Project-delete cancellations are retried with backoff and dead-lettered after 20 failures:

```sql
select id, temporal_workflow_id, attempt_count, last_error_code
from public.workflow_cancellation_requests
where dead_lettered_at is not null;

update public.workflow_cancellation_requests
set dead_lettered_at = null, attempt_count = 0, lease_expires_at = null
where id = '<request-id>';
```

A dead-lettered outbox message (a workflow start or signal that failed `max_attempts` times) can be
re-armed for the dispatcher:

```sql
update public.transactional_outbox
set state = 'failed', dead_lettered_at = null, attempt_count = 0, available_at = now()
where id = '<outbox-id>' and state = 'dead_letter';
```

## Stuck workflow runs

The reconciler fails runs whose Temporal execution failed, was terminated, timed out or no longer
exists, and the workflow itself fails a run when a step outcome does not arrive within two hours or
an activity exhausts its retries. To fail a run by hand (idempotent, service role only):

```sql
select public.motionly_fail_workflow_run(
  '<workspace-id>', '<project-id>', '<workflow-run-id>', 'operator_failed', 'Stopped by an operator.'
);
```

Users can then retry the workflow from the product.

## Worker settings

| Setting                                                    | Default                    | Effect                                                                                                                         |
| ---------------------------------------------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `PYTHON_EXECUTOR_POOL_SIZE`                                | 2                          | Persistent Python executors per worker for domain jobs (each loads the full app); GPU executors use their queue's concurrency. |
| `PYTHON_EXECUTOR_REQUEST_TIMEOUT_MS`                       | 900000                     | Per-job execution limit. Waiting for a free executor does not count.                                                           |
| `WORKER_SHUTDOWN_TIMEOUT_MS`                               | 840000                     | How long SIGTERM waits for active jobs before force-closing. Compose `stop_grace_period` is 15 min.                            |
| `RETENTION_REQUEST_TIMEOUT_MS`, `BATCH_REQUEST_TIMEOUT_MS` | 290000                     | Worker-side HTTP timeout; a retry while the API is still working is answered with `already_running`.                           |
| `RETENTION_CRON`, `GPU_AUTOSCALING_CRON`                   | `0 3 * * *`, `*/2 * * * *` | Maintenance schedules, evaluated in UTC; changing one replaces the old schedule.                                               |
| `REDIS_MAXMEMORY`                                          | 2gb                        | Redis rejects writes (`noeviction`) instead of being OOM-killed.                                                               |
