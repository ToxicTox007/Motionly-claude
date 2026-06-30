import subprocess
from pathlib import Path

from app.api.errors import ApiError
from app.schemas.uploads import VideoMetadata
from app.services.media_probe import MediaProbeError, probe_video
from app.services.upload_validation import assert_video_render_ready


class VideoPreprocessor:
    def __init__(self, *, storage_root: str | Path) -> None:
        self.storage_root = Path(storage_root).expanduser().resolve()

    def process(
        self,
        *,
        source_path: Path,
        thumbnail_bucket: str,
        thumbnail_path: str,
    ) -> tuple[VideoMetadata, str]:
        try:
            metadata = probe_video(source_path)
        except MediaProbeError as exc:
            raise ApiError(422, "video_probe_failed", str(exc)) from exc
        readiness = assert_video_render_ready(metadata)
        metadata.warnings = readiness.warning_codes

        thumbnail_file = (self.storage_root / thumbnail_bucket / thumbnail_path).resolve()
        thumbnail_file.parent.mkdir(parents=True, exist_ok=True)
        self._thumbnail(source_path=source_path, output_path=thumbnail_file, metadata=metadata)
        return metadata, thumbnail_path

    def _thumbnail(
        self,
        *,
        source_path: Path,
        output_path: Path,
        metadata: VideoMetadata,
    ) -> None:
        seek_seconds = 1
        if metadata.duration_ms and metadata.duration_ms < 10_000:
            seek_seconds = max(0, round(metadata.duration_ms / 10_000, 2))
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            str(seek_seconds),
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=720:-2",
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
        if result.returncode != 0:
            raise ApiError(502, "thumbnail_generation_failed", "Thumbnail could not be generated.")
