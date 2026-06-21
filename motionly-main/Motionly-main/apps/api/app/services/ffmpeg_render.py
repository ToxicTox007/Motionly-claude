import subprocess
import time
from pathlib import Path
from shutil import which
from typing import Any

from app.api.errors import ApiError
from app.schemas.renders import CutSegment, RenderSpecs

AudioSource = str | Path


class FFmpegCaptionRenderer:
    def render(
        self,
        *,
        source_path: Path,
        ass_path: Path | None,
        output_path: Path,
        specs: RenderSpecs,
        broll_assets: list[dict[str, Any]] | None = None,
        effects_config: dict[str, object] | None = None,
        manual_elements: list[dict[str, object]] | None = None,
        framing: dict[str, object] | None = None,
        music_source: AudioSource | None = None,
        music_volume: float = 0,
        sfx_sources: list[AudioSource] | None = None,
        sfx_intensity: float = 0,
        sfx_cue_ms: list[float] | None = None,
        duck_music: bool = True,
        keep_segments: list[CutSegment] | None = None,
        video_encoder: str = "libx264",
        watermark: bool = False,
    ) -> tuple[int, int]:
        if which("ffmpeg") is None:
            raise ApiError(
                502,
                "ffmpeg_unavailable",
                "FFmpeg is not available in the render environment.",
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        broll_assets = [asset for asset in broll_assets or [] if _is_renderable_asset(asset)]
        safe_keep_segments = _normalize_keep_segments(keep_segments)
        filter_graph, video_map, source_audio_map = _build_video_filter_graph(
            specs=specs,
            ass_path=ass_path,
            broll_assets=broll_assets,
            effects_config=effects_config,
            manual_elements=manual_elements or [],
            framing=framing,
            keep_segments=safe_keep_segments,
            watermark=watermark,
        )
        active_sfx_sources = (sfx_sources or [])[:6] if sfx_intensity > 0 else []
        has_music = music_source is not None and music_volume > 0
        audio_filter = _build_audio_filter_graph(
            has_music=has_music,
            first_music_input=1 + len(broll_assets),
            sfx_count=len(active_sfx_sources),
            music_volume=music_volume,
            sfx_intensity=sfx_intensity,
            sfx_cue_ms=sfx_cue_ms or [],
            duck_music=duck_music,
            source_audio_label=source_audio_map or "[0:a]",
        )
        uses_filter_complex = bool(broll_assets or audio_filter or source_audio_map)
        if audio_filter and not broll_assets and not source_audio_map:
            filter_graph = f"[0:v]{filter_graph}[vout]"
            video_map = "[vout]"
        if audio_filter:
            filter_graph = f"{filter_graph};{audio_filter}"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            str(source_path),
        ]
        for asset in broll_assets:
            command.extend(["-stream_loop", "-1", "-i", str(asset["src"])])
        if has_music:
            command.extend(["-stream_loop", "-1", "-i", str(music_source)])
        for source in active_sfx_sources:
            command.extend(["-i", str(source)])
        command.extend(
            [
                "-filter_complex" if uses_filter_complex else "-vf",
                filter_graph,
                "-map",
                video_map,
                "-map",
                "[aout]" if audio_filter else (source_audio_map or "0:a?"),
                "-r",
                str(specs.fps),
                *_video_encoder_args(video_encoder),
                "-pix_fmt",
                specs.pixel_format,
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        started = time.perf_counter()
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)
        benchmark_ms = round((time.perf_counter() - started) * 1000)
        if result.returncode != 0:
            stderr_tail = _tail_for_diagnostics(getattr(result, "stderr", ""))
            stdout_tail = _tail_for_diagnostics(getattr(result, "stdout", ""))
            raise ApiError(
                502,
                "ffmpeg_render_failed",
                "FFmpeg could not render the export.",
                details={
                    "returncode": result.returncode,
                    "stderr_tail": stderr_tail,
                    "stdout_tail": stdout_tail,
                },
            )
        file_size = output_path.stat().st_size if output_path.exists() else 0
        if file_size <= 0:
            raise ApiError(502, "render_output_empty", "Rendered export was empty.")
        return benchmark_ms, file_size


def _video_encoder_args(video_encoder: str) -> list[str]:
    if video_encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq",
            "-rc", "vbr", "-cq", "21", "-b:v", "0", "-spatial-aq", "1",
        ]
    if video_encoder == "hevc_nvenc":
        return [
            "-c:v", "hevc_nvenc", "-preset", "p4", "-tune", "hq",
            "-rc", "vbr", "-cq", "23", "-b:v", "0", "-spatial-aq", "1",
        ]
    return ["-c:v", "libx264", "-preset", "veryfast", "-threads", "2"]


