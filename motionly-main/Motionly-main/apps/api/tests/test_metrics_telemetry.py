import pytest
from types import SimpleNamespace
from uuid import uuid4
from fastapi import Request
from app.services.operations import MetricsRegistry, OperationsRepository
from app.api.operations import metrics
from app.services.queue import QueueRegistry, QueueJobStatus, EnqueuedJob


class DummyApp:
    def __init__(self, settings, queue_registry, operations_repository, metrics_registry):
        self.state = SimpleNamespace(
            settings=settings,
            queue_registry=queue_registry,
            operations_repository=operations_repository,
            metrics_registry=metrics_registry,
        )


@pytest.mark.anyio
async def test_metrics_telemetry_rendering() -> None:
    settings = SimpleNamespace(metrics_bearer_token=None, app_env="test")
    queue_registry = QueueRegistry()
    ops_repo = OperationsRepository()
    metrics_reg = MetricsRegistry()
    
    # Observe some sample metrics
    metrics_reg.observe("job_duration_ms", 120, kind="plan", status="succeeded")
    metrics_reg.observe("job_retry_count", 2, kind="plan")
    metrics_reg.observe("job_failure_count", 1, kind="plan", error_code="ValueError")
    metrics_reg.observe("fallback_degraded_mode_count", 1, provider="openrouter", model="openrouter/free", reason="edit_plan_fallback")
    
    # Seed a cost event
    workspace_id = uuid4()
    ops_repo.record_cost(
        workspace_id=workspace_id,
        project_id=uuid4(),
        provider="openrouter",
        category="tokens",
        quantity=5000,
        unit="token",
        unit_cost_usd=0.000002,
    )
    
    # Render metrics
    request = SimpleNamespace(
        app=DummyApp(
            settings=settings,
            queue_registry=queue_registry,
            operations_repository=ops_repo,
            metrics_registry=metrics_reg,
        )
    )
    
    output = await metrics(request=request)
    
    # Assert Promethean formatted metric outputs
    assert "motionly_job_duration_ms_count{kind=\"plan\",status=\"succeeded\"} 1" in output
    assert "motionly_job_retry_count_count{kind=\"plan\"} 1" in output
    assert "motionly_job_failure_count_count{error_code=\"ValueError\",kind=\"plan\"} 1" in output
    assert "motionly_fallback_degraded_mode_count_count{model=\"openrouter/free\",provider=\"openrouter\",reason=\"edit_plan_fallback\"} 1" in output
    assert f'motionly_cost_usd{{workspace_id="{workspace_id}",provider="openrouter"}} 0.010000' in output
