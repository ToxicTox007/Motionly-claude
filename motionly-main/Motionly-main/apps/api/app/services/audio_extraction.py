import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.api.errors import ApiError


@dataclass(frozen=True)
class ExtractedAudio:
    path: Path
    sample_rate_hz: int = 16_000
    channels: int = 1


class FFmpegAudioExtractor:
    def extract_mono_wav(self, *, source_path: Path, output_path: Path) -> ExtractedAudio:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            stderr = result.stderr.lower()
            if "does not contain any stream" in stderr or "audio" in stderr:
                raise ApiError(
                    422,
                    "captions_unavailable",
                    "This video does not appear to contain usable audio.",
                )
            raise ApiError(502, "audio_extraction_failed", "Audio could not be prepared.")
        return ExtractedAudio(path=output_path)

    def cleanup(self, *, audio_path: Path, retain_debug_copy: bool) -> None:
        if retain_debug_copy:
            return
        audio_path.unlink(missing_ok=True)