def _ffmpeg_filter_path(path: Path) -> str:
    escaped = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"'{escaped}'"


def _build_video_filter_graph(
    *,
    specs: RenderSpecs,
    ass_path: Path | None,
    broll_assets: list[dict[str, Any]],
    effects_config: dict[str, object] | None,
    manual_elements: list[dict[str, object]],
    framing: dict[str, object] | None,
    keep_segments: list[CutSegment],
    watermark: bool,
) -> tuple[str, str, str | None]:
    source_steps = _source_framing_filters(specs=specs, framing=framing)
    source_steps.extend(_effects_filters(effects_config))
    source_steps.extend(_manual_element_filters(manual_elements, specs=specs))
    if watermark:
        source_steps.append(
            "drawtext=text='MOTIONLY':fontsize=h/24:fontcolor=white@0.72:"
            "x=w-text_w-w*0.035:y=h-text_h-h*0.025:"
            "box=1:boxcolor=black@0.38:boxborderw=10"
        )
    cut_filters, source_video_label, source_audio_label = _cut_filter_graph(keep_segments)
    if not broll_assets:
        if ass_path is not None:
            source_steps.append(f"ass={_ffmpeg_filter_path(ass_path)}")
        if cut_filters:
            return (
                f"{cut_filters};{source_video_label}{','.join(source_steps)}[vout]",
                "[vout]",
                source_audio_label,
            )
        return ",".join(source_steps), "0:v:0", None

    filters = [cut_filters] if cut_filters else []
    filters.append(f"{source_video_label}{','.join(source_steps)}[base0]")
    current = "base0"
    for index, asset in enumerate(broll_assets, start=1):
        start_ms = max(0, int(asset["start_ms"]))
        end_ms = max(start_ms + 1, int(asset["end_ms"]))
        start_s = start_ms / 1000
        end_s = end_ms / 1000
        duration_s = max(0.1, (end_ms - start_ms) / 1000)
        crossfade_s = min(
            max(0.08, float(asset.get("crossfade_ms") or 260) / 1000),
            max(0.08, duration_s / 3),
        )
        broll_label = f"broll{index}"
        next_label = f"base{index}"
        filters.append(
            f"[{index}:v]"
            f"trim=duration={duration_s:.3f},"
            "setpts=PTS-STARTPTS,"
            f"scale={specs.width}:{specs.height}:force_original_aspect_ratio=increase,"
            f"crop={specs.width}:{specs.height},setsar=1,format=yuva420p,"
            f"fade=t=in:st=0:d={crossfade_s:.3f}:alpha=1,"
            f"fade=t=out:st={max(0, duration_s - crossfade_s):.3f}:"
            f"d={crossfade_s:.3f}:alpha=1,"
            f"setpts=PTS+{start_s:.3f}/TB"
            f"[{broll_label}]"
        )
        filters.append(
            f"[{current}][{broll_label}]"
            f"overlay=enable='between(t,{start_s:.3f},{end_s:.3f})':"
            "eof_action=pass:shortest=0"
            f"[{next_label}]"
        )
        current = next_label

    if ass_path is not None:
        filters.append(f"[{current}]ass={_ffmpeg_filter_path(ass_path)}[vout]")
        return ";".join(filters), "[vout]", source_audio_label
    return ";".join(filters), f"[{current}]", source_audio_label


