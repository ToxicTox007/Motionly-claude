"""Task 27: audio tooling for the music catalog importer.

* ``synthesize_test_track`` writes a deterministic sine-wave WAV (plain Python)
  used as a generated "Test track" (allowed only outside production).
* ``build_loudnorm_command`` normalises loudness with ffmpeg ``loudnorm`` (EBU
  R128) before analysis, so every catalogued track has a consistent level.
* ``LibrosaAudioAnalyzer`` extracts duration, tempo, beat positions and energy
  with librosa. librosa is heavy (numba/llvmlite) and is NOT installed in the
  API request-path image. The choice (documented): analysis runs in the CPU
  worker image. librosa is a PINNED optional extra (``music-analysis`` in
  apps/api/pyproject.toml, ``librosa==0.10.2.post1``); its hash-locked pins are
  produced by regenerating apps/api/requirements-ml.lock with pip-compile and
  ``--extra=music-analysis`` (see pyproject). No unpinned dependency is ever
  used. The import here is lazy, with an actionable error if the analyzer is
  invoked where librosa is unavailable.
"""

from __future__ import annotations

import array
import hashlib
import math
import subprocess
import sys
import wave

from app.services.music_catalog_import import AudioAnalysis

_FFMPEG = "ffmpeg"


_SAMPLE_RATE = 44100
_AMPLITUDE = 0.3


def synthesize_test_track(*, frequency: int, duration_s: int, output_path: str) -> str:
    """Write a deterministic stereo 16-bit 44.1 kHz sine bed as WAV.

    Plain Python, no ffmpeg: the generated catalog is seeded from the API
    container, which ships no media tools (test_control_media_boundary.py).
    """
    frames = _SAMPLE_RATE * duration_s
    step = 2 * math.pi * frequency / _SAMPLE_RATE
    peak = int(32767 * _AMPLITUDE)
    samples = array.array("h")
    for index in range(frames):
        value = int(round(peak * math.sin(step * index)))
        samples.extend((value, value))
    if sys.byteorder != "little":  # WAV PCM is little-endian.
        samples.byteswap()
    with wave.open(output_path, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(_SAMPLE_RATE)
        handle.writeframes(samples.tobytes())
    return output_path


def build_loudnorm_command(*, input_path: str, output_path: str) -> list[str]:
    # EBU R128 target used widely for streaming beds: -14 LUFS, -1 dBTP, 11 LU.
    return [
        _FFMPEG,
        "-y",
        "-i",
        input_path,
        "-af",
        "loudnorm=I=-14:TP=-1.0:LRA=11",
        "-ar",
        "44100",
        output_path,
    ]


def normalize_loudness(*, input_path: str, output_path: str) -> str:
    subprocess.run(
        build_loudnorm_command(input_path=input_path, output_path=output_path),
        capture_output=True,
        check=True,
        timeout=300,
    )
    return output_path


class LibrosaAudioAnalyzer:
    """Analyze an audio file with librosa (lazy import; CPU worker image only)."""

    def analyze(self, audio_path: str) -> AudioAnalysis:
        try:
            import librosa  # noqa: PLC0415  (lazy: keep librosa out of the API image)
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised via monkeypatch
            raise RuntimeError(
                "librosa is required for music analysis and is only installed in the "
                "CPU worker image (apps/worker/requirements/analysis.lock). Run "
                "`python -m app.ops.import_music` there, or inject an analyzer."
            ) from exc

        y, sr = librosa.load(audio_path, mono=True)
        duration_s = librosa.get_duration(y=y, sr=sr)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        rms = librosa.feature.rms(y=y)
        energy = float(rms.mean()) if rms.size else 0.0
        return AudioAnalysis(
            duration_ms=int(round(duration_s * 1000)),
            tempo_bpm=float(tempo),
            beat_positions_ms=[float(round(t * 1000, 3)) for t in beat_times.tolist()],
            energy=max(0.0, min(1.0, energy)),
        )


def _wav_duration_ms(audio_path: str) -> int:
    """A WAV file's exact duration in milliseconds, from its header."""
    with wave.open(audio_path, "rb") as handle:
        return int(round(handle.getnframes() * 1000 / handle.getframerate()))


class GeneratedTrackAnalyzer:
    """Analyzer for GENERATED test tracks — no librosa required.

    The generated beds are deterministic sine waves, so a full spectral analysis
    is unnecessary (and we must not pull librosa into the API/seed image). The
    duration is read from the WAV header (the one real measurement that matters
    for the editor timeline); tempo, beat grid and energy are derived deterministically
    from the track's own path so a seeded catalog is stable across runs and still
    ranks sensibly in the Audio tab.
    """

    def __init__(self, *, tempo_bpm: float = 112.0, energy: float = 0.4) -> None:
        self.tempo_bpm = tempo_bpm
        self.energy = energy

    def analyze(self, audio_path: str) -> AudioAnalysis:
        duration_ms = _wav_duration_ms(audio_path)
        digest = int(hashlib.sha256(audio_path.encode()).hexdigest(), 16)
        # Spread tempo across a musical range (90–136 bpm) so ranking varies.
        tempo = 90.0 + float(digest % 46)
        beat_interval_ms = 60_000.0 / tempo
        beats: list[float] = []
        position = 0.0
        while position < max(duration_ms, beat_interval_ms):
            beats.append(round(position, 3))
            position += beat_interval_ms
        # Energy varies mildly per track but stays in a sensible bed range.
        energy = max(0.1, min(0.9, 0.3 + (digest % 50) / 100.0))
        return AudioAnalysis(
            duration_ms=duration_ms,
            tempo_bpm=tempo,
            beat_positions_ms=beats or [0.0],
            energy=energy,
        )
