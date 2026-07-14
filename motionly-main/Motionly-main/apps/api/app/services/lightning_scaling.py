import math
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from app.services.gpu_autoscaling import policy_from_settings
from app.services.queue import QueueSnapshot


class LightningScalingService:
    """Scale specialized GPU deployments from BullMQ backlog snapshots."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.policy = policy_from_settings(settings)
        self._replicas: dict[str, int] = {}
        self._last_scaled_at: dict[str, datetime] = {}

    @staticmethod
    def is_configured(settings) -> bool:
        required = (
            "lightning_user_id",
            "lightning_api_key",
            "lightning_teamspace",
            "lightning_visual_deployment",
            "lightning_matte_deployment",
            "lightning_render_deployment",
        )
        return all(bool(getattr(settings, name, None)) for name in required)

    def run(
        self,
        snapshots: Iterable[QueueSnapshot],
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        if not self.is_configured(self.settings):
            return {"status": "disabled", "reason": "lightning_not_configured"}

        captured_at = now or datetime.now(UTC)
        by_queue = {snapshot.queue_name: snapshot for snapshot in snapshots}
        deployment_specs = (
            (
                str(self.settings.lightning_visual_deployment),
                ("visual_analysis", "transcription"),
            ),
            (str(self.settings.lightning_matte_deployment), ("matte_extraction",)),
            (str(self.settings.lightning_render_deployment), ("render_gpu",)),
        )
        deployments = [
            self._scale_deployment(
                deployment=deployment,
                queues=queues,
                snapshots=by_queue,
                now=captured_at,
            )
            for deployment, queues in deployment_specs
        ]
        return {
            "status": "ok",
            "captured_at": captured_at.isoformat(),
            "deployments": deployments,
        }

    def _scale_deployment(
        self,
        *,
        deployment: str,
        queues: tuple[str, ...],
        snapshots: dict[str, QueueSnapshot],
        now: datetime,
    ) -> dict[str, object]:
        backlog = sum(
            max(0, snapshot.queued) + max(0, snapshot.running)
            for queue in queues
            if (snapshot := snapshots.get(queue)) is not None
        )
        desired = min(
            self.policy.max_worker_count,
            math.ceil(backlog / self.policy.max_concurrent_jobs_per_gpu),
        )
        current = self._replicas.get(deployment, 0)
        last_scaled_at = self._last_scaled_at.get(deployment)
        in_cooldown = (
            last_scaled_at is not None
            and (now - last_scaled_at).total_seconds() < self.policy.cooldown_seconds
        )

        if desired == current:
            decision = "hold"
        elif in_cooldown:
            decision = "hold_cooldown"
            desired = current
        else:
            decision = "scale_up" if desired > current else "scale_down"
            self._set_deployment_replicas(deployment, desired)
            self._replicas[deployment] = desired
            self._last_scaled_at[deployment] = now

        return {
            "deployment": deployment,
            "queues": list(queues),
            "backlog": backlog,
            "current_replicas": current,
            "desired_replicas": desired,
            "decision": decision,
        }

    def _set_deployment_replicas(self, name: str, replicas: int) -> None:
        template = str(
            getattr(
                self.settings,
                "lightning_scale_url_template",
                "https://lightning.ai/api/v1/teamspaces/{teamspace}/deployments/{deployment}",
            )
        )
        url = template.format(
            teamspace=quote(str(self.settings.lightning_teamspace), safe=""),
            deployment=quote(name, safe=""),
        )
        response = httpx.patch(
            url,
            headers={
                "x-lightning-user-id": str(self.settings.lightning_user_id),
                "x-lightning-api-key": str(self.settings.lightning_api_key),
                "content-type": "application/json",
            },
            json={"replicas": replicas},
            timeout=15,
        )
        response.raise_for_status()
