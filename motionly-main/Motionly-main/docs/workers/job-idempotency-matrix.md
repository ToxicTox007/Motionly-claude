# Job Idempotency Matrix

Phase 8 uses deterministic queue job ids, upsert/reuse paths, and bounded recovery rules so retries do not create duplicate side effects.

| Job type           | Input identity                                         | Idempotency key                                                           | Output / side effects                                               | Retryable failures                       | Non-retryable failures               | Cancellation                          | Stuck timeout / recovery                                           |
| ------------------ | ------------------------------------------------------ | ------------------------------------------------------------------------- | ------------------------------------------------------------------- | ---------------------------------------- | ------------------------------------ | ------------------------------------- | ------------------------------------------------------------------ |
| preprocessing      | `project_id + video_id + workspace_id`                 | `preprocess--{video_id}`                                                  | upload metadata, thumbnail, active video pointer                    | worker crash, transient storage          | invalid upload state                 | queued cancel only                    | queue retry, then dead-letter                                      |
| transcription      | `project_id + video_id + workspace_id`                 | `transcribe--{project_id}--{video_id}--{workspace_id}`                    | transcript row update                                               | provider/network, worker crash           | audio missing, unusable media        | queued cancel only                    | queue retry, transcript state visible                              |
| visual analysis    | `project_id + video_id + workspace_id`                 | `visual--{project_id}--{video_id}--{workspace_id}`                        | manifest row transition                                             | worker crash, provider/runtime transient | invalid source video                 | queued cancel only                    | ready/queued/processing work collapsed, queue retry                |
| matte extraction   | `project_id + video_id + workspace_id + matte_id`      | `matte--...--{matte_id}` plus matte cache key                             | matte row transition, matte asset                                   | worker crash, transient media/GPU        | invalid source, retry limit exceeded | queued cancel only                    | cache reuse, queue retry, failed state visible                     |
| edit planning      | `project_id + transcript_id + workspace_id`            | `plan--{project_id}--{transcript_id}--{workspace_id}` plus plan cache key | edit plan row                                                       | provider/network, worker crash           | invalid transcript quality           | queued cancel only                    | bounded retry/fallback, failed plan row                            |
| caption generation | `project_id + transcript_id + workspace_id`            | `captions--{project_id}--{transcript_id}--{workspace_id}`                 | caption track row                                                   | worker crash                             | invalid transcript/request           | queued cancel only                    | queue retry or dead-letter                                         |
| B-roll enrichment  | `project_id + edit_plan_id + workspace_id`             | `broll--{project_id}--{edit_plan_id}--{workspace_id}` plus cache key      | slot rows, stock assets upsert                                      | provider/network, worker crash           | invalid request                      | queued cancel only                    | cached result reuse, queue retry                                   |
| music selection    | `project_id + edit_plan_id + workspace_id`             | `music--{project_id}--{edit_plan_id}--{workspace_id}` plus cache key      | music selection, track upsert                                       | provider/network, worker crash           | invalid request                      | queued cancel only                    | cached result reuse, queue retry                                   |
| effects generation | `project_id + edit_plan_id + workspace_id`             | `effects--{project_id}--{edit_plan_id or no-plan}--{workspace_id}`        | effects config row                                                  | provider/network, worker crash           | invalid request                      | queued cancel only                    | cached result reuse, queue retry                                   |
| basic render       | `render_job_id + project_id + workspace_id + priority` | `render--{project_id}--{render_job_id}--{workspace_id}[--p{priority}]`    | render job transitions, export row, usage source id=`render_job_id` | worker crash, transient renderer/storage | invalid inputs, cancellation         | queued and in-flight cancel supported | retry re-runs the same queue job id, export row reused             |
| premium render     | `render_job_id + project_id + workspace_id + priority` | same as basic render, queue=`render_remotion`                             | render job transitions, export row, usage source id=`render_job_id` | worker crash, transient renderer/storage | invalid inputs, cancellation         | queued and in-flight cancel supported | retry same render job, export row reused                           |
| retention cleanup  | singleton job scheduler                                | `daily-retention` (UTC cron) plus one live `retention_runs` row           | deletion queue drain, export deletion, transcript redaction         | transient storage/db                     | invalid config                       | not cancelable in API                 | safe to rerun; concurrent runs refused, failing rows dead-lettered |

Stock asset enrichment uses `stock-asset-enrichment--{project_id}--{asset_id}--{workspace_id}--{refresh_digest}`
and layer regeneration `regenerate--{project_id}--{control_settings_id}--{workspace_id}`. Job ids never
contain `:`: BullMQ rejects such custom ids, and `assert_valid_queue_job_id` fails the enqueue before
any row depends on it.

## Recovery notes

- A duplicate enqueue while a job is queued or running returns that job. A deliberate re-run after the
  job finished (render retry, forced re-transcription) replaces the retained finished job and runs again.
- If an enqueue fails after the row was written, the row is failed (`queue_enqueue_failed`) so it can be
  retried instead of looking in progress. Mattes and transcripts left `pending`/`processing` for two
  hours are re-queued by the next request.
- Permanent failures (invalid payload, inference catalog mismatch, deleted matte, unhandled kind) are
  discarded instead of retried; transient ones keep their BullMQ attempts with exponential backoff.
- Persistent Python executors run one job each (`PYTHON_EXECUTOR_POOL_SIZE` per worker); a job's
  timeout covers only its own execution, and a hung job fails alone.
- Python subprocess failures are retried through BullMQ/job retry limits and emit structured subprocess restart events.
- In-memory/direct worker execution uses bounded backoff and `recover_stuck_jobs(...)` for stale `RUNNING` jobs.
- Paid usage is idempotent through `usage_ledger` dedupe on `(workspace_id, event_type, source_id)`; the
  paid edit-planner cap counts BullMQ transport attempts.
- Replay procedures and dead-letter handling: [background job recovery runbook](job-recovery-runbook.md).
