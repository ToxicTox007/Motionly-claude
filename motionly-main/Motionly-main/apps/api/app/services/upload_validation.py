from dataclasses import dataclass
from pathlib import PurePath

from app.api.errors import ApiError
from app.schemas.uploads import VideoMetadata

ALLOWED_MIME_TYPES = {
    "video/mp4": {".mp4", ".m4v"},
    "video/quicktime": {".mov", ".qt"},
    "video/webm": {".webm"},
}
SUPPORTED_SOURCE_CODECS = {"h264", "hevc", "h265", "vp8", "vp9"}
MIN_RECOMMENDED_DURATION_MS = 3_000
LONG_VIDEO_WARNING_MS = 180_000
MAX_RENDERABLE_DURATION_MS = 900_000
MIN_RECOMMENDED_SHORT_EDGE_PX = 720
MIN_RENDERABLE_DIMENSION_PX = 240
MIN_RECOMMENDED_FPS = 15
MAX_RECOMMENDED_FPS = 60
MAX_UPLOAD_FILE_SIZE_BYTES = 512 * 1024 * 1024
MAX_RENDERABLE_FPS = 120


@dataclass(frozen=True)
class UploadValidationResult:
    extension: str
    warnings: list[str]


@dataclass(frozen=True)
class MediaReadinessResult:
    blocking_messages: list[str]
    warning_codes: list[str]

    @property
    def is_render_ready(self) -> bool:
        return not self.blocking_messages


def validate_upload_preflight(
    *,
    filename: str,
    mime_type: str,
    file_size_bytes: int,
    max_file_size_bytes: int,
) -> UploadValidationResult:
    extension = PurePath(filename).suffix.lower()
    if not extension:
        raise ApiError(
            422,
            "upload_missing_extension",
            "Upload files must include a video extension.",
        )

    allowed_extensions = ALLOWED_MIME_TYPES.get(mime_type)
    if not allowed_extensions:
        raise ApiError(415, "upload_unsupported_mime", "This video MIME type is not supported yet.")

    if extension not in allowed_extensions:
        raise ApiError(
            422,
            "upload_mime_extension_mismatch",
            "The file extension does not match the declared video type.",
        )

    effective_max_file_size_bytes = min(
        max_file_size_bytes,
        MAX_UPLOAD_FILE_SIZE_BYTES,
    )
    if file_size_bytes > effective_max_file_size_bytes:
        raise ApiError(413, "upload_file_too_large", "This file exceeds your plan limit.")

    warnings: list[str] = []
    if file_size_bytes > effective_max_file_size_bytes * 0.8:
        warnings.append("large_file")

    return UploadValidationResult(extension=extension.lstrip("."), warnings=warnings)


def validate_video_render_readiness(metadata: VideoMetadata) -> MediaReadinessResult:
    blocking_messages: list[str] = []
    warning_codes: list[str] = []

    if metadata.duration_ms is None:
        blocking_messages.append("Video duration could not be detected.")
    elif metadata.duration_ms <= 0:
        blocking_messages.append("Video duration must be greater than zero.")
    elif metadata.duration_ms > MAX_RENDERABLE_DURATION_MS:
        blocking_messages.append("Video is longer than the current 15 minute render limit.")
    elif metadata.duration_ms > LONG_VIDEO_WARNING_MS:
        warning_codes.append("long_video")
    elif metadata.duration_ms < MIN_RECOMMENDED_DURATION_MS:
        warning_codes.append("very_short_video")

    if not metadata.width or not metadata.height:
        blocking_messages.append("Video resolution could not be detected.")
    elif (
        metadata.width < MIN_RENDERABLE_DIMENSION_PX
        or metadata.height < MIN_RENDERABLE_DIMENSION_PX
    ):
        blocking_messages.append("Video resolution is too small for rendering.")
    elif min(metadata.width, metadata.height) < MIN_RECOMMENDED_SHORT_EDGE_PX:
        warning_codes.append("low_resolution")

    if metadata.fps is None:
        blocking_messages.append("Video frame rate could not be detected.")
    elif metadata.fps <= 0 or metadata.fps > MAX_RENDERABLE_FPS:
        blocking_messages.append("Video frame rate is not supported for rendering.")
    elif metadata.fps < MIN_RECOMMENDED_FPS or metadata.fps > MAX_RECOMMENDED_FPS:
        warning_codes.append("unusual_frame_rate")

    codec = (metadata.codec or "").lower()
    if not codec:
        blocking_messages.append("Video codec could not be detected.")
    elif codec not in SUPPORTED_SOURCE_CODECS:
        blocking_messages.append(
            "Video codec is not supported yet. Upload H.264, HEVC, VP8, or VP9 video.",
        )

    if metadata.has_audio is False:
        blocking_messages.append("No audio track was detected. Upload a video with audio.")
    elif metadata.has_audio is None:
        blocking_messages.append("Audio availability could not be detected.")

    if metadata.orientation == "landscape":
        warning_codes.append("landscape_source")
    elif metadata.orientation == "square":
        warning_codes.append("square_source")
    elif metadata.orientation == "unknown":
        warning_codes.append("unknown_aspect_ratio")

    return MediaReadinessResult(
        blocking_messages=blocking_messages,
        warning_codes=_unique([*metadata.warnings, *warning_codes]),
    )


def assert_video_render_ready(metadata: VideoMetadata) -> MediaReadinessResult:
    result = validate_video_render_readiness(metadata)
    if result.blocking_messages:
        raise ApiError(
            422,
            "video_not_render_ready",
            " ".join(result.blocking_messages),
        )
    return result


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
