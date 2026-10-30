from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import Settings, get_settings
from app.core.production import validate_production_settings
from app.orchestration.activities import BullMQActivities, QueueRegistryProtocol
from app.orchestration.client import TemporalConnectionConfig, connect_temporal_client
from app.orchestration.proposal_activities import WorkflowProposalActivities
from app.orchestration.render_activities import WorkflowRenderActivities
from app.orchestration.workflow import MotionlyWorkflow
from app.repositories.workflows import SupabaseWorkflowRepository
from app.services.queue import BullMQQueueRegistry, QueueRegistry


def create_worker(
    *,
    client: Client,
    registry: QueueRegistry | BullMQQueueRegistry | QueueRegistryProtocol,
    task_queue: str = "motionly-orchestration",
    max_concurrent_activities: int = 100,
    max_concurrent_workflow_tasks: int = 100,
    workflow_repository: SupabaseWorkflowRepository | None = None,
    proposal_activities: WorkflowProposalActivities | None = None,
    render_activities: WorkflowRenderActivities | None = None,
) -> Worker:
    if not task_queue:
        raise ValueError("Temporal task queue is required")
    activities = BullMQActivities(registry, workflow_repository=workflow_repository)
    proposal_activities = proposal_activities or WorkflowProposalActivities(
        api_base_url="",
        worker_token="",
    )
    render_activities = render_activities or WorkflowRenderActivities(
        api_base_url="",
        worker_token="",
    )
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[MotionlyWorkflow],
        activities=[
            activities.enqueue_bullmq_step,
            activities.fail_workflow_run,
            proposal_activities.ensure_workflow_proposal,
            render_activities.ensure_workflow_draft_render,
        ],
        max_concurrent_activities=max(1, min(max_concurrent_activities, 1000)),
        max_concurrent_workflow_tasks=max(1, min(max_concurrent_workflow_tasks, 1000)),
        graceful_shutdown_timeout=timedelta(seconds=30),
    )


async def run_worker(settings: Settings) -> None:
    config = TemporalConnectionConfig.from_settings(settings)
    client = await connect_temporal_client(config)
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required for Temporal BullMQ activities")
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    workflow_repository = SupabaseWorkflowRepository(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
    )
    registry = BullMQQueueRegistry(
        redis_url=settings.redis_url,
        bridge_path=settings.bullmq_bridge_path,
        node_command=settings.bullmq_node_command,
        job_attempts=settings.job_attempts,
        bridge_timeout_seconds=settings.bullmq_bridge_timeout_seconds,
    )
    worker = create_worker(
        client=client,
        registry=registry,
        task_queue=config.task_queue,
        max_concurrent_activities=settings.temporal_max_concurrent_activities,
        max_concurrent_workflow_tasks=settings.temporal_max_concurrent_workflow_tasks,
        workflow_repository=workflow_repository,
        proposal_activities=WorkflowProposalActivities(
            api_base_url=settings.worker_api_base_url or "",
            worker_token=settings.worker_api_token or "",
        ),
        render_activities=WorkflowRenderActivities(
            api_base_url=settings.worker_api_base_url or "",
            worker_token=settings.worker_api_token or "",
        ),
    )
    try:
        await worker.run()
    finally:
        registry.close()


def main() -> None:
    settings = get_settings()
    validate_production_settings(settings)
    if settings.process_role.strip().lower() != "temporal-worker":
        raise RuntimeError("PROCESS_ROLE must be temporal-worker for this entrypoint")
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