def _cut_filter_graph(keep_segments: list[CutSegment]) -> tuple[str, str, str | None]:
    if not keep_segments:
        return "", "[0:v]", None

    video_parts = []
    audio_parts = []
    video_labels = []
    audio_labels = []
    for index, segment in enumerate(keep_segments):
        start_s = segment.start_ms / 1000
        end_s = segment.end_ms / 1000
        video_label = f"vcut{index}"
        audio_label = f"acut{index}"
        video_parts.append(
            f"[0:v]trim=start={start_s:.3f}:end={end_s:.3f},"
            f"setpts=PTS-STARTPTS[{video_label}]"
        )
        audio_parts.append(
            f"[0:a]atrim=start={start_s:.3f}:end={end_s:.3f},"
            f"asetpts=PTS-STARTPTS[{audio_label}]"
        )
        video_labels.append(f"[{video_label}]")
        audio_labels.append(f"[{audio_label}]")

    if len(keep_segments) == 1:
        return ";".join([*video_parts, *audio_parts]), video_labels[0], audio_labels[0]

    video_parts.append(
        f"{''.join(video_labels)}concat=n={len(keep_segments)}:v=1:a=0[vcut]"
    )
    audio_parts.append(
        f"{''.join(audio_labels)}concat=n={len(keep_segments)}:v=0:a=1[acut]"
    )
    return ";".join([*video_parts, *audio_parts]), "[vcut]", "[acut]"


def _effects_filters(effects_config: dict[str, object] | None) -> list[str]:
    if not effects_config:
        return []

    texture = _dict(effects_config.get("texture"))
    gradient = _dict(effects_config.get("gradient_ramp"))
    background = _dict(effects_config.get("background_removal"))
    motion_effects = {
        str(effect)
        for effect in effects_config.get("motion_effects", [])
        if isinstance(effect, str)
    }
    filters = [
        _preset_filter(
            str(effects_config.get("preset", "")),
            _float(effects_config.get("lut_intensity"), 0.75),
        )
    ]

    if _float(texture.get("grain"), 0) > 0:
        strength = max(1, min(12, round(_float(texture.get("grain"), 0) * 18)))
        filters.append(f"noise=alls={strength}:allf=t+u")
    if _float(texture.get("vignette"), 0) > 0:
        filters.append("vignette=PI/4")
    if "motion_blur" in motion_effects:
        filters.append("tmix=frames=3")
    if "glitch" in motion_effects:
        filters.append("rgbashift=rh=3:gh=-2:bh=2")
    if bool(gradient.get("enabled")):
        opacity = min(0.14, _float(gradient.get("opacity"), 0.28) * 0.35)
        start_color = _ffmpeg_color(str(gradient.get("start_color", "#0f172a")))
        end_color = _ffmpeg_color(str(gradient.get("end_color", "#f59e0b")))
        filters.append(
            f"drawbox=x=0:y=0:w=iw:h=ih:color={start_color}@{opacity:.3f}:t=fill"
        )
        filters.append(
            f"drawbox=x=0:y=ih*0.55:w=iw:h=ih*0.45:color={end_color}@{opacity:.3f}:t=fill"
        )
    if _float(texture.get("light_leak"), 0) > 0 or "flash" in motion_effects:
        opacity = min(0.16, _float(texture.get("light_leak"), 0.08) * 0.55 + 0.03)
        filters.append(
            f"drawbox=x=iw*0.62:y=0:w=iw*0.38:h=ih:color=0xffd08a@{opacity:.3f}:t=fill"
        )
    if bool(texture.get("anamorphic_flare")):
        filters.append("drawbox=x=0:y=ih*0.43:w=iw:h=26:color=white@0.105:t=fill")
    if bool(background.get("enabled")):
        feather = min(16, max(0, _float(background.get("feather_px"), 18) / 6))
        filters.append(
            f"boxblur=luma_radius={feather:.1f}:luma_power=1:enable='lt(mod(t\\,2)\\,0.28)'"
        )
    return filters


