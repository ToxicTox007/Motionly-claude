"""Audit WJ-01 cleanup: re-queue renders orphaned by the rejected job ids."""

from __future__ import annotations

from uuid import uuid4

from app.ops.requeue_orphaned_renders import classify_orphans
from app.services.queue import QueueRegistry, RenderQueue


def _row(**overrides):
    row = {
        "id": str(uuid4()),
        "workspace_id": str(uuid4()),
        "project_id": str(uuid4()),
        "render_engine": "ffmpeg_basic",
        "manifest": {"compute_tier": "cpu", "billing": {"priority_queue": True}},
    }
    row.update(overrides)
    return row


def test_report_mode_lists_orphans_without_enqueueing() -> None:
    registry = QueueRegistry()
    orphan = _row()
    results = classify_orphans([orphan], registry, apply=False)
    assert results == [(orphan["id"], orphan["workspace_id"], "orphaned")]
    assert registry.jobs == {}


def test_apply_mode_requeues_with_the_create_path_priority_and_queue() -> None:
    registry = QueueRegistry()
    orphan = _row(render_engine="remotion_premium")
    results = classify_orphans([orphan], registry, apply=True)
    assert results == [(orphan["id"], orphan["workspace_id"], "requeued")]
    [job] = registry.jobs.values()
    assert job.queue_name == "render_remotion"
    assert job.priority == 10
    assert ":" not in job.id and job.id.endswith("--p10")


def test_renders_with_a_live_job_are_left_alone() -> None:
    registry = QueueRegistry()
    live = _row(manifest={})
    RenderQueue(registry).enqueue_render(
        render_job_id=live["id"],
        project_id=live["project_id"],
        workspace_id=live["workspace_id"],
    )
    assert classify_orphans([live], registry, apply=True) == [
        (live["id"], live["workspace_id"], "queued")
    ]
    assert len(registry.jobs) == 1
