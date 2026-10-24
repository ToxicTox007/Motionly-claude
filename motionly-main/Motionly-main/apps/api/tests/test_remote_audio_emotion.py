"""Voice emotion on an OpenRouter audio model (AUDIO_EMOTION_PROVIDER=openrouter_audio)."""

from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
import wave
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.main import _validate_production_settings, create_app
from app.schemas.captions import TranscriptWord
from app.schemas.scale import EmotionPacing, EmotionWindow
from app.services.audio_intelligence import merge_audio_intelligence
from app.services.audio_ml_providers import emotion_insights
from app.services.clip_finder import ClipGoal, _emotion_score
from app.services.model_adapters import OpenAICompatibleAdapter
from app.services.model_domain_adapters import (
    OPENROUTER_AUDIO_EMOTION_ROUTE_ID,
    OPENROUTER_ZDR_ADAPTER,
    GatewayAudioIntelligenceProvider,
    bundle_revision,
    ensure_openrouter_zdr_adapter,
    register_audio_route,
    register_openrouter_audio_emotion_route,
)
from app.services.model_gateway import (
    AudioRequest,
    ChatMessage,
    InMemoryBudgetLedger,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelRoute,
    PlanningRequest,
    budget_scope,
    feature_budget_scope,
)
from app.services.remote_audio_emotion import OpenRouterAudioEmotionProvider
from tests.test_service_hardening import _production_settings

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-3.5-flash-lite"
REVISION = "google/gemini-3.5-flash-lite-2026-07-21"


def _wav(tmp_path: Path, seconds: float, name: str = "speech.wav") -> Path:
    path = tmp_path / name
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x01\x00" * int(16_000 * seconds))
    return path


def _completion(content: object, *, prompt_tokens: int = 400) -> dict:
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 60},
    }


def _install(monkeypatch: pytest.MonkeyPatch, responses: list[tuple[int, dict]]) -> list[dict]:
    calls: list[dict] = []
    queue = list(responses)

    def fake_request(client, method, url, **kwargs):  # noqa: ANN001
        del client
        calls.append({"method": method, "url": url, **kwargs})
        status, body = queue.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    return calls


def _gateway(**kwargs) -> ModelGateway:
    gateway = ModelGateway(adapters={}, routes=[], **kwargs)
    register_openrouter_audio_emotion_route(
        gateway,
        api_key="openrouter-key",
        model=MODEL,
        revision=REVISION,
        input_cost_per_million_usd=0.3,
        output_cost_per_million_usd=2.5,
    )
    return gateway


def _provider(gateway: ModelGateway, **overrides) -> OpenRouterAudioEmotionProvider:
    options = {
        "gateway": gateway,
        "route_id": OPENROUTER_AUDIO_EMOTION_ROUTE_ID,
        "model": MODEL,
        "model_revision": REVISION,
        "window_ms": 5000,
        "chunk_seconds": 10,
        **overrides,
    }
    return OpenRouterAudioEmotionProvider(**options)


def _window(index: int, emotion: str = "joy", **scores: float) -> dict:
    return {
        "index": index,
        "emotion": emotion,
        "joy": scores.get("joy", 0.7),
        "sadness": scores.get("sadness", 0.1),
        "urgency": scores.get("urgency", 0.2),
        "intensity": scores.get("intensity", 0.6),
    }


# -- adapter -------------------------------------------------------------------


def test_extra_body_is_merged_and_request_fields_win(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, [(200, _completion("{}"))])
    adapter = OpenAICompatibleAdapter(
        base_url="https://openrouter.ai/api/v1",
        api_key="key",
        extra_body={"provider": {"zdr": True}, "model": "must-not-win"},
    )
    route = ModelRoute(
        route_id="plan",
        capability=ModelCapability.PLANNING,
        adapter="a",
        provider="openrouter",
        model="planner",
        model_revision="planner-2026-07-14",
    )

    adapter.invoke(
        route=route,
        request=PlanningRequest(messages=(ChatMessage(role="user", content="hi"),)),
    )

    body = calls[0]["json"]
    assert body["provider"] == {"zdr": True}
    assert body["model"] == "planner"