def _source_framing_filters(
    *,
    specs: RenderSpecs,
    framing: dict[str, object] | None,
) -> list[str]:
    fit_mode = str((framing or {}).get("fit_mode") or "fill")
    crop = _dict((framing or {}).get("crop"))
    zoom = _float(crop.get("zoom"), 1)
    x = min(1, max(0, _float(crop.get("x"), 0.5)))
    y = min(1, max(0, _float(crop.get("y"), 0.5)))
    padding_color = _ffmpeg_color(str((framing or {}).get("padding_color") or "#000000"))
    if fit_mode == "fit":
        return [
            f"scale={specs.width}:{specs.height}:force_original_aspect_ratio=decrease",
            f"pad={specs.width}:{specs.height}:(ow-iw)/2:(oh-ih)/2:color={padding_color}",
            "setsar=1",
        ]

    safe_zoom = min(3, max(1, zoom if fit_mode == "custom" else 1))
    return [
        f"scale={specs.width}:{specs.height}:force_original_aspect_ratio=increase",
        f"scale=iw*{safe_zoom:.4f}:ih*{safe_zoom:.4f}",
        f"crop={specs.width}:{specs.height}:(iw-ow)*{x:.4f}:(ih-oh)*{y:.4f}",
        "setsar=1",
    ]


def _preset_filter(preset: str, intensity: float) -> str:
    amount = max(0.5, min(1, intensity))
    values = {
        "vogue_luxury": (1 + amount * 0.16, 0.01 + amount * 0.02, 1 - amount * 0.06),
        "champagne_gold": (1 + amount * 0.1, 0.02 + amount * 0.03, 1 + amount * 0.16),
        "dubai_influencer": (1 + amount * 0.08, 0.02 + amount * 0.02, 1 + amount * 0.24),
        "old_money": (1 + amount * 0.12, -0.01, 1 - amount * 0.08),
        "clean_founder": (1 + amount * 0.04, 0.02 + amount * 0.03, 1 - amount * 0.1),
        "cinematic_hotel": (1 + amount * 0.18, -0.01, 1 + amount * 0.08),
    }
    contrast, brightness, saturation = values.get(preset, (1.04, 0.0, 1.04))
    eq_filter = (
        f"eq=contrast={contrast:.3f}:brightness={brightness:.3f}:"
        f"saturation={saturation:.3f}"
    )
    lut_path = Path(__file__).with_name("luts") / f"{preset}.cube"
    if lut_path.is_file():
        return f"lut3d=file={_ffmpeg_filter_path(lut_path)}:interp=tetrahedral,{eq_filter}"
    return eq_filter


def _manual_element_filters(
    elements: list[dict[str, object]],
    *,
    specs: RenderSpecs,
) -> list[str]:
    filters: list[str] = []
    for element in elements[:80]:
        element_type = str(element.get("type") or "")
        if element_type not in {"text", "caption", "overlay", "shape", "graphic"}:
            continue
        start_s = max(0, _float(element.get("start_ms"), 0) / 1000)
        end_s = max(start_s + 0.001, _float(element.get("end_ms"), 0) / 1000)
        x_pct = min(100, max(0, _float(element.get("x_pct"), 50))) / 100
        y_pct = min(100, max(0, _float(element.get("y_pct"), 50))) / 100
        width_pct = min(100, max(1, _float(element.get("width_pct"), 40))) / 100
        height_pct = min(100, max(1, _float(element.get("height_pct"), 12))) / 100
        opacity = min(1, max(0, _float(element.get("opacity"), 1)))
        properties = _dict(element.get("properties"))
        enable = f"between(t\\,{start_s:.3f}\\,{end_s:.3f})"
        if element_type in {"overlay", "shape"}:
            color = _ffmpeg_color(str(properties.get("backgroundColor") or "#14b8a6"))
            filters.append(
                f"drawbox=x=iw*{x_pct:.4f}-iw*{width_pct / 2:.4f}:"
                f"y=ih*{y_pct:.4f}-ih*{height_pct / 2:.4f}:"
                f"w=iw*{width_pct:.4f}:h=ih*{height_pct:.4f}:"
                f"color={color}@{opacity:.3f}:t=fill:enable='{enable}'"
            )
        if element_type in {"text", "caption", "graphic"}:
            text = _ffmpeg_drawtext(
                str(properties.get("content") or element.get("name") or "")
            )
            if not text:
                continue
            font_size = max(
                12,
                min(
                    specs.height // 4,
                    round(
                        _float(properties.get("fontSize"), 32)
                        * specs.height
                        / 1080
                    ),
                ),
            )
            color = _ffmpeg_color(str(properties.get("color") or "#ffffff"))
            filters.append(
                f"drawtext=text='{text}':fontsize={font_size}:fontcolor={color}@{opacity:.3f}:"
                f"x=w*{x_pct:.4f}-text_w/2:y=h*{y_pct:.4f}-text_h/2:"
                f"enable='{enable}'"
            )
    return filters


