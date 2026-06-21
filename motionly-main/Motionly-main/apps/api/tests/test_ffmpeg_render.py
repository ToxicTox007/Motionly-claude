from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.errors import ApiError
from app.schemas.renders import CutSegment, RenderSpecs
from app.services import ffmpeg_render
from app.services.ffmpeg_render import FFmpegCaptionRenderer
from app.services.local_storage import LocalObjectStorage
from app.services.render_jobs import _audio_mix_from_manifest


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


def test_ffmpeg_renderer_uses_demucs_dialogue_stem_and_retimes_cuts(
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
    vocal_stem = tmp_path / "vocals.wav"
    vocal_stem.write_bytes(b"wav")

    FFmpegCaptionRenderer().render(
        source_path=tmp_path / "source.mp4",
        ass_path=None,
        output_path=tmp_path / "out.mp4",
        specs=RenderSpecs(),
        dialogue_source=vocal_stem,
        keep_segments=[CutSegment(start_ms=500, end_ms=1500, reason="keep dialogue")],
    )

    command = captured["command"]
    graph = command[command.index("-filter_complex") + 1]
    assert str(vocal_stem) in command
    assert "[1:a]atrim=start=0.500:end=1.500" in graph
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "[dialoguecut]"


def test_ffmpeg_renderer_uses_nvenc_and_renders_free_plan_watermark(
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
        output_path=tmp_path / "out.mp4",
        specs=RenderSpecs(),
        video_encoder="h264_nvenc",
        watermark=True,
    )

    command = captured["command"]
    assert command[command.index("-c:v") + 1] == "h264_nvenc"
    assert command[command.index("-preset") + 1] == "p4"
    video_filter = command[command.index("-vf") + 1]
    assert "drawtext=text='MOTIONLY'" in video_filter


def test_manual_sfx_data_url_is_materialized_for_ffmpeg(tmp_path: Path) -> None:
    mix = _audio_mix_from_manifest(
        {
            "render_props": {
                "editPlan": {
                    "manual_elements": [
                        {
                            "type": "sfx",
                            "start_ms": 1250,
                            "properties": {
                                "assetUrl": "data:audio/wav;base64,UklGRg==",
                            },
                        }
                    ]
                }
            }
        },
        storage=LocalObjectStorage(root=tmp_path),
    )

    assert mix["sfx_cue_ms"] == [1250.0]
    assert mix["sfx_intensity"] == 0.8
    assert Path(mix["sfx_sources"][0]).read_bytes() == b"RIFF"


def test_demucs_vocal_stem_is_resolved_from_render_manifest(tmp_path: Path) -> None:
    storage = LocalObjectStorage(root=tmp_path)
    storage.write_object(bucket="raw-uploads", path="stems/vocals.wav", content=b"vocal")

    mix = _audio_mix_from_manifest(
        {
            "render_props": {
                "sourceStems": {
                    "vocals": {"bucket": "raw-uploads", "path": "stems/vocals.wav"}
                }
            }
        },
        storage=storage,
    )

    assert Path(mix["dialogue_source"]).read_bytes() == b"vocal"


def test_ffmpeg_renderer_mixes_sfx_without_unconnected_ducking_branch(
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
        output_path=tmp_path / "out.mp4",
        specs=RenderSpecs(),
        sfx_sources=[tmp_path / "impact.wav"],
        sfx_intensity=0.8,
        sfx_cue_ms=[500],
    )

    filter_graph = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert "asplit" not in filter_graph
    assert "adelay=500|500" in filter_graph


def test_ffmpeg_renderer_supports_fit_and_custom_framing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command, **_kwargs):
        captured.setdefault("commands", []).append(command)
        Path(command[-1]).write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(ffmpeg_render, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg_render.subprocess, "run", fake_run)

    FFmpegCaptionRenderer().render(
        source_path=tmp_path / "source.mp4",
        ass_path=None,
        output_path=tmp_path / "exports" / "fit.mp4",
        specs=RenderSpecs(width=1080, height=1920),
        framing={
            "output_aspect": "9:16",
            "fit_mode": "fit",
            "crop": {"x": 0.5, "y": 0.5, "zoom": 1},
            "padding_color": "#111111",
        },
    )
    fit_filter = captured["commands"][0][captured["commands"][0].index("-vf") + 1]
    assert "force_original_aspect_ratio=decrease" in fit_filter
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x111111" in fit_filter

    FFmpegCaptionRenderer().render(
        source_path=tmp_path / "source.mp4",
        ass_path=None,
        output_path=tmp_path / "exports" / "custom.mp4",
        specs=RenderSpecs(width=1920, height=1080),
        framing={
            "output_aspect": "original",
            "fit_mode": "custom",
            "crop": {"x": 0.25, "y": 0.75, "zoom": 1.4},
            "padding_color": "#000000",
        },
    )
    custom_filter = captured["commands"][1][captured["commands"][1].index("-vf") + 1]
    assert "force_original_aspect_ratio=increase" in custom_filter
    assert "scale=iw*1.4000:ih*1.4000" in custom_filter
    assert "crop=1920:1080:(iw-ow)*0.2500:(ih-oh)*0.7500" in custom_filter


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
        manual_elements=[
            {
                "id": "title-1",
                "type": "text",
                "name": "Title",
                "start_ms": 500,
                "end_ms": 2500,
                "x_pct": 50,
                "y_pct": 20,
                "width_pct": 50,
                "height_pct": 12,
                "opacity": 1,
                "properties": {"content": "Launch day", "color": "#ffffff"},
            },
            {
                "id": "shape-1",
                "type": "shape",
                "name": "Rectangle",
                "start_ms": 500,
                "end_ms": 2500,
                "x_pct": 50,
                "y_pct": 80,
                "width_pct": 30,
                "height_pct": 10,
                "opacity": 0.8,
                "properties": {"backgroundColor": "#14b8a6"},
            },
        ],
    )

    command = captured["command"]
    filter_graph = command[command.index("-vf") + 1]
    assert "lut3d=file=" in filter_graph
    assert "interp=tetrahedral" in filter_graph
    assert "eq=contrast=" in filter_graph
    assert "drawtext=text='Launch day'" in filter_graph
    assert "drawbox=x=iw*0.5000" in filter_graph
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
