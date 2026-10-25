import json
import subprocess
import wave
from pathlib import Path

from app.schemas.uploads import VideoMetadata
from app.services.subprocess_env import media_tool_env


class MediaProbeError(Exception):
    pass


def probe_video(path: Path) -> VideoMetadata:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=media_tool_env(),
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise MediaProbeError("Unable to inspect uploaded video.") from exc

    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not video_stream:
        raise MediaProbeError("Uploaded file does not contain a video stream.")

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    rotation = _rotation_degrees(video_stream)
    orientation = "unknown"
    if width and height:
        display_width, display_height = (
            (height, width) if rotation in {90, 270} else (width, height)
        )
        if display_height > display_width:
            orientation = "vertical"
        elif display_width > display_height:
            orientation = "landscape"
        else:
            orientation = "square"

    warnings: list[str] = []
    if orientation == "landscape":
        warnings.append("landscape_source")
    if not audio_streams:
        warnings.append("missing_audio")

    return VideoMetadata(
        duration_ms=_duration_ms(payload),
        width=width or None,
        height=height or None,
        fps=_fps(video_stream),
        codec=video_stream.get("codec_name"),
        bitrate=int(payload.get("format", {}).get("bit_rate") or 0) or None,
        frame_count=int(video_stream.get("nb_frames") or 0) or None,
        has_audio=bool(audio_streams),
        orientation=orientation,
        rotation_degrees=rotation,
        warnings=warnings,
    )


def probe_duration_seconds(path: Path) -> float | None:
    """Container duration in seconds, or None when ffprobe cannot read it."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=media_tool_env(),
        )
        duration = float(json.loads(result.stdout).get("format", {}).get("duration") or 0)
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
        ValueError,
        TypeError,
        AttributeError,
    ):
        return None
    return duration if duration > 0 else None


def audio_duration_seconds(path: Path) -> float | None:
    """Duration of a WAV file from its header, falling back to ffprobe.

    Seconds-billed audio capabilities (diarization, voice emotion) meter by
    this value, so every caller uses the same reading.
    """
    try:
        with wave.open(str(path), "rb") as audio:
            rate = audio.getframerate()
            frames = audio.getnframes()
        if rate > 0 and frames > 0:
            return frames / rate
        return None
    except (wave.Error, EOFError, OSError):
        return probe_duration_seconds(path)


def _duration_ms(payload: dict[str, object]) -> int | None:
    duration = (
        payload.get("format", {}).get("duration")
        if isinstance(payload.get("format"), dict)
        else None
    )
    return int(float(duration) * 1000) if duration else None


def _fps(video_stream: dict[str, object]) -> float | None:
    rate = str(video_stream.get("avg_frame_rate") or "0/0")
    numerator, _, denominator = rate.partition("/")
    if not numerator or not denominator or denominator == "0":
        return None
    return round(int(numerator) / int(denominator), 3)


def _rotation_degrees(video_stream: dict[str, object]) -> int | None:
    tags = video_stream.get("tags")
    if isinstance(tags, dict) and tags.get("rotate"):
        return int(tags["rotate"]) % 360
    for side_data in video_stream.get("side_data_list") or []:
        if isinstance(side_data, dict) and side_data.get("rotation") is not None:
            return int(side_data["rotation"]) % 360
    return None