def test_default_adapter_sends_no_extra_fields(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls = _install(monkeypatch, [(200, _completion("ok"))])
    adapter = OpenAICompatibleAdapter(base_url="https://models.test/v1", api_key="key")
    route = ModelRoute(
        route_id="audio",
        capability=ModelCapability.AUDIO,
        adapter="a",
        provider="custom",
        model="audio-model",
        model_revision="audio-model-2026-07-14",
    )

    adapter.invoke(route=route, request=AudioRequest(prompt="x", audio_path=_wav(tmp_path, 1)))

    assert set(calls[0]["json"]) == {"model", "messages", "max_tokens"}


def test_zdr_adapter_is_registered_once_per_gateway() -> None:
    gateway = ModelGateway(adapters={}, routes=[])

    first = ensure_openrouter_zdr_adapter(gateway, api_key="key")
    second = ensure_openrouter_zdr_adapter(gateway, api_key="key")

    assert first == second == OPENROUTER_ZDR_ADAPTER
    assert gateway.has_adapter(OPENROUTER_ZDR_ADAPTER)


def test_route_is_gated_priced_and_charged_to_audio_emotion() -> None:
    route = next(r for r in _gateway().routes if r.route_id == OPENROUTER_AUDIO_EMOTION_ROUTE_ID)

    assert route.operations == (ModelOperation.AUDIO_PROMPT,)
    assert route.adapter == OPENROUTER_ZDR_ADAPTER
    assert route.endpoint_host == "openrouter.ai"
    assert route.requires_live_effects_gate is True
    assert route.budget_feature == "audio_emotion"
    assert route.max_output_units == 1024


# -- provider ------------------------------------------------------------------


def test_chunks_are_rated_and_mapped_to_absolute_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fenced = "```json\n" + json.dumps({"windows": [_window(0, "anger")]}) + "\n```"
    calls = _install(
        monkeypatch,
        [
            (200, _completion({"windows": [_window(0), _window(1, "calm", intensity=0.2)]})),
            # The tail chunk (10-12 s) holds one window; a fenced reply is accepted.
            (200, _completion(fenced)),
        ],
    )

    windows = _provider(_gateway()).classify_windows(audio_path=_wav(tmp_path, 12))

    assert [(w["start_ms"], w["end_ms"], w["emotion"]) for w in windows] == [
        (0, 5000, "joy"),
        (5000, 10000, "calm"),
        (10000, 12000, "anger"),
    ]
    for window in windows:
        EmotionWindow.model_validate(window)
    assert len(calls) == 2


def test_requests_use_zdr_routing_structured_output_and_wav_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install(monkeypatch, [(200, _completion({"windows": [_window(0)]}))])

    _provider(_gateway()).classify_windows(audio_path=_wav(tmp_path, 4))

    body = calls[0]["json"]
    assert calls[0]["url"] == CHAT_URL
    assert calls[0]["headers"]["authorization"] == "Bearer openrouter-key"
    assert body["provider"] == {"zdr": True, "data_collection": "deny"}
    assert body["model"] == MODEL
    assert body["max_tokens"] == 1024
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    text, audio = body["messages"][0]["content"]
    assert "4.0 seconds" in text["text"] and "1 windows" in text["text"]
    assert audio["input_audio"]["format"] == "wav"
    chunk = base64.b64decode(audio["input_audio"]["data"])
    with wave.open(io.BytesIO(chunk), "rb") as reader:
        assert reader.getnframes() == 16_000 * 4


def test_scores_are_clamped_and_bad_items_are_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reply = {
        "windows": [
            _window(0, "ecstatic", joy=1.7, sadness=-0.4, intensity=0.5),
            _window(0, "sadness"),  # duplicate index: first wins
            _window(9),  # out of range
            {"index": "1", "emotion": "joy", "intensity": 0.5},  # non-integer index
            {"index": 1, "emotion": "calm", "joy": 0.2, "sadness": 0.1, "urgency": 0.3},
        ]
    }
    _install(monkeypatch, [(200, _completion(reply))])

    windows = _provider(_gateway()).classify_windows(audio_path=_wav(tmp_path, 10))

    assert windows[0]["emotion"] == "neutral"
    assert (windows[0]["joy"], windows[0]["sadness"]) == (1.0, 0.0)
    # A missing intensity is derived from the strongest score.
    assert windows[1]["intensity"] == 0.3
    assert len(windows) == 2


@pytest.mark.parametrize(
    "response",
    [
        (200, _completion("not json")),
        (200, _completion({"result": []})),
        (200, _completion({"windows": [{"index": 0}]})),
    ],
)
def test_malformed_replies_degrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, response
) -> None:
    _install(monkeypatch, [response])

    result = _provider(_gateway()).classify_windows_detailed(audio_path=_wav(tmp_path, 10))

    assert result["degraded"] is True
    assert result["status"] == "unavailable:provider_malformed_response"
    assert result["windows"] == []


def test_provider_failure_stops_after_the_first_chunk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install(monkeypatch, [(503, {"error": "busy"})])

    result = _provider(_gateway()).classify_windows_detailed(audio_path=_wav(tmp_path, 30))

    assert result["status"] == "unavailable:provider_unavailable"
    assert len(calls) == 1


def test_budget_exhaustion_degrades_without_calling_the_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install(monkeypatch, [])
    gateway = _gateway(feature_budgets_usd={"audio_emotion": 0.000001})

    result = _provider(gateway).classify_windows_detailed(audio_path=_wav(tmp_path, 10))

    assert result["status"] == "unavailable:model_budget_exceeded"
    assert calls == []


