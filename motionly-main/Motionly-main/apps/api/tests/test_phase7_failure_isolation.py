import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.api.dependencies import Actor
from app.api.health import dependencies
from app.core.middleware import InMemoryRateLimiter
from app.services.transcription_jobs import _run_audio_ml_providers


# 4. audio ML provider failure does not fail transcription job
def test_audio_ml_provider_failure_isolation() -> None:
    class FailingProvider:
        def diarize(self, *, audio_path):
            raise RuntimeError("Diarization failed!")
        def classify_windows(self, *, audio_path):
            raise RuntimeError("Emotion failed!")
        def classify(self, *, audio_path):
            raise RuntimeError("Yamnet failed!")

    providers = {
        "speaker_diarization": FailingProvider(),
        "emotion": FailingProvider(),
        "sound": FailingProvider()
    }
    
    insights = _run_audio_ml_providers(
        providers=providers,
        audio_path=Path(__file__)
    )
    
    # Assert it returns well-formed degraded states for each failing provider instead of crashing
    assert "speaker_diarization" in insights
    assert insights["speaker_diarization"]["speaker_count"] == 0
    assert insights["speaker_diarization"]["degraded"] is True
    
    assert "emotion_windows" in insights
    assert insights["emotion_windows"] == []
    
    assert "sound_classification" in insights
    assert insights["sound_classification"]["degraded"] is True
    assert insights["sound_classification"]["labels"] == []

def test_health_dependencies_endpoint_with_audio_ml(monkeypatch) -> None:
    class MockAudioMLProvider:
        def check_availability(self):
            return True, "available"

    class MockAudioMLProviderDown:
        def check_availability(self):
            return False, "dependency_missing"

    class MockState:
        def __init__(self):
            self.settings = SimpleNamespace(
                app_env="test",
                gpu_stage="enabled",
                redis_url=None,
                supabase_url=None,
                supabase_service_role_key=None,
                remotion_enabled=False,
                worker_api_token=None,
                health_diagnostics_rate_limit_per_minute=10,
                health_diagnostics_cache_ttl_seconds=0,
            )
            self.queue_registry = SimpleNamespace(
                snapshots=lambda: []
            )
            self.rate_limiter = InMemoryRateLimiter()
            self.audio_ml_providers = {
                "emotion": MockAudioMLProvider(),
                "speaker_diarization": MockAudioMLProviderDown()
            }

    request = SimpleNamespace(
        app=SimpleNamespace(state=MockState()),
        state=SimpleNamespace(request_id="req_test_dependencies")
    )

    async def fake_supabase_health(*args, **kwargs):
        from app.api.health import DependencyHealth
        return DependencyHealth(status="ok", message="Mocked Supabase.")
    monkeypatch.setattr("app.api.health._supabase_health", fake_supabase_health)

    actor = Actor(user_id=uuid4(), workspace_id=uuid4())
    response = asyncio.run(dependencies(request, actor))

    assert "audio_ml" in response.dependencies
    assert response.dependencies["audio_ml"].status == "degraded"
    assert response.dependencies["audio_ml"].metadata["emotion"] == {
        "available": True,
        "status": "available",
    }
    assert response.dependencies["audio_ml"].metadata["speaker_diarization"] == {
        "available": False,
        "status": "dependency_missing",
    }
