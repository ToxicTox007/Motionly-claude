import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.health import dependencies
from app.repositories.captions import TranscriptRecord
from app.schemas.captions import LanguageHint, TranscriptWord
from app.schemas.plans import EditPlanStyle, PlanCreateRequest
from app.services.edit_planner import HttpEditPlannerProvider
from app.services.provider_resilience import ProviderCallFailure
from app.services.transcription_jobs import _run_audio_ml_providers
from app.services.transcription_provider import WhisperCompatibleHttpProvider


# 1. Groq retry test (transient error -> success)
def test_groq_whisper_retry_success(monkeypatch) -> None:
    calls = []
    
    def mock_post(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) < 2:
            # First attempt: transient 429
            response = httpx.Response(429, request=httpx.Request("POST", args[0]))
            raise httpx.HTTPStatusError(
                "Too Many Requests",
                request=response.request,
                response=response,
            )
        # Second attempt: success
        return httpx.Response(
            200,
            json={
                "language": "en",
                "confidence": 0.95,
                "words": [
                    {"word": "hello", "start": 0.1, "end": 0.5, "confidence": 0.9}
                ]
            },
            request=httpx.Request("POST", args[0])
        )

    monkeypatch.setattr("httpx.post", mock_post)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    provider = WhisperCompatibleHttpProvider(
        endpoint="https://api.groq.com/openai/v1/audio/transcriptions",
        api_key="test-api-key",
        model="whisper-large-v3-turbo",
        provider_name="groq"
    )

    audio_file = Path(__file__) # just any valid path to exist
    result = provider.transcribe(
        audio_path=audio_file,
        language_hint=LanguageHint.AUTO
    )
    
    assert len(calls) == 2
    assert result.language == "en"
    assert len(result.words) == 1
    assert result.words[0].text == "hello"

# 2. Groq retry test (transient error -> max attempts reached)
def test_groq_whisper_retry_fails_max_attempts(monkeypatch) -> None:
    calls = []
    
    def mock_post(*args, **kwargs):
        calls.append(kwargs)
        response = httpx.Response(503, request=httpx.Request("POST", args[0]))
        raise httpx.HTTPStatusError(
            "Service Unavailable",
            request=response.request,
            response=response,
        )

    monkeypatch.setattr("httpx.post", mock_post)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    provider = WhisperCompatibleHttpProvider(
        endpoint="https://api.groq.com/openai/v1/audio/transcriptions",
        api_key="test-api-key",
        model="whisper-large-v3-turbo",
        provider_name="groq"
    )

    audio_file = Path(__file__)
    
    with pytest.raises(ProviderCallFailure) as exc_info:
        provider.transcribe(
            audio_path=audio_file,
            language_hint=LanguageHint.AUTO
        )

    assert exc_info.value.error_info.code == "provider_unavailable"
    assert exc_info.value.error_info.retryable is True
    assert len(calls) == 3 # max_attempts = 3

# 3. Groq 401/403/non-429 4xx does not retry
def test_groq_whisper_non_transient_no_retry(monkeypatch) -> None:
    calls = []
    
    def mock_post(*args, **kwargs):
        calls.append(kwargs)
        response = httpx.Response(401, request=httpx.Request("POST", args[0]))
        raise httpx.HTTPStatusError("Unauthorized", request=response.request, response=response)

    monkeypatch.setattr("httpx.post", mock_post)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    provider = WhisperCompatibleHttpProvider(
        endpoint="https://api.groq.com/openai/v1/audio/transcriptions",
        api_key="test-api-key",
        model="whisper-large-v3-turbo",
        provider_name="groq"
    )

    audio_file = Path(__file__)
    
    with pytest.raises(ProviderCallFailure) as exc_info:
        provider.transcribe(
            audio_path=audio_file,
            language_hint=LanguageHint.AUTO
        )

    assert exc_info.value.error_info.code == "provider_auth_failed"
    assert exc_info.value.error_info.retryable is False
    assert len(calls) == 1 # No retry on 401!

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

# 5. OpenRouter request fallback
def test_openrouter_request_failure_fallback(monkeypatch) -> None:
    def mock_post(*args, **kwargs):
        raise httpx.ConnectError("Connection timed out")

    monkeypatch.setattr("httpx.post", mock_post)

    provider = HttpEditPlannerProvider(
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        api_key="test-openrouter-key",
        model="openrouter/free",
        provider_name="openrouter"
    )

    transcript = TranscriptRecord(
        id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        status="ready",
        language="en",
        language_hint=LanguageHint.AUTO,
        provider="groq",
        model="whisper",
        confidence=1.0,
        duration_ms=5000,
        words=[TranscriptWord(index=0, text="hello", start_ms=0, end_ms=1000, confidence=1.0)],
        raw_payload={}
    )

    payload = PlanCreateRequest(
        style=EditPlanStyle.CLEAN_MINIMAL,
        prompt="Make it clean"
    )

    result = provider.create_plan(
        transcript=transcript,
        payload=payload,
        video_duration_ms=5000
    )

    assert result.raw_payload["fallback_used"] is True
    assert any(
        json.loads(err).get("code") == "provider_network_error"
        for err in result.validation_errors
        if err.startswith("{")
    )
    assert result.plan is not None

# 6. health dependencies endpoint test with audio_ml
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
                redis_url=None,
                supabase_url=None,
                supabase_service_role_key=None,
                remotion_enabled=False,
                admin_api_token=None,
                render_execute_ffmpeg=False
            )
            self.queue_registry = SimpleNamespace(
                snapshots=lambda: []
            )
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

    response = asyncio.run(dependencies(request))

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
