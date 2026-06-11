from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.errors import ApiError
from app.schemas.renders import CutSegment, RenderSpecs
from app.services import ffmpeg_render
from app.services.ffmpeg_render import FFmpegCaptionRenderer


def test_ffmpeg_renderer_maps_primary_video_and_optional_audio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(ffmpeg_render, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg_render.subprocess, "run", fake_run)

    FFmpegCaptionRenderer().render(
        source_path=tmp_path / "source.mp4",
        ass_path=tmp_path / "captions.ass",
        output_path=tmp_path / "exports" / "out.mp4",
        specs=RenderSpecs(),
    )

    command = captured["command"]
    assert command[:4] == ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
    assert command[command.index("-map") + 1] == "0:v:0"
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "0:a?"
    assert command[command.index("-preset") + 1] == "veryfast"
    assert command[command.index("-threads") + 1] == "2"
    assert "-shortest" in command


def test_ffmpeg_renderer_mixes_music_with_ducking_and_limiter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(ffmpeg_render, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg_render.subprocess, "run", fake_run)

    FFmpegCaptionRenderer().render(
        source_path=tmp_path / "source.mp4",
        ass_path=None,
        output_path=tmp_path / "exports" / "out.mp4",
        specs=RenderSpecs(),
        music_source="https://cdn.example.com/music.mp3",
        music_volume=0.2,
        sfx_sources=["https://cdn.example.com/pop.wav"],
        sfx_intensity=0.5,
        sfx_cue_ms=[750],
    )

    command = captured["command"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "-stream_loop" in command
    assert "sidechaincompress" in filter_graph
    assert "volume=0.200" in filter_graph
    assert "adelay=750|750" in filter_graph
    assert "alimiter=limit=0.95" in filter_graph
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "[aout]"


def test_ffmpeg_renderer_trims_and_concats_keep_segments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(ffmpeg_render, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg_render.subprocess, "run", fake_run)

    FFmpegCaptionRenderer().render(
        source_path=tmp_path / "source.mp4",
        ass_path=tmp_path / "captions.ass",
        output_path=tmp_path / "exports" / "out.mp4",
        specs=RenderSpecs(),
        keep_segments=[
            CutSegment(start_ms=1000, end_ms=2500, reason="speech"),
            CutSegment(start_ms=5000, end_ms=6500, reason="speech"),
        ],
    )

    command = captured["command"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "trim=start=1.000:end=2.500" in filter_graph
    assert "trim=start=5.000:end=6.500" in filter_graph
    assert "concat=n=2:v=1:a=0[vcut]" in filter_graph
    assert "concat=n=2:v=0:a=1[acut]" in filter_graph
    assert command[command.index("-map") + 1] == "[vout]"
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "[acut]"


def test_ffmpeg_renderer_preserves_stderr_tail_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(ffmpeg_render, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        ffmpeg_render.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stderr="line one\n" + ("x" * 1300),
        ),
    )

    with pytest.raises(ApiError) as exc_info:
        FFmpegCaptionRenderer().render(
            source_path=tmp_path / "source.mp4",
            ass_path=tmp_path / "captions.ass",
            output_path=tmp_path / "out.mp4",
            specs=RenderSpecs(),
        )

    assert exc_info.value.code == "ffmpeg_render_failed"
    assert exc_info.value.details["returncode"] == 1
    assert str(exc_info.value.details["stderr_tail"]).endswith("x" * 100)
    assert len(str(exc_info.value.details["stderr_tail"])) == 1200


def test_ffmpeg_renderer_overlays_broll_before_caption_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(ffmpeg_render, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg_render.subprocess, "run", fake_run)

    FFmpegCaptionRenderer().render(
        source_path=tmp_path / "source.mp4",
        ass_path=tmp_path / "captions.ass",
        output_path=tmp_path / "exports" / "out.mp4",
        specs=RenderSpecs(),
        broll_assets=[
            {
                "id": "asset-1",
                "kind": "video",
                "src": "https://cdn.example.com/broll.mp4",
                "start_ms": 1200,
                "end_ms": 3600,
                "crossfade_ms": 260,
            }
        ],
    )

    command = captured["command"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "-stream_loop" in command
    assert "https://cdn.example.com/broll.mp4" in command
    assert "overlay=enable='between(t,1.200,3.600)'" in filter_graph
    assert filter_graph.index("overlay=") < filter_graph.index("ass=")
    assert command[command.index("-map") + 1] == "[vout]"


def test_ffmpeg_renderer_applies_effects_before_caption_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(ffmpeg_render, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg_render.subprocess, "run", fake_run)

    FFmpegCaptionRenderer().render(
        source_path=tmp_path / "source.mp4",
        ass_path=tmp_path / "captions.ass",
        output_path=tmp_path / "exports" / "out.mp4",
        specs=RenderSpecs(),
        effects_config={
            "preset": "champagne_gold",
            "lut_intensity": 0.8,
            "texture": {
                "grain": 0.2,
                "vignette": 0.1,
                "light_leak": 0.08,
                "anamorphic_flare": True,
            },
            "motion_effects": ["motion_blur", "glitch"],
            "background_removal": {"enabled": True, "feather_px": 24},
            "gradient_ramp": {
                "enabled": True,
                "start_color": "#111827",
                "end_color": "#f59e0b",
                "opacity": 0.35,
            },
        },
    )

    command = captured["command"]
    filter_graph = command[command.index("-vf") + 1]
    assert "eq=contrast=" in filter_graph
    assert "noise=alls=" in filter_graph
    assert "drawbox=x=0:y=0:w=iw:h=ih:color=0x111827" in filter_graph
    assert "rgbashift=" in filter_graph
    assert "boxblur=" in filter_graph
    assert filter_graph.index("eq=") < filter_graph.index("ass=")


def test_ffmpeg_renderer_reports_missing_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(ffmpeg_render, "which", lambda _name: None)

    with pytest.raises(ApiError) as exc_info:
        FFmpegCaptionRenderer().render(
            source_path=tmp_path / "source.mp4",
            ass_path=tmp_path / "captions.ass",
            output_path=tmp_path / "out.mp4",
            specs=RenderSpecs(),
        )

    assert exc_info.value.code == "ffmpeg_unavailable"