def _ffmpeg_drawtext(value: str) -> str:
    return (
        value[:300]
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
        .replace("\n", " ")
    )


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _ffmpeg_color(value: str) -> str:
    if len(value) == 7 and value.startswith("#"):
        return f"0x{value[1:]}"
    return "0x0f172a"


def _is_renderable_asset(asset: dict[str, Any]) -> bool:
    src = str(asset.get("src") or "")
    if not src or src.startswith("fixture://"):
        return False
    if asset.get("kind") != "video":
        return False
    return asset.get("start_ms") is not None and asset.get("end_ms") is not None


def _build_audio_filter_graph(
    *,
    has_music: bool,
    first_music_input: int,
    sfx_count: int,
    music_volume: float,
    sfx_intensity: float,
    sfx_cue_ms: list[float],
    duck_music: bool,
    source_audio_label: str,
) -> str | None:
    active_sfx_count = min(6, sfx_count) if sfx_intensity > 0 else 0
    if not has_music and active_sfx_count == 0:
        return None

    if has_music and duck_music:
        steps = [
            f"{source_audio_label}aformat=channel_layouts=stereo,"
            "volume=1.0,asplit=2[srcmix][srcduck]"
        ]
    else:
        steps = [
            f"{source_audio_label}aformat=channel_layouts=stereo,"
            "volume=1.0[srcmix]"
        ]
    mix_inputs = ["[srcmix]"]
    if has_music:
        safe_music_volume = max(0, min(1, music_volume))
        steps.append(
            f"[{first_music_input}:a]aformat=channel_layouts=stereo,"
            f"volume={safe_music_volume:.3f}[musicbase]"
        )
        if duck_music:
            steps.append(
                "[musicbase][srcduck]sidechaincompress="
                "threshold=0.03:ratio=8:attack=180:release=420[musicduck]"
            )
            mix_inputs.append("[musicduck]")
        else:
            mix_inputs.append("[musicbase]")

    first_sfx_input = first_music_input + (1 if has_music else 0)
    safe_sfx_volume = max(0, min(1, sfx_intensity)) * 0.35
    for index in range(active_sfx_count):
        cue_ms = max(0, int(round(sfx_cue_ms[index] if index < len(sfx_cue_ms) else 0)))
        label = f"sfx{index}"
        steps.append(
            f"[{first_sfx_input + index}:a]aformat=channel_layouts=stereo,"
            f"volume={safe_sfx_volume:.3f},adelay={cue_ms}|{cue_ms},"
            f"atrim=duration=1.4[{label}]"
        )
        mix_inputs.append(f"[{label}]")

    steps.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:"
        "duration=first:dropout_transition=2,alimiter=limit=0.95[aout]"
    )
    return ";".join(steps)


def _tail_for_diagnostics(output: str, *, limit: int = 1200) -> str:
    compact = "\n".join(line.strip() for line in output.splitlines() if line.strip())
    if len(compact) <= limit:
        return compact
    return compact[-limit:]


def _normalize_keep_segments(segments: list[CutSegment] | None) -> list[CutSegment]:
    normalized = [
        segment for segment in (segments or []) if segment.end_ms - segment.start_ms >= 1
    ]
    if not normalized:
        return []
    return sorted(normalized, key=lambda segment: (segment.start_ms, segment.end_ms))
