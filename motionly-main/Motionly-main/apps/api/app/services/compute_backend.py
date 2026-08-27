"""Static Compose GPU pool capacity observer.

Motionly places visual intelligence, matte extraction, and GPU rendering on a
fixed set of queue-specific worker services defined in
``infra/docker-compose.gpu.yml``. This module observes that static capacity for
metrics and backpressure; it never calls a remote orchestration API.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.services.gpu_autoscaling import policy_from_settings
from app.services.queue import QueueSnapshot

STATIC_GPU_POOL_BACKEND = "static_gpu_pool"

# Keep this aligned with the GPU worker services and queue-routing contract tests.
GPU_POOL_LAYOUT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("visual", ("visual_analysis", "transcription")),
    ("matte", ("matte_extraction",)),
    ("render", ("render_gpu",)),
)


@dataclass(frozen=True)
class GpuPoolSpec:
    """Static replica capacity for each Compose GPU worker group."""

    visual_replicas: int
    matte_replicas: int
    render_replicas: int

    def replicas_for(self, pool: str) -> int:
        return {
            "visual": self.visual_replicas,
            "matte": self.matte_replicas,
            "render": self.render_replicas,
        }.get(pool, 0)


def _positive_int(value: object, *, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def gpu_pool_spec_from_settings(settings: object) -> GpuPoolSpec:
    return GpuPoolSpec(
        visual_replicas=_positive_int(
            getattr(settings, "gpu_pool_visual_replicas", 1),
            default=1,
        ),
        matte_replicas=_positive_int(
            getattr(settings, "gpu_pool_matte_replicas", 1),
            default=1,
        ),
        render_replicas=_positive_int(
            getattr(settings, "gpu_pool_render_replicas", 1),
            default=1,
        ),
    )


class ComputeBackend(ABC):
    """Static compute capacity observation contract."""

    backend_name: str = "compute_backend"

    @staticmethod
    @abstractmethod
    def is_configured(settings: object) -> bool:
        """Return whether the static pool has at least one worker."""

    @abstractmethod
    def run(
        self,
        snapshots: Iterable[QueueSnapshot],
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Observe queue backlog against static Compose capacity."""


class StaticGpuPoolBackend(ComputeBackend):
    """Fixed queue-specific GPU worker capacity operated through Compose."""

    backend_name = STATIC_GPU_POOL_BACKEND

    def __init__(self, settings: object) -> None:
        self.settings = settings
        self.policy = policy_from_settings(settings)
        self.pool = gpu_pool_spec_from_settings(settings)

    @staticmethod
    def is_configured(settings: object) -> bool:
        spec = gpu_pool_spec_from_settings(settings)
        return (spec.visual_replicas + spec.matte_replicas + spec.render_replicas) > 0

    def run(
        self,
        snapshots: Iterable[QueueSnapshot],
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        if not self.is_configured(self.settings):
            return {"status": "disabled", "reason": "static_gpu_pool_unconfigured"}

        captured_at = now or datetime.now(UTC)
        by_queue = {snapshot.queue_name: snapshot for snapshot in snapshots}
        jobs_per_replica = max(1, self.policy.max_concurrent_jobs_per_gpu)
        pools = []
        for pool_name, queues in GPU_POOL_LAYOUT:
            backlog = sum(
                max(0, snapshot.queued) + max(0, snapshot.running)
                for queue in queues
                if (snapshot := by_queue.get(queue)) is not None
            )
            replicas = self.pool.replicas_for(pool_name)
            capacity = replicas * jobs_per_replica
            desired_replicas = min(
                self.policy.max_worker_count,
                math.ceil(backlog / jobs_per_replica) if backlog else 0,
            )
            pools.append(
                {
                    "pool": pool_name,
                    "queues": list(queues),
                    "backlog": backlog,
                    "static_replicas": replicas,
                    "capacity": capacity,
                    "desired_replicas": desired_replicas,
                    "saturated": backlog > capacity,
                }
            )

        return {
            "status": "ok",
            "backend": self.backend_name,
            "captured_at": captured_at.isoformat(),
            "pools": pools,
        }


def compute_backend_from_settings(settings: object) -> StaticGpuPoolBackend | None:
    """Return the sole supported static Compose GPU capacity observer."""

    if StaticGpuPoolBackend.is_configured(settings):
        return StaticGpuPoolBackend(settings)
    return None
