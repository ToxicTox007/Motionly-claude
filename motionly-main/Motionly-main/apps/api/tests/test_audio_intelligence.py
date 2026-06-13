from app.schemas.captions import TranscriptWord
from app.services.audio_intelligence import (
    build_audio_intelligence_payload,
    merge_audio_intelligence,
)


def test_audio_intelligence_extracts_keywords_and_style_signals() -> None:
    words = [
        TranscriptWord(index=0, text="Show", start_ms=0, end_ms=200, confidence=0.92),
        TranscriptWord(index=1, text="the", start_ms=220, end_ms=320, confidence=0.91),
        TranscriptWord(index=2, text="simple", start_ms=340, end_ms=620, confidence=0.93),
        TranscriptWord(index=3, text="hook", start_ms=650, end_ms=900, confidence=0.88),
        TranscriptWord(index=4, text="then", start_ms=920, end_ms=1100, confidence=0.89),
        TranscriptWord(index=5, text="prove", start_ms=1120, end_ms=1390, confidence=0.95),
        TranscriptWord(index=6, text="the", start_ms=1400, end_ms=1480, confidence=0.94),
        TranscriptWord(index=7, text="result", start_ms=1500, end_ms=1800, confidence=0.96),
    ]

    payload = build_audio_intelligence_payload(words=words, duration_ms=2400)

    keywords = [item["text"] for item in payload["keywords"]]
    assert "hook" in keywords
    assert "result" in keywords
    assert "the" not in keywords
    assert payload["style_signals"]["predicted_style"] in {"creator", "motivational"}
    assert payload["style_signals"]["tone"] == "confident"
    assert payload["style_signals"]["pace"] == "fast"
    assert payload["confidence"]["average_word_confidence"] == 0.922
    assert payload["confidence"]["word_confidence_available"] is True
    assert payload["speaker_diarization"]["speaker_count"] == 1
    assert payload["speaker_diarization"]["segments"]
    assert payload["emotion_pacing"]["aggregate_emotion"] == "positive"
    assert payload["emotion_pacing"]["windows"]
    assert payload["sound_classification"]["labels"][0]["label"] == "speech"


def test_audio_intelligence_marks_no_audio_sources() -> None:
    payload = build_audio_intelligence_payload(
        words=[],
        duration_ms=3000,
        has_audio=False,
    )

    assert payload["keywords"] == []
    assert payload["speech_metrics"]["word_count"] == 0
    assert payload["speaker_diarization"]["speaker_count"] == 0
    assert payload["emotion_pacing"]["windows"] == []
    assert payload["sound_classification"]["background_sound"] == "none"
    assert payload["sound_classification"]["labels"][0]["label"] == "no_audio"


def test_audio_intelligence_splits_speaker_segments_on_long_pauses() -> None:
    words = [
        TranscriptWord(index=0, text="First", start_ms=0, end_ms=300, confidence=0.9),
        TranscriptWord(index=1, text="thought", start_ms=420, end_ms=800, confidence=0.9),
        TranscriptWord(index=2, text="Second", start_ms=2600, end_ms=3000, confidence=0.9),
        TranscriptWord(index=3, text="thought", start_ms=3120, end_ms=3500, confidence=0.9),
    ]

    payload = build_audio_intelligence_payload(words=words, duration_ms=4000)

    assert payload["emotion_pacing"]["pause_count"] == 1
    assert len(payload["speaker_diarization"]["segments"]) == 2


def test_audio_intelligence_merges_real_ml_provider_outputs() -> None:
    words = [
        TranscriptWord(index=0, text="Premium", start_ms=0, end_ms=300, confidence=0.9),
        TranscriptWord(index=1, text="craft", start_ms=340, end_ms=700, confidence=0.92),
    ]

    payload = merge_audio_intelligence(
        raw_payload={"provider": "fixture"},
        words=words,
        duration_ms=1200,
        ml_insights={
            "speaker_diarization": {
                "provider": "pyannote",
                "speaker_count": 2,
                "segments": [],
            },
            "emotion_windows": [
                {
                    "start_ms": 0,
                    "end_ms": 500,
                    "emotion": "happy",
                    "intensity": 0.81,
                }
            ],
            "sound_classification": {
                "provider": "yamnet",
                "labels": [{"label": "speech", "confidence": 0.97}],
            },
        },
    )

    audio = payload["audio_intelligence"]
    assert audio["speaker_diarization"]["provider"] == "pyannote"
    assert audio["speaker_diarization"]["speaker_count"] == 2
    assert audio["emotion_pacing"]["provider"] == "wav2vec2_emotion"
    assert audio["emotion_pacing"]["aggregate_emotion"] == "happy"
    assert audio["sound_classification"]["provider"] == "yamnet"
