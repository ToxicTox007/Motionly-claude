"""Voice-emotion windows from an audio-capable chat model.

The speech track is split into chunks (default 60 s), and the model rates the
vocal delivery of consecutive fixed windows (default 5 s) in each chunk as
structured JSON. Results use the same window shape as the local wav2vec2
provider (``start_ms``, ``end_ms``, ``emotion``, ``joy``, ``sadness``,
``urgency``, ``intensity``) but at a coarser time resolution.

Requests go through whichever gateway route serves ``audio.emotion`` with an
audio-input model; provider routing preferences (for example zero-data-retention
endpoints) are adapter ``extra_body`` data, not code here. On any failure the
provider reports a degraded result instead of raising, so the transcript-based
emotion estimate is kept and transcription never fails.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.services.inference_strategies import EMOTION_LABELS as STRATEGY_EMOTION_LABELS
from app.services.inference_strategies import (
    EMOTION_PROMPT,
    EmotionResponseInvalid,
    emotion_response_format,
    parse_emotion_windows,
)
from app.services.inference_strategies import (
    EMOTION_SCORE_FIELDS as STRATEGY_EMOTION_SCORE_FIELDS,
)
from app.services.media_probe import audio_duration_seconds
from app.services.model_gateway import (
    AudioOutput,
    AudioRequest,
    ModelCallContext,
    ModelGateway,
    ModelGatewayError,
    ModelOperation,
    ModelProvidersExhausted,
    inherited_call_context,
)
from app.services.provider_resilience import ProviderCallFailure, classify_provider_exception
from app.services.subprocess_env import media_tool_env

logger = logging.getLogger(__name__)

EMOTION_LABELS = STRATEGY_EMOTION_LABELS
_SCORE_FIELDS = STRATEGY_EMOTION_SCORE_FIELDS
# Windows shorter than this at the end of the audio are not rated.
_MIN_TAIL_MS = 1000

# The prompt, structured-output schema and window parser are Motionly-owned and
# shared by every route that can serve ``audio.emotion`` (plan Task 11); they
# live in :mod:`app.services.inference_strategies`.
_PROMPT = EMOTION_PROMPT
_response_format = emotion_response_format


class RemoteAudioEmotionProvider:
    provider_name = "remote_audio_emotion"
    remote = True

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        route_id: str,
        model: str,
        model_revision: str,
        window_ms: int = 5000,
        chunk_seconds: int = 60,
    ) -> None:
        if window_ms < 1000:
            raise ValueError("emotion window_ms must be at least 1000")
        if chunk_seconds * 1000 < window_ms:
            raise ValueError("emotion chunks must hold at least one window")
        self.gateway = gateway
        self.route_id = route_id
        self.model = model
        self.model_revision = model_revision
        self.window_ms = window_ms
        # Chunks hold a whole number of windows so windows never straddle chunks.
        self.chunk_ms = (chunk_seconds * 1000 // window_ms) * window_ms

    def check_availability(self) -> tuple[bool, str]:
        configured = any(
            route.route_id == self.route_id and ModelOperation.AUDIO_PROMPT in route.operations
            for route in self.gateway.routes
        )
        return (True, "remote:configured") if configured else (False, "route_missing")

    def classify_windows(self, *, audio_path: Path, window_ms: int | None = None) -> list[dict]:
        """Local-provider contract: windows on success, an empty list otherwise."""
        del window_ms  # The remote window size is configured on the provider.
        return list(self.classify_windows_detailed(audio_path=audio_path)["windows"])

    def classify_windows_detailed(self, *, audio_path: Path) -> dict[str, Any]:
        context = inherited_call_context()
        try:
            # Refuse up front rather than pay for the first chunks of a file
            # whose remaining chunks would not fit the plan's quota.
            self.gateway.check_feature_quota(
                route_id=self.route_id,
                context=context,
                quantity=audio_duration_seconds(audio_path) or 0.0,
            )
        except ModelGatewayError as exc:
            code = _failure_code(exc)
            logger.warning(
                "remote_audio_emotion_degraded",
                extra={"error_code": code, "model": self.model},
            )
            return self._degraded(code)
        windows: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="motionly-emotion-") as temp_dir:
            try:
                for offset_ms, duration_ms, chunk_path in _iter_wav_chunks(
                    audio_path, Path(temp_dir), chunk_ms=self.chunk_ms
                ):
                    try:
                        windows.extend(
                            self._classify_chunk(
                                chunk_path,
                                offset_ms=offset_ms,
                                duration_ms=duration_ms,
                                context=context,
                            )
                        )
                    except (
                        ModelGatewayError,
                        ProviderCallFailure,
                        EmotionResponseInvalid,
                    ) as exc:
                        # Stop on the first failure: partial coverage would be
                        # misleading and further calls would only add cost.
                        code = _failure_code(exc)
                        logger.warning(
                            "remote_audio_emotion_degraded",
                            extra={"error_code": code, "model": self.model},
                        )
                        return self._degraded(code)
                    finally:
                        chunk_path.unlink(missing_ok=True)
            except (OSError, wave.Error, EOFError, ValueError, subprocess.SubprocessError):
                return self._degraded("audio_unreadable")
        return self._result(windows)

    def _classify_chunk(
        self,
        chunk_path: Path,
        *,
        offset_ms: int,
        duration_ms: int,
        context: ModelCallContext,
    ) -> list[dict[str, Any]]:
        count = _window_count(duration_ms, self.window_ms)
        if count == 0:
            return []
        result = self.gateway.invoke(
            AudioRequest(
                prompt=_PROMPT.format(
                    duration=duration_ms / 1000,
                    window=self.window_ms / 1000,
                    count=count,
                    last=count - 1,
                    labels=", ".join(EMOTION_LABELS),
                ),
                audio_path=chunk_path,
                audio_format="wav",
                max_output_units=1024,
                response_format=_response_format(count),
                duration_s=duration_ms / 1000,
            ),
            context=ModelCallContext(
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                required_route_id=self.route_id,
            ),
        )
        if not isinstance(result.output, AudioOutput):
            raise EmotionResponseInvalid("audio route returned the wrong typed output")
        return _parse_windows(
            result.output.content,
            count=count,
            window_ms=self.window_ms,
            offset_ms=offset_ms,
            duration_ms=duration_ms,
        )

    def _result(self, windows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "windows": sorted(windows, key=lambda window: window["start_ms"]),
        }

    def _degraded(self, reason: str) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "windows": [],
            "degraded": True,
            "status": f"unavailable:{reason}",
        }


def _window_count(duration_ms: int, window_ms: int) -> int:
    full, tail = divmod(duration_ms, window_ms)
    return full + (1 if tail >= _MIN_TAIL_MS else 0)


def _parse_windows(
    content: str,
    *,
    count: int,
    window_ms: int,
    offset_ms: int,
    duration_ms: int,
) -> list[dict[str, Any]]:
    """Absolute-time windows via the Motionly-owned parser (plan Task 11)."""
    return parse_emotion_windows(
        content,
        count=count,
        window_ms=window_ms,
        offset_ms=offset_ms,
        duration_ms=duration_ms,
    )



def _iter_wav_chunks(
    audio_path: Path, output_dir: Path, *, chunk_ms: int
) -> Iterator[tuple[int, int, Path]]:
    """Yield consecutive WAV chunks as (offset_ms, duration_ms, path), one at a time."""
    source = audio_path
    if not _is_wav(source):
        source = _convert_to_wav(audio_path, output_dir / "speech.wav")
    with wave.open(str(source), "rb") as reader:
        rate = reader.getframerate()
        if rate <= 0:
            raise ValueError("invalid sample rate")
        frames_per_chunk = max(1, rate * chunk_ms // 1000)
        params = reader.getparams()
        offset_frames = 0
        index = 0
        while True:
            frames = reader.readframes(frames_per_chunk)
            frame_count = len(frames) // (params.sampwidth * params.nchannels)
            if frame_count == 0:
                return
            chunk_path = output_dir / f"chunk-{index:04d}.wav"
            with wave.open(str(chunk_path), "wb") as writer:
                writer.setparams(params)
                writer.writeframes(frames)
            yield offset_frames * 1000 // rate, frame_count * 1000 // rate, chunk_path
            offset_frames += frame_count
            index += 1


def _is_wav(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
    except OSError:
        return False
    return header[:4] == b"RIFF" and header[8:12] == b"WAVE"


def _convert_to_wav(source: Path, target: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        raise ValueError("ffmpeg is required to read non-WAV audio")
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        env=media_tool_env(),
    )
    if result.returncode != 0 or not target.exists():
        raise ValueError("audio could not be converted to WAV")
    return target


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, EmotionResponseInvalid):
        return "provider_malformed_response"
    if isinstance(exc, ModelProvidersExhausted) and exc.failures:
        return str(exc.failures[0].get("code") or exc.code)
    if isinstance(exc, ModelGatewayError):
        return exc.code
    return classify_provider_exception(exc).code