def test_unreadable_audio_degrades(tmp_path: Path) -> None:
    garbage = tmp_path / "speech.bin"
    garbage.write_bytes(b"definitely not audio")

    result = _provider(_gateway()).classify_windows_detailed(audio_path=garbage)

    assert result["status"] == "unavailable:audio_unreadable"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_non_wav_audio_is_converted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    compressed = tmp_path / "speech.m4a"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=3", str(compressed),
        ],
        check=True,
    )
    _install(monkeypatch, [(200, _completion({"windows": [_window(0)]}))])

    windows = _provider(_gateway()).classify_windows(audio_path=compressed)

    # AAC encoding pads a few milliseconds of priming samples.
    assert len(windows) == 1
    assert windows[0]["start_ms"] == 0
    assert abs(windows[0]["end_ms"] - 3000) < 100


def test_tails_shorter_than_a_second_are_not_rated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install(monkeypatch, [(200, _completion({"windows": [_window(0), _window(1)]}))])

    windows = _provider(_gateway()).classify_windows(audio_path=_wav(tmp_path, 10.5))

    assert len(calls) == 1  # the 0.5 s tail chunk is skipped
    assert len(windows) == 2


def test_nested_calls_are_attributed_to_the_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, [(200, _completion({"windows": [_window(0)]}, prompt_tokens=1000))])
    ledger = InMemoryBudgetLedger()
    # Feature spend is tracked only for capped features.
    gateway = _gateway(budget_ledger=ledger, feature_budgets_usd={"audio_emotion": 10.0})
    audio_route = register_audio_route(
        gateway,
        providers={"emotion": _provider(gateway)},
        revision=bundle_revision("audio-bundle"),
    )

    insights = GatewayAudioIntelligenceProvider(gateway=gateway, route=audio_route)\
        .analyze_with_context(
            audio_path=_wav(tmp_path, 5), workspace_id="workspace-a", project_id="project-a"
        )

    expected = 1000 * 0.3 / 1_000_000 + 60 * 2.5 / 1_000_000
    assert insights["emotion_ml"] == {"provider": "openrouter_audio_emotion", "model": MODEL}
    assert len(insights["emotion_windows"]) == 1
    assert ledger.usage(budget_scope("workspace-a")) == pytest.approx(expected)
    assert ledger.usage(feature_budget_scope("audio_emotion")) == pytest.approx(expected)
    assert ledger.usage(budget_scope(None)) == 0.0


# -- merge into audio intelligence -----------------------------------------------


class _LocalEmotion:
    def classify_windows(self, *, audio_path):
        return [{"start_ms": 0, "end_ms": 500, "emotion": "happy", "intensity": 0.8}]


def test_local_providers_keep_their_exact_contract(tmp_path: Path) -> None:
    assert emotion_insights(_LocalEmotion(), audio_path=tmp_path / "a.wav") == {
        "emotion_windows": [{"start_ms": 0, "end_ms": 500, "emotion": "happy", "intensity": 0.8}]
    }


_WORDS = [
    TranscriptWord(index=0, text="This", start_ms=0, end_ms=300, confidence=0.9),
    TranscriptWord(index=1, text="matters", start_ms=320, end_ms=900, confidence=0.9),
]


def test_degraded_remote_emotion_keeps_the_transcript_estimate() -> None:
    heuristic = merge_audio_intelligence(raw_payload={}, words=_WORDS, duration_ms=6000)
    merged = merge_audio_intelligence(
        raw_payload={},
        words=_WORDS,
        duration_ms=6000,
        ml_insights={
            "emotion_ml": {
                "provider": "openrouter_audio_emotion",
                "degraded": True,
                "status": "unavailable:provider_timeout",
            }
        },
    )

    pacing = merged["audio_intelligence"]["emotion_pacing"]
    expected = heuristic["audio_intelligence"]["emotion_pacing"]
    assert pacing["windows"] == expected["windows"]
    assert pacing.get("provider") == expected.get("provider")
    assert pacing["ml_status"] == "unavailable:provider_timeout"
    assert pacing["ml_provider"] == "openrouter_audio_emotion"
    EmotionPacing.model_validate(pacing)


def test_remote_emotion_windows_are_labelled_with_their_provider() -> None:
    merged = merge_audio_intelligence(
        raw_payload={},
        words=_WORDS,
        duration_ms=6000,
        ml_insights={
            "emotion_windows": [
                {"start_ms": 0, "end_ms": 5000, "emotion": "sadness", "joy": 0.1,
                 "sadness": 0.8, "urgency": 0.1, "intensity": 0.4},
            ],
            "emotion_ml": {"provider": "openrouter_audio_emotion", "model": MODEL},
        },
    )

    pacing = merged["audio_intelligence"]["emotion_pacing"]
    assert pacing["provider"] == "openrouter_audio_emotion"
    assert pacing["aggregate_emotion"] == "sadness"


