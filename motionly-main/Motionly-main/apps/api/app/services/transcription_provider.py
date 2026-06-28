from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

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


class WhisperCompatibleHttpProvider:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str | None,
        model: str,
        provider_name: str = "http_whisper",
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.provider_name = provider_name

    def transcribe(
        self,
        *,
        audio_path: Path | None,
        language_hint: LanguageHint,
    ) -> TranscriptionResult:
        if audio_path is None:
            raise ValueError("audio_path is required for HTTP transcription.")

        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
        }
        if language_hint != LanguageHint.AUTO:
            data["language"] = language_hint.value

        import logging
        import time
        import random

        logger = logging.getLogger(__name__)

        max_attempts = 3
        base_delay = 1.0
        backoff_factor = 2.0
        request_timeout = 30

        attempt = 0
        while True:
            attempt += 1
            try:
                with audio_path.open("rb") as audio_file:
                    response = httpx.post(
                        self.endpoint,
                        headers=headers,
                        data=data,
                        files={"file": (audio_path.name, audio_file, "audio/wav")},
                        timeout=request_timeout,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                break
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                is_transient = True
                status_code = None
                if isinstance(exc, httpx.HTTPStatusError):
                    status_code = exc.response.status_code
                    if 400 <= status_code < 500 and status_code != 429:
                        is_transient = False

                if not is_transient or attempt >= max_attempts:
                    logger.error(
                        "transcription_provider_failed",
                        extra={
                            "provider": self.provider_name,
                            "attempt": attempt,
                            "status_code": status_code,
                            "error_type": type(exc).__name__,
                        }
                    )
                    if status_code:
                        raise RuntimeError(f"Transcription provider returned status {status_code}") from None
                    raise RuntimeError(f"Transcription provider failed: {type(exc).__name__}") from None

                delay = base_delay * (backoff_factor ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning(
                    "transcription_provider_transient_error",
                    extra={
                        "provider": self.provider_name,
                        "attempt": attempt,
                        "delay": round(delay, 2),
                        "error_type": type(exc).__name__,
                    }
                )
                time.sleep(delay)
        language = normalize_transcription_language(payload.get("language"), language_hint)
        words = [
            TranscriptWord(
                index=index,
                text=str(item["word"]).strip(),
                start_ms=int(float(item["start"]) * 1000),
                end_ms=int(float(item["end"]) * 1000),
                confidence=item.get("confidence"),
            )
            for index, item in enumerate(payload.get("words", []))
            if item.get("word") and item.get("start") is not None and item.get("end") is not None
        ]
        return TranscriptionResult(
            language=language,
            confidence=payload.get("confidence"),
            provider=self.provider_name,
            model=self.model,
            words=words,
            raw_payload=payload,
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
