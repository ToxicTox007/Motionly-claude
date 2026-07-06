from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class GpuAutoscalingPolicy:
    instance_type: str
    max_concurrent_jobs_per_gpu: int
    gpu_memory_ceiling_mb: int
    matte_worker_concurrency: int
    visual_worker_concurrency: int
    scale_up_queue_depth: int
    scale_down_queue_depth: int
    cooldown_seconds: int
    max_worker_count: int
    oom_alert_threshold: int
    long_job_timeout_seconds: int


def positive_int(value: object, *, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def policy_from_settings(settings) -> GpuAutoscalingPolicy:
    gpu_max_worker_count = positive_int(
        getattr(settings, "gpu_max_worker_count", 4),
        default=4,
    )
    lightning_max_replicas = positive_int(
        getattr(settings, "lightning_max_replicas", gpu_max_worker_count),
        default=gpu_max_worker_count,
    )
    return GpuAutoscalingPolicy(
        instance_type=str(getattr(settings, "gpu_worker_instance_type", "g5.xlarge")),
        max_concurrent_jobs_per_gpu=positive_int(
            getattr(settings, "gpu_max_concurrent_jobs_per_gpu", 1),
            default=1,
        ),
        gpu_memory_ceiling_mb=positive_int(
            getattr(settings, "gpu_memory_ceiling_mb", 16000),
            default=16000,
        ),
        matte_worker_concurrency=positive_int(
            getattr(settings, "matte_worker_concurrency", 1),
            default=1,
        ),
        visual_worker_concurrency=positive_int(
            getattr(settings, "visual_worker_concurrency", 1),
            default=1,
        ),
        scale_up_queue_depth=positive_int(
            getattr(settings, "gpu_scale_up_queue_depth", 4),
            default=4,
        ),
        scale_down_queue_depth=positive_int(
            getattr(settings, "gpu_scale_down_queue_depth", 1),
            default=1,
            minimum=0,
        ),
        cooldown_seconds=positive_int(
            getattr(settings, "gpu_autoscale_cooldown_seconds", 300),
            default=300,
        ),
        max_worker_count=min(gpu_max_worker_count, lightning_max_replicas),
        oom_alert_threshold=positive_int(
            getattr(settings, "gpu_oom_alert_threshold", 2),
            default=2,
        ),
        long_job_timeout_seconds=positive_int(
            getattr(settings, "gpu_long_job_timeout_seconds", 1800),
            default=1800,
        ),
    )


def autoscale_decision(
    *,
    policy: GpuAutoscalingPolicy,
    queue_depth: int,
    current_workers: int,
    last_scaled_at: datetime | None,
    now: datetime,
) -> str:
    if last_scaled_at is not None:
        elapsed = (now - last_scaled_at).total_seconds()
        if elapsed < policy.cooldown_seconds:
            return "hold_cooldown"
    if queue_depth >= policy.scale_up_queue_depth and current_workers < policy.max_worker_count:
        return "scale_up"
    if queue_depth <= policy.scale_down_queue_depth and current_workers > 0:
        return "scale_down"
    return "hold"


def should_timeout_job(
    *,
    started_at: datetime,
    now: datetime,
    policy: GpuAutoscalingPolicy,
) -> bool:
    return (now - started_at).total_seconds() >= policy.long_job_timeout_seconds


def oom_alert_event(*, oom_count: int, policy: GpuAutoscalingPolicy) -> dict[str, object] | None:
    if oom_count < policy.oom_alert_threshold:
        return None
    return {
        "event": "gpu_oom_threshold_exceeded",
        "severity": "warning",
        "oom_count": oom_count,
        "threshold": policy.oom_alert_threshold,
        "created_at": datetime.now(UTC).isoformat(),
    }
