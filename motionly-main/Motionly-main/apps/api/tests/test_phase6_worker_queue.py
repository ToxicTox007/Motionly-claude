import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.broll import generate_broll
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.core.config import get_settings
from app.schemas.broll import BrollGenerateRequest
from app.services.job_engine import run_queue_job
from app.services.queue import BrollQueue, QueueRegistry, StockAssetEnrichmentQueue
from app.services.queue_guard import require_worker_queue
from app.services.stock_video import FixtureStockVideoProvider
from tests.test_broll import make_broll_repository, make_creator_billing_repository


def test_production_blocks_force_local_media_storage(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.example.com")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
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
                    worker_api_token="worker-secret",
                ),
                queue_registry=registry,
                broll_repository=repository,
                billing_repository=billing_repository,
                stock_video_providers=[provider],
                storage_signer=SimpleNamespace(),
                stock_asset_enrichment_queue=StockAssetEnrichmentQueue(registry),
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
                            worker_api_token="worker-secret",
                        ),
                        queue_registry=registry,
                        broll_repository=repository,
                        billing_repository=billing_repository,
                        stock_video_providers=[provider],
                        storage_signer=SimpleNamespace(),
                        stock_asset_enrichment_queue=StockAssetEnrichmentQueue(registry),
                    )
                )
            ),
            x_motionly_worker_token="worker-secret",
        )
    )

    assert provider.search_calls > 0
    slots = repository.list_slots(project_id=project_id, workspace_id=workspace_id)
    assert slots


@pytest.mark.parametrize(
    ("field", "value", "expected_name"),
    [
        ("visual_intelligence_provider", "mystery_visual", "VISUAL_PROVIDER"),
    ],
)
def test_production_worker_rejects_unknown_ai_provider(
    field: str,
    value: str,
    expected_name: str,
) -> None:
    from app.main import _validate_production_settings

    settings = get_settings().model_copy(
        update={
            "app_env": "production",
            "process_role": "worker",
            "supabase_url": "https://supabase.example.com",
            "supabase_public_url": "https://app.example.com/supabase",
            "supabase_service_role_key": "service-role",
            "redis_url": "redis://redis.example.com:6379",
            "worker_api_token": "worker-secret",
            "allow_fixture_providers": False,
            "stem_separation_provider": "demucs",
            "visual_intelligence_provider": "local_ml",
            "audio_ml_provider": "local_ml",
            "music_provider": "licensed_catalog",
            "stock_video_provider": "pixabay",
            "pixabay_api_key": "pixabay-key",
            field: value,
        }
    )

    with pytest.raises(RuntimeError, match=expected_name):
        _validate_production_settings(settings)


@pytest.mark.parametrize(
    "registry_fixture",
    [
        "production_missing_transcription.json",
    ],
)
def test_production_worker_rejects_registry_missing_a_required_capability(
    registry_fixture: str,
) -> None:
    """Task 7: production validation is capability-driven, not vendor-key-driven.

    The legacy per-vendor auth-key requirements are gone. The registry-driven
    equivalent is that production must have a valid enabled route for every
    required capability (planning.chat and transcription.speech_to_text); a
    registry missing transcription fails startup with a clear
    InferenceStartupError.
    """
    from pathlib import Path

    from app.main import _validate_production_settings
    from app.services.inference_validation import InferenceStartupError

    root = Path(__file__).resolve().parents[3]
    settings = get_settings().model_copy(
        update={
            "app_env": "production",
            "process_role": "worker",
            "supabase_url": "https://supabase.example.com",
            "supabase_public_url": "https://app.example.com/supabase",
            "supabase_service_role_key": "service-role",
            "redis_url": "redis://redis.example.com:6379",
            "worker_api_token": "worker-secret",
            "allow_fixture_providers": False,
            "stem_separation_provider": "demucs",
            "visual_intelligence_provider": "local_ml",
            "audio_ml_provider": "local_ml",
            "music_provider": "licensed_catalog",
            "stock_video_provider": "pixabay",
            "pixabay_api_key": "pixabay-key",
            "ai_registry_file": str(
                root / "apps/api/tests/fixtures/inference_registry" / registry_fixture
            ),
            "ai_secrets_bundle_file": str(
                root / "apps/api/tests/fixtures/inference_registry/bundle.json"
            ),
        }
    )

    with pytest.raises(InferenceStartupError, match="transcription.speech_to_text"):
        _validate_production_settings(settings)
