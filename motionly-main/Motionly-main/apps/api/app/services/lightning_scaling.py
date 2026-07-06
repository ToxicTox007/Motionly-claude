from __future__ import annotations

import math
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

from app.services.gpu_autoscaling import autoscale_decision, policy_from_settings
from app.services.queue import QueueSnapshot


@dataclass(frozen=True)
class LightningDeploymentSpec:
    name: str
    queues: tuple[str, ...]


@dataclass
class LightningDeploymentState:
    desired_replicas: int = 0
    last_scaled_at: datetime | None = None


class LightningScalingService:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.teamspace = settings.lightning_teamspace
        self.enabled = bool(
            settings.lightning_user_id
            and settings.lightning_api_key
            and settings.lightning_teamspace
        )
        self.policy = policy_from_settings(settings)
        self.specs = [
            LightningDeploymentSpec(
                name=settings.lightning_visual_deployment,
                queues=("visual_analysis", "transcription"),
            ),
            LightningDeploymentSpec(
                name=settings.lightning_matte_deployment,
                queues=("matte_extraction",),
            ),
            LightningDeploymentSpec(
                name=settings.lightning_render_deployment,
                queues=("render_gpu",),
            ),
        ]
        self._state = {
            spec.name: LightningDeploymentState() for spec in self.specs if spec.name
        }

    def run(self, snapshots: list[QueueSnapshot]) -> dict[str, object]:
        if not self.enabled:
            return {"status": "disabled", "reason": "lightning_credentials_missing"}

        now = datetime.now(UTC)
        by_queue = {snapshot.queue_name: snapshot for snapshot in snapshots}
        results = []

        for spec in self.specs:
            if not spec.name:
                continue

            state = self._state.setdefault(spec.name, LightningDeploymentState())
            queued = sum(
                by_queue.get(queue, QueueSnapshot(queue, 0, 0, 0, 0, 0, 0, None)).queued
                for queue in spec.queues
            )
            running = sum(
                by_queue.get(queue, QueueSnapshot(queue, 0, 0, 0, 0, 0, 0, None)).running
                for queue in spec.queues
            )

            decision = autoscale_decision(
                policy=self.policy,
                queue_depth=queued,
                current_workers=state.desired_replicas,
                last_scaled_at=state.last_scaled_at,
                now=now,
            )

            desired = state.desired_replicas
            if decision == "hold_cooldown":
                desired = state.desired_replicas
            elif queued > 0 or running > 0:
                desired = self._desired_replicas_for_backlog(queued=queued, running=running)
            elif decision == "scale_down":
                desired = 0
            elif decision == "scale_up":
                desired = min(
                    self.policy.max_worker_count,
                    max(1, state.desired_replicas + 1),
                )

            changed = desired != state.desired_replicas
            if changed:
                self._set_deployment_replicas(spec.name, desired)
                state.desired_replicas = desired
                state.last_scaled_at = now

            results.append(
                {
                    "deployment": spec.name,
                    "queues": list(spec.queues),
                    "queued": queued,
                    "running": running,
                    "decision": decision,
                    "desired_replicas": desired,
                    "changed": changed,
                    "last_scaled_at": (
                        state.last_scaled_at.isoformat() if state.last_scaled_at else None
                    ),
                }
            )

        return {"status": "ok", "teamspace": self.teamspace, "deployments": results}

    def _desired_replicas_for_backlog(self, *, queued: int, running: int) -> int:
        capacity = max(1, self.policy.max_concurrent_jobs_per_gpu)
        backlog = max(0, queued) + max(0, running)
        desired = max(1, math.ceil(backlog / capacity))
        return min(self.policy.max_worker_count, desired)

    def _set_deployment_replicas(self, name: str, replicas: int) -> None:
        env = {
            "LIGHTNING_USER_ID": self.settings.lightning_user_id or "",
            "LIGHTNING_API_KEY": self.settings.lightning_api_key or "",
        }
        command = [
            "lightning",
            "deployment",
            "update",
            name,
            "--teamspace",
            self.teamspace,
            "--min-replicas",
            str(replicas),
            "--max-replicas",
            str(replicas),
        ]
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )
