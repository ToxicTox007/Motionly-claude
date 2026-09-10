# Temporal task-queue migration

The Temporal task queue is a **data-plane** contract, not a cosmetic configuration value. Every dispatcher, worker, reconciler, and health-check participant must agree on the queue name or workflows can remain unprocessed.

## Current names

- `motionly-orchestration` is the canonical queue for the self-hosted Compose deployment.
- `motionly-workflows` is a legacy cloud-deployment name retained only for backward-compatibility evidence.

New deployments should use `motionly-orchestration` unless a separately approved cloud contract explicitly requires the legacy name.

## Migration procedure

Changing a queue name requires a coordinated deployment and workflow-draining plan:

1. Identify active workflows and record the current queue before changing configuration.
2. Drain or complete workflows on the old queue, or run a deliberately configured compatibility worker for both queues.
3. Deploy dispatcher, worker, reconciler, and health-check configuration together.
4. Verify that new workflow submissions and worker heartbeats use the same queue.
5. Monitor workflow age, queue depth, and failure signals before retiring the old queue.
6. Keep rollback configuration available until all in-flight workflows have reached a terminal state.

Do not change `TEMPORAL_TASK_QUEUE` in only one environment file or one service. A mismatch creates a compatibility split where submissions succeed but no worker consumes them.

## Related checks

The canonical queue is asserted by `apps/api/tests/test_temporal_queue_canonical.py`. Self-hosted deployment behavior is documented in [Compose VM deployment](compose-vm-deployment.md) and [Operations](../operations.md).
