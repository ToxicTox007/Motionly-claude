import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.schemas.broll import StockOrientation
from app.schemas.captions import LanguageHint, TranscriptWord
from app.schemas.plans import BrollDensity, PlanCreateRequest
from app.services.edit_planner import HttpEditPlannerProvider
from app.services.scale import RunwayGen3Provider
from app.services.stock_video import PexelsStockVideoProvider, PixabayStockVideoProvider
from app.services.transcription_provider import WhisperCompatibleHttpProvider

FIXTURES = Path(__file__).parent / "fixtures/providers"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def response(name: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=fixture(name),
        request=httpx.Request("GET", "https://provider.motionly.test/recorded"),
    )


def test_recorded_openrouter_plan_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.edit_planner.httpx.post",
        lambda *_a, **_k: response("openrouter_plan.json"),
    )
    transcript = SimpleNamespace(
        words=[
            TranscriptWord(index=0, text="Build", start_ms=0, end_ms=500, confidence=0.96),
            TranscriptWord(index=1, text="better", start_ms=550, end_ms=1100, confidence=0.95),
        ],
        raw_payload={},
    )
    result = HttpEditPlannerProvider(
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        api_key="sandbox",
        model="recorded-model",
        provider_name="openrouter",
    ).create_plan(
        transcript=transcript,
        payload=PlanCreateRequest(
            style="founder", target_duration_s=10, broll_density=BrollDensity.NONE
        ),
        video_duration_ms=2000,
    )
    # Provider cuts are normalized to the nearest available transcript boundary.
    assert result.plan.cuts[0].end_ms == 1100
    assert result.raw_payload["usage"]["total_tokens"] == 200
    assert result.cost_estimate_usd == 0.0004


def test_recorded_groq_whisper_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.services.transcription_provider.httpx.post",
        lambda *_a, **_k: response("groq_whisper.json"),
    )
    audio = tmp_path / "source.wav"
    audio.write_bytes(b"RIFF-recorded")
    result = WhisperCompatibleHttpProvider(
        endpoint="https://api.groq.com/openai/v1/audio/transcriptions",
        api_key="sandbox",
        model="whisper-large-v3-turbo",
        provider_name="groq",
    ).transcribe(audio_path=audio, language_hint=LanguageHint.AUTO)
    assert result.language == "en"
    assert [(word.text, word.start_ms, word.end_ms) for word in result.words] == [
        ("Build", 100, 420),
        ("better", 450, 880),
    ]


def test_recorded_stock_provider_contracts(monkeypatch) -> None:
    payloads = iter([response("pexels_video_search.json"), response("pixabay_video_search.json")])
    monkeypatch.setattr("app.services.stock_video.httpx.get", lambda *_a, **_k: next(payloads))
    pexels = PexelsStockVideoProvider(api_key="sandbox").search(
        query="founder", orientation=StockOrientation.PORTRAIT, min_width=720, min_height=1280
    )
    pixabay = PixabayStockVideoProvider(api_key="sandbox").search(
        query="founder", orientation=StockOrientation.PORTRAIT, min_width=720, min_height=1280
    )
    assert (pexels[0].provider_asset_id, pexels[0].duration_ms) == ("12345", 6200)
    assert (pixabay[0].provider_asset_id, pixabay[0].duration_ms) == ("67890", 5000)


def test_recorded_runway_generation_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.scale.httpx.post", lambda *_a, **_k: response("runway_create.json")
    )
    monkeypatch.setattr(
        "app.services.scale.httpx.get", lambda *_a, **_k: response("runway_succeeded.json")
    )
    monkeypatch.setattr("app.services.scale.time.sleep", lambda _seconds: None)
    result = RunwayGen3Provider(api_key="sandbox").generate(
        prompt="Founder enters office",
        prompt_image_url="https://images.test/founder.jpg",
        duration_s=5,
        ratio="768:1280",
    )
    assert result.task_id == "task_recorded_123"
    assert result.video_url.endswith("task_recorded_123.mp4")
