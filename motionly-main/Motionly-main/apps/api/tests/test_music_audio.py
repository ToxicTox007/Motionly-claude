"""Task 27: generated test-track synthesis, ffmpeg loudnorm, and the analyzers.

Generated test tracks are synthesised in plain Python (no ffmpeg): the seed runs
in the API container, which deliberately ships no media tools
(test_control_media_boundary.py). Loudness normalisation of real imports still
uses ffmpeg in the worker image.
"""

from __future__ import annotations

import array
import itertools
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from app.services.music_audio import (
    GeneratedTrackAnalyzer,
    LibrosaAudioAnalyzer,
    build_loudnorm_command,
    synthesize_test_track,
)

_FFMPEG = shutil.which("ffmpeg")


def _no_subprocess(monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("generated test tracks must not need an external binary")

    monkeypatch.setattr(subprocess, "run", refuse)


def test_synthesis_writes_a_deterministic_stereo_sine_without_ffmpeg(
    tmp_path: Path, monkeypatch
) -> None:
    _no_subprocess(monkeypatch)
    first, second = tmp_path / "a.wav", tmp_path / "b.wav"
    synthesize_test_track(frequency=440, duration_s=3, output_path=str(first))
    synthesize_test_track(frequency=440, duration_s=3, output_path=str(second))
    assert first.read_bytes() == second.read_bytes()
    with wave.open(str(first), "rb") as handle:
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (
            2,
            2,
            44100,
        )
        assert handle.getnframes() == 3 * 44100
        frames = handle.readframes(44100)
    samples = array.array("h", frames)[::2]  # left channel, one second
    # 440 Hz: 880 zero crossings in one second, and an audible amplitude.
    crossings = sum(1 for a, b in itertools.pairwise(samples) if (a < 0) != (b < 0))
    assert 875 <= crossings <= 885
    assert max(samples) > 8000


def test_generated_track_analyzer_reads_the_exact_duration_without_ffprobe(
    tmp_path: Path, monkeypatch
) -> None:
    out = tmp_path / "bed.wav"
    synthesize_test_track(frequency=330, duration_s=2, output_path=str(out))
    _no_subprocess(monkeypatch)
    assert GeneratedTrackAnalyzer().analyze(str(out)).duration_ms == 2000


def test_loudnorm_command_applies_the_loudnorm_filter() -> None:
    cmd = build_loudnorm_command(input_path="/tmp/in.wav", output_path="/tmp/out.wav")
    assert "ffmpeg" in cmd[0]
    joined = " ".join(cmd)
    assert "loudnorm" in joined
    assert cmd[-1] == "/tmp/out.wav"


@pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg not installed")
def test_loudnorm_runs_end_to_end(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    synthesize_test_track(frequency=440, duration_s=1, output_path=str(src))
    dst = tmp_path / "norm.wav"
    cmd = build_loudnorm_command(input_path=str(src), output_path=str(dst))
    subprocess.run(cmd, capture_output=True, check=True, timeout=60)
    assert dst.exists() and dst.stat().st_size > 0


def test_librosa_analyzer_errors_clearly_when_librosa_missing(monkeypatch) -> None:
    analyzer = LibrosaAudioAnalyzer()
    # Force the lazy import to fail to prove the error is actionable.
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "librosa":
            raise ModuleNotFoundError("No module named 'librosa'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError) as excinfo:
        analyzer.analyze("/nonexistent.wav")
    assert "librosa" in str(excinfo.value).lower()


def test_generated_track_analyzer_is_deterministic_without_librosa(monkeypatch) -> None:
    # It must never import librosa, and must be stable for the same path.
    import app.services.music_audio as music_audio

    monkeypatch.setattr(music_audio, "_wav_duration_ms", lambda _path: 30_000)
    analyzer = GeneratedTrackAnalyzer()
    first = analyzer.analyze("test-tracks/test-track-calm.wav")
    second = analyzer.analyze("test-tracks/test-track-calm.wav")
    assert first == second
    assert first.duration_ms == 30_000
    assert 90.0 <= first.tempo_bpm <= 136.0
    assert 0.1 <= first.energy <= 0.9
    assert first.beat_positions_ms and first.beat_positions_ms[0] == 0.0
