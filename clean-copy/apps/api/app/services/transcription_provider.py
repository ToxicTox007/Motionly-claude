import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from app.schemas.captions import LanguageHint, TranscriptWord
from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderCircuitOpen,
    ProviderErrorInfo,
    bounded_backoff_seconds,
    classify_provider_exception,
    provider_circuit_breaker,
)


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

        logger = logging.getLogger(__name__)

        max_attempts = 3
        request_timeout = httpx.Timeout(30.0, connect=5.0, read=25.0)

        attempt = 0
        while True:
            attempt += 1
            started = time.perf_counter()
            try:
                provider_circuit_breaker.check(self.provider_name)
                with audio_path.open("rb") as audio_file:
                    response = httpx.post(
                        self.endpoint,
                        headers=headers,
                        data=data,
                        files={"file": (audio_path.name, audio_file, "audio/wav")},
                        timeout=request_timeout,
                    )
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError:
                    raise ProviderCallFailure(
                        provider=self.provider_name,
                        error_info=ProviderErrorInfo("provider_malformed_response", False),
                    ) from None
                if not isinstance(payload, dict):
                    raise ProviderCallFailure(
                        provider=self.provider_name,
                        error_info=ProviderErrorInfo("provider_malformed_response", False),
                    )
                provider_circuit_breaker.record_success(self.provider_name)
                logger.info(
                    "transcription_provider_succeeded",
                    extra={
                        "provider": self.provider_name,
                        "attempt": attempt,
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                    },
                )
                break
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
                ProviderCallFailure,
                ProviderCircuitOpen,
            ) as exc:
                error_info = classify_provider_exception(exc)
                latency_ms = round((time.perf_counter() - started) * 1000)
                if not error_info.retryable or attempt >= max_attempts:
                    if error_info.code != "provider_circuit_open":
                        provider_circuit_breaker.record_failure(
                            self.provider_name,
                            retryable=error_info.retryable,
                        )
                    logger.error(
                        "transcription_provider_failed",
                        extra={
                            "provider": self.provider_name,
                            "attempt": attempt,
                            "status_code": error_info.status_code,
                            "error_code": error_info.code,
                            "retryable": error_info.retryable,
                            "latency_ms": latency_ms,
                            "error_type": type(exc).__name__,
                        }
                    )
                    raise ProviderCallFailure(
                        provider=self.provider_name,
                        error_info=error_info,
                    ) from None

                delay = bounded_backoff_seconds(attempt)
                logger.warning(
                    "transcription_provider_transient_error",
                    extra={
                        "provider": self.provider_name,
                        "attempt": attempt,
                        "delay": delay,
                        "error_code": error_info.code,
                        "retryable": error_info.retryable,
                        "latency_ms": latency_ms,
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
