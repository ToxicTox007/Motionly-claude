"""Find (and optionally re-queue) QUEUED renders that have no queue job.

    python -m app.ops.requeue_orphaned_renders                 # report only
    python -m app.ops.requeue_orphaned_renders --apply         # re-queue them
    python -m app.ops.requeue_orphaned_renders --older-than-minutes 60 --limit 200

Before audit fix WJ-01, every user-started render and render retry built a
BullMQ job id containing ``:``, which BullMQ rejects: the render row (and its
billing slot) was written, the enqueue failed, and the render stayed QUEUED
with nothing to process it. This command lists non-workflow renders that are
QUEUED, untouched for ``--older-than-minutes``, and whose deterministic queue
job is missing or already finished. With ``--apply`` it enqueues them with the
current (``--``-joined) job id, so the user gets the render they paid a slot
for. Workflow first-draft renders are enqueued by Temporal and are skipped.

Reads SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and REDIS_URL from the API's
settings. It prints one tab-separated line per render: id, workspace, action.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from app.core.config import get_settings
from app.services.queue import (
    ACTIVE_QUEUE_JOB_STATUSES,
    BullMQQueueRegistry,
    QueueRegistry,
    RenderQueue,
)


def render_queue_target(row: dict[str, Any]) -> tuple[int, str, str]:
    """(priority, render_engine, compute_tier) as the create/retry path uses them."""
    manifest = row.get("manifest") if isinstance(row.get("manifest"), dict) else {}
    billing = manifest.get("billing") if isinstance(manifest.get("billing"), dict) else {}
    priority = 10 if billing.get("priority_queue") is True else 0
    render_engine = str(row.get("render_engine") or "ffmpeg_basic")
    compute_tier = str(manifest.get("compute_tier") or "cpu")
    return priority, render_engine, compute_tier


def classify_orphans(
    rows: Iterable[dict[str, Any]],
    registry: QueueRegistry | BullMQQueueRegistry,
    *,
    apply: bool,
) -> list[tuple[str, str, str]]:
    """Return (render_job_id, workspace_id, action) for each candidate row.

    action: ``queued`` (a live job exists), ``orphaned`` (report mode) or
    ``requeued`` (apply mode).
    """
    queue = RenderQueue(registry)
    results = []
    for row in rows:
        render_job_id = UUID(str(row["id"]))
        workspace_id = UUID(str(row["workspace_id"]))
        project_id = UUID(str(row["project_id"]))
        priority, render_engine, compute_tier = render_queue_target(row)
        job_id = RenderQueue.job_id_for_render(
            render_job_id=render_job_id,
            project_id=project_id,
            workspace_id=workspace_id,
            priority=priority,
        )
        existing = registry.get_job(job_id)
        if existing is not None and existing.status in ACTIVE_QUEUE_JOB_STATUSES:
            results.append((str(render_job_id), str(workspace_id), "queued"))
            continue
        if apply:
            queue.enqueue_render(
                render_job_id=render_job_id,
                project_id=project_id,
                workspace_id=workspace_id,
                priority=priority,
                render_engine=render_engine,
                compute_tier=compute_tier,
            )
        results.append((str(render_job_id), str(workspace_id), "requeued" if apply else "orphaned"))
    return results


def _queued_renders(
    *, supabase_url: str, service_role_key: str, older_than: timedelta, limit: int
) -> list[dict[str, Any]]:
    cutoff = (datetime.now(UTC) - older_than).isoformat()
    response = httpx.get(
        f"{supabase_url.rstrip('/')}/rest/v1/render_jobs",
        headers={"apikey": service_role_key, "authorization": f"Bearer {service_role_key}"},
        params={
            "status": "eq.queued",
            "workflow_run_id": "is.null",
            "updated_at": f"lt.{cutoff}",
            "select": "id,workspace_id,project_id,render_engine,manifest",
            "order": "updated_at.asc",
            "limit": str(limit),
        },
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json()
    return rows if isinstance(rows, list) else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--older-than-minutes", type=int, default=30)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--apply", action="store_true", help="re-queue the orphaned renders")
    args = parser.parse_args(argv)
    if args.older_than_minutes < 5 or not 1 <= args.limit <= 5000:
        print("--older-than-minutes must be >= 5 and --limit 1..5000", file=sys.stderr)
        return 2
    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_service_role_key and settings.redis_url):
        print(
            "SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and REDIS_URL must be configured.",
            file=sys.stderr,
        )
        return 2
    rows = _queued_renders(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
        older_than=timedelta(minutes=args.older_than_minutes),
        limit=args.limit,
    )
    registry = BullMQQueueRegistry(
        redis_url=settings.redis_url,
        bridge_path=settings.bullmq_bridge_path,
        node_command=settings.bullmq_node_command,
        job_attempts=settings.job_attempts,
        bridge_timeout_seconds=settings.bullmq_bridge_timeout_seconds,
    )
    try:
        for render_job_id, workspace_id, action in classify_orphans(
            rows, registry, apply=args.apply
        ):
            print(f"{render_job_id}\t{workspace_id}\t{action}")
    finally:
        registry.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
