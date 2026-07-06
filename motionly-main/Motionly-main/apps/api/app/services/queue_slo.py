from dataclasses import dataclass
from typing import Protocol

from app.api.errors import ApiError


@dataclass(frozen=True)
class QueueSlo:
    queued_warn: int
    queued_reject: int
    oldest_warn_seconds: int
    oldest_reject_seconds: int


class QueueSnapshotLike(Protocol):
    queue_name: str
    queued: int
    oldest_queued_seconds: int | None


DEFAULT_QUEUE_SLO = QueueSlo(
    queued_warn=500,
    queued_reject=5_000,
    oldest_warn_seconds=300,
    oldest_reject_seconds=1_800,
)

QUEUE_SLOS: dict[str, QueueSlo] = {
    "upload_preprocess": QueueSlo(
        queued_warn=200,
        queued_reject=2_000,
        oldest_warn_seconds=120,
        oldest_reject_seconds=900,
    ),
    "transcription": QueueSlo(
        queued_warn=200,
        queued_reject=2_000,
        oldest_warn_seconds=300,
        oldest_reject_seconds=1_800,
    ),
    "visual_analysis": QueueSlo(
        queued_warn=100,
        queued_reject=1_000,
        oldest_warn_seconds=300,
        oldest_reject_seconds=1_800,
    ),
    "matte_extraction": QueueSlo(
        queued_warn=50,
        queued_reject=500,
        oldest_warn_seconds=300,
        oldest_reject_seconds=1_800,
    ),
    "planning": QueueSlo(
        queued_warn=300,
        queued_reject=3_000,
        oldest_warn_seconds=180,
        oldest_reject_seconds=1_200,
    ),
    "stock_search": QueueSlo(
        queued_warn=300,
        queued_reject=3_000,
        oldest_warn_seconds=180,
        oldest_reject_seconds=1_200,
    ),
    "stock_asset_enrichment": QueueSlo(
        queued_warn=300,
        queued_reject=3_000,
        oldest_warn_seconds=300,
        oldest_reject_seconds=1_800,
    ),
    "audio": QueueSlo(
        queued_warn=200,
        queued_reject=2_000,
        oldest_warn_seconds=180,
        oldest_reject_seconds=1_200,
    ),
    "render": QueueSlo(
        queued_warn=150,
        queued_reject=1_500,
        oldest_warn_seconds=300,
        oldest_reject_seconds=1_800,
    ),
    "render_gpu": QueueSlo(
        queued_warn=50,
        queued_reject=500,
        oldest_warn_seconds=300,
        oldest_reject_seconds=1_800,
    ),
    "render_remotion": QueueSlo(
        queued_warn=75,
        queued_reject=750,
        oldest_warn_seconds=300,
        oldest_reject_seconds=1_800,
    ),
    "publish": QueueSlo(
        queued_warn=200,
        queued_reject=2_000,
        oldest_warn_seconds=300,
        oldest_reject_seconds=1_800,
    ),
    "intelligence": QueueSlo(
        queued_warn=200,
        queued_reject=2_000,
        oldest_warn_seconds=300,
        oldest_reject_seconds=1_800,
    ),
    "audio_demucs": QueueSlo(
        queued_warn=50,
        queued_reject=500,
        oldest_warn_seconds=300,
        oldest_reject_seconds=1_800,
    ),
    "provider_calls": QueueSlo(
        queued_warn=300,
        queued_reject=3_000,
        oldest_warn_seconds=180,
        oldest_reject_seconds=1_200,
    ),
    "maintenance": QueueSlo(
        queued_warn=100,
        queued_reject=1_000,
        oldest_warn_seconds=600,
        oldest_reject_seconds=3_600,
    ),
}


def queue_slo_for(queue_name: str) -> QueueSlo:
    return QUEUE_SLOS.get(queue_name, DEFAULT_QUEUE_SLO)


def queue_slo_state(snapshot: QueueSnapshotLike) -> dict[str, int | str]:
    slo = queue_slo_for(snapshot.queue_name)
    oldest = snapshot.oldest_queued_seconds or 0
    return {
        "queue": snapshot.queue_name,
        "queued_warn": slo.queued_warn,
        "queued_reject": slo.queued_reject,
        "oldest_warn_seconds": slo.oldest_warn_seconds,
        "oldest_reject_seconds": slo.oldest_reject_seconds,
        "queued_breach": int(snapshot.queued >= slo.queued_warn),
        "oldest_breach": int(oldest >= slo.oldest_warn_seconds),
        "backpressure": int(
            snapshot.queued >= slo.queued_reject or oldest >= slo.oldest_reject_seconds
        ),
    }


def raise_if_queue_backpressured(registry: object, queue_name: str) -> None:
    snapshots = getattr(registry, "snapshots", None)
    if snapshots is None:
        return
    try:
        current = snapshots()
    except Exception:
        return
    snapshot = next((item for item in current if item.queue_name == queue_name), None)
    if snapshot is None:
        return
    state = queue_slo_state(snapshot)
    if not state["backpressure"]:
        return
    raise ApiError(
        429,
        "queue_backpressure",
        "This queue is overloaded. The job was not enqueued; try again shortly.",
    )
