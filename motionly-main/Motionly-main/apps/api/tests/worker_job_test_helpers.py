import asyncio
from types import SimpleNamespace
from uuid import UUID

from app.api.dependencies import Actor
from app.api.internal_jobs import run_queue_job
from app.api.plans import create_plan
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.ai_proposals import ProposalDecisionRequest, ProposalDecisionType
from app.schemas.plans import PlanCreateRequest
from app.services.edit_planner import FixtureEditPlannerProvider
from app.services.queue import PlanQueue, QueueRegistry


class DummyResponse:
    def __init__(self) -> None:
        self.status_code = 200


def build_queue_request_state(
    *,
    registry: QueueRegistry,
    settings: SimpleNamespace | None = None,
    **repositories: object,
) -> SimpleNamespace:
    settings = settings or SimpleNamespace(
        app_env="test",
        billing_enabled=False,
        redis_url=None,
        worker_api_token="worker-secret",
    )
    state = {"settings": settings, "queue_registry": registry, **repositories}
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


def seed_active_plan(
    *,
    plan_repository: InMemoryPlanRepository,
    project_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    payload: PlanCreateRequest | None = None,
    registry: QueueRegistry | None = None,
) -> None:
    registry = registry or QueueRegistry()
    request = build_queue_request_state(
        registry=registry,
        plan_repository=plan_repository,
        edit_planner_provider=FixtureEditPlannerProvider(),
        operations_repository=None,
    )
    response = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=payload or PlanCreateRequest(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=plan_repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(registry=registry),
            response=DummyResponse(),
            request=request,
        )
    )
    asyncio.run(
        run_queue_job(
            queue_job_id=response.job_id,
            request=request,
            x_motionly_worker_token="worker-secret",
        )
    )
    proposal_repository = plan_repository._proposal_repository
    proposal = proposal_repository.get(
        proposal_id=response.proposal_id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    proposal_repository.decide(
        proposal_id=proposal.id,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=ProposalDecisionRequest(
            decision=ProposalDecisionType.APPROVE,
            expected_proposal_revision=proposal.revision,
            expected_base_active_plan_id=proposal.artifacts.base_active_plan_id,
            expected_base_active_plan_revision=proposal.artifacts.base_active_plan_revision,
            idempotency_key=f"seed-plan-{proposal.id}",
        ),
    )


async def run_enqueued_job(
    *,
    job_id: str,
    request: SimpleNamespace,
) -> None:
    await run_queue_job(
        queue_job_id=job_id,
        request=request,
        x_motionly_worker_token="worker-secret",
    )