def test_degraded_remote_diarization_keeps_the_transcript_estimate() -> None:
    heuristic = merge_audio_intelligence(raw_payload={}, words=_WORDS, duration_ms=6000)
    remote_failure = {
        "provider": "pyannoteai",
        "speaker_count": 0,
        "segments": [],
        "degraded": True,
        "status": "unavailable:provider_job_failed",
        "heuristic_fallback": True,
    }

    merged = merge_audio_intelligence(
        raw_payload={},
        words=_WORDS,
        duration_ms=6000,
        ml_insights={"speaker_diarization": remote_failure},
    )

    diarization = merged["audio_intelligence"]["speaker_diarization"]
    expected = heuristic["audio_intelligence"]["speaker_diarization"]
    assert diarization["speaker_count"] == expected["speaker_count"]
    assert diarization["provider"] == expected["provider"]
    assert diarization["ml_status"] == "unavailable:provider_job_failed"


def test_degraded_local_diarization_behaviour_is_unchanged() -> None:
    local_failure = {
        "provider": "pyannote",
        "speaker_count": 0,
        "segments": [],
        "degraded": True,
        "status": "unavailable:dependency_missing",
    }

    merged = merge_audio_intelligence(
        raw_payload={},
        words=_WORDS,
        duration_ms=6000,
        ml_insights={"speaker_diarization": local_failure},
    )

    assert merged["audio_intelligence"]["speaker_diarization"] == local_failure


def test_clip_scoring_works_with_coarse_windows() -> None:
    windows = [
        {"start_ms": 0, "end_ms": 5000, "emotion": "joy", "intensity": 0.9},
        {"start_ms": 5000, "end_ms": 10000, "emotion": "calm", "intensity": 0.2},
    ]

    emotion, intensity, score = _emotion_score(
        start_ms=1000, end_ms=6000, goal=ClipGoal.VIRAL_HOOK, emotion_windows=windows
    )

    assert emotion == "joy"
    assert intensity == pytest.approx((0.9 * 4000 + 0.2 * 1000) / 5000)
    assert 0 < score <= 1


# -- wiring --------------------------------------------------------------------


def _audio_route(app):
    return next(
        route for route in app.state.model_gateway.routes
        if route.route_id == "local-audio-intelligence"
    )


def test_create_app_worker_switches_emotion_to_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    get_settings.cache_clear()
    default_app = create_app()

    monkeypatch.setenv("AUDIO_EMOTION_PROVIDER", "openrouter_audio")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    get_settings.cache_clear()
    remote_app = create_app()

    assert type(default_app.state.audio_ml_providers["emotion"]).__name__ == (
        "Wav2VecEmotionProvider"
    )
    remote_emotion = remote_app.state.audio_ml_providers["emotion"]
    assert isinstance(remote_emotion, OpenRouterAudioEmotionProvider)
    assert "sound" in remote_app.state.audio_ml_providers
    assert _audio_route(remote_app).model_revision != _audio_route(default_app).model_revision
    # Planning keeps its own adapter without ZDR routing.
    planning = next(
        route
        for route in remote_app.state.model_gateway.routes
        if route.route_id.endswith("planning-primary")
    )
    assert planning.adapter == "openrouter"


def test_create_app_requires_an_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    monkeypatch.setenv("AUDIO_EMOTION_PROVIDER", "openrouter_audio")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        create_app()


def test_emotion_can_be_disabled_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    monkeypatch.setenv("AUDIO_EMOTION_PROVIDER", "disabled")
    get_settings.cache_clear()

    app = create_app()

    assert "emotion" not in app.state.audio_ml_providers
    assert "sound" in app.state.audio_ml_providers


_PRICED_EMOTION = {
    "audio_emotion_provider": "openrouter_audio",
    "audio_emotion_input_cost_per_million_usd": 0.3,
    "audio_emotion_output_cost_per_million_usd": 2.5,
}


def test_production_accepts_a_priced_emotion_configuration() -> None:
    _validate_production_settings(_production_settings(**_PRICED_EMOTION))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"openrouter_api_key": None}, "OPENROUTER_API_KEY"),
        ({"audio_emotion_input_cost_per_million_usd": 0.0}, "must be positive"),
        ({"audio_emotion_output_cost_per_million_usd": 0.0}, "must be positive"),
        ({"audio_emotion_model_revision": "google/gemini-3.5-flash-lite"}, "immutable"),
    ],
)
def test_production_rejects_incomplete_emotion_configuration(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _validate_production_settings(_production_settings(**{**_PRICED_EMOTION, **overrides}))
