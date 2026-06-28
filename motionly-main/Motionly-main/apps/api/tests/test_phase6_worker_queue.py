import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.broll import generate_broll
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.internal_jobs import run_queue_job
from app.core.config import get_settings
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
from app.schemas.billing import BillingPlanKey, SubscriptionStatus
from app.schemas.broll import BrollGenerateRequest
from app.services.queue import BrollQueue, QueueRegistry
from app.services.queue_guard import require_worker_queue
from app.services.stock_video import FixtureStockVideoProvider
from tests.test_broll import make_broll_repository, make_creator_billing_repository


def test_production_blocks_publish_sync_completion(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLISH_SYNC_COMPLETION", "true")
    get_settings.cache_clear()

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    assert "PUBLISH_SYNC_COMPLETION" in str(exc_info.value)


def test_production_blocks_force_local_media_storage(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("FORCE_LOCAL_MEDIA_STORAGE", "true")
    get_settings.cache_clear()

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    assert "FORCE_LOCAL_MEDIA_STORAGE" in str(exc_info.value)


def test_production_requires_bullmq_registry(monkeypatch) -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(app_env="production", redis_url=None),
                queue_registry=QueueRegistry(),
            )
        )
    )
    with pytest.raises(ApiError) as exc_info:
        require_worker_queue(request)
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "queue_unavailable"


def test_generate_broll_enqueues_and_worker_executes_stock_search() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()
    registry = QueueRegistry()
    billing_repository = make_creator_billing_repository(workspace_id)

    class SpyProvider(FixtureStockVideoProvider):
        def __init__(self) -> None:
            super().__init__()
            self.search_calls = 0

        def search(self, **kwargs):
            self.search_calls += 1
            return super().search(**kwargs)

    provider = SpyProvider()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env="test",
                    billing_enabled=True,
                    redis_url=None,
                    admin_api_token="worker-secret",
                ),
                queue_registry=registry,
                broll_repository=repository,
                billing_repository=billing_repository,
                stock_video_providers=[provider],
                storage_signer=SimpleNamespace(),
            )
        )
    )

    class DummyResponse:
        status_code = 200

    response = asyncio.run(
        generate_broll(
            project_id=project_id,
            payload=BrollGenerateRequest(force=True),
            request=request,
            response=DummyResponse(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=billing_repository,
            broll_queue=BrollQueue(registry=registry),
            providers=[provider],
        )
    )

    assert response.status == "queued"
    assert provider.search_calls == 0
    assert response.status_url == f"/projects/{project_id}/jobs/{response.job_id}"

    asyncio.run(
        run_queue_job(
            queue_job_id=response.job_id,
            request=SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        settings=SimpleNamespace(
                            app_env="test",
                            billing_enabled=True,
                            admin_api_token="worker-secret",
                        ),
                        queue_registry=registry,
                        broll_repository=repository,
                        billing_repository=billing_repository,
                        stock_video_providers=[provider],
                        storage_signer=SimpleNamespace(),
                    )
                )
            ),
            x_motionly_worker_token="worker-secret",
        )
    )

    assert provider.search_calls > 0
    slots = repository.list_slots(project_id=project_id, workspace_id=workspace_id)
    assert slots
