from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.schemas.captions import LanguageHint, TranscriptWord


@dataclass(frozen=True)
class TranscriptionResult:
    language: str
    confidence: float | None
    provider: str
    model: str
    words: list[TranscriptWord]
    raw_payload: dict[str, object]


class TranscriptionProvider(Protocol):
    provider_name: str
    model: str

    def transcribe(
        self,
        *,
        audio_path: Path | None,
        language_hint: LanguageHint,
    ) -> TranscriptionResult:
        """Return word-level transcript data for an extracted audio file."""


class FixtureTranscriptionProvider:
    provider_name = "fixture"

    def __init__(self, *, model: str = "fixture-v1") -> None:
        self.model = model

    def transcribe(
        self,
        *,
        audio_path: Path | None,
        language_hint: LanguageHint,
    ) -> TranscriptionResult:
        language = language_hint.value if language_hint != LanguageHint.AUTO else "en"
        source_words = (
            ["Yeh", "simple", "secret", "hai:", "pehle", "hook", "dikhao,", "phir", "proof."]
            if language == "hi"
            else [
                "Here",
                "is",
                "the",
                "simple",
                "secret:",
                "show",
                "the",
                "hook,",
                "then",
                "prove",
                "the",
                "result.",
            ]
        )
        words: list[TranscriptWord] = []
        cursor_ms = 240
        for index, text in enumerate(source_words):
            duration_ms = max(280, min(620, len(text) * 52))
            words.append(
                TranscriptWord(
                    index=index,
                    text=text,
                    start_ms=cursor_ms,
                    end_ms=cursor_ms + duration_ms,
                    confidence=0.94,
                )
            )
            cursor_ms += duration_ms + (460 if text.endswith(":") else 120)

        return TranscriptionResult(
            language=language,
            confidence=0.94,
            provider=self.provider_name,
            model=self.model,
            words=words,
            raw_payload={"fixture": True, "word_count": len(words)},
        )


def normalize_transcription_language(value: object, language_hint: LanguageHint) -> str:
    language = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "eng": "en",
        "english": "en",
        "hin": "hi",
        "hindi": "hi",
        "spa": "es",
        "spanish": "es",
        "por": "pt",
        "portuguese": "pt",
        "fra": "fr",
        "fre": "fr",
        "french": "fr",
        "deu": "de",
        "ger": "de",
        "german": "de",
        "ara": "ar",
        "arabic": "ar",
    }
    if language in aliases:
        return aliases[language]
    if language[:2] in {"en", "hi", "es", "pt", "fr", "de", "ar"}:
        return language[:2]
    if language_hint != LanguageHint.AUTO:
        return language_hint.value
    return "unknown"
