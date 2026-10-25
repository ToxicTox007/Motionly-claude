import subprocess
import tempfile
from pathlib import Path

from app.api.errors import ApiError
from app.services.subprocess_env import media_tool_env


def generate_selfie_segmentation_matte(
    *,
    cv2,
    np,
    selfie_segmentation,
    source_path: Path,
    output_path: Path,
    max_dimension: int = 720,
) -> dict[str, float | int]:
    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise ApiError(
            422, "segmentation_video_open_failed", "Video could not be opened for segmentation."
        )

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        capture.release()
        raise ApiError(
            422, "segmentation_video_invalid", "Video dimensions are invalid for segmentation."
        )

    scale = min(1.0, max_dimension / max(width, height))
    out_width = max(2, int(round(width * scale)))
    out_height = max(2, int(round(height * scale)))
    if out_width % 2:
        out_width -= 1
    if out_height % 2:
        out_height -= 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="motionly-matte-") as temp_dir:
        frame_dir = Path(temp_dir) / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if scale != 1.0:
                frame = cv2.resize(frame, (out_width, out_height), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = selfie_segmentation.process(rgb)
            mask = results.segmentation_mask
            if mask is None:
                mask = np.zeros((out_height, out_width), dtype=np.float32)
            mask_uint8 = (np.clip(mask, 0.0, 1.0) * 255).astype(np.uint8)
            cv2.imwrite(str(frame_dir / f"frame_{frame_index:06d}.png"), mask_uint8)
            frame_index += 1
        capture.release()

        if frame_index == 0:
            raise ApiError(
                422, "segmentation_frames_empty", "No frames could be sampled for segmentation."
            )

        _encode_matte_video(frame_dir=frame_dir, output_path=output_path, fps=fps)

    return {
        "fps": round(fps, 3),
        "width": out_width,
        "height": out_height,
        "frame_count": frame_index,
    }


def _encode_matte_video(*, frame_dir: Path, output_path: Path, fps: float) -> None:
    pattern = str(frame_dir / "frame_%06d.png")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-framerate",
        f"{fps:.3f}",
        "-i",
        pattern,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=600, env=media_tool_env()
    )
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        stderr = (result.stderr or "").strip()
        raise ApiError(
            502,
            "segmentation_matte_encode_failed",
            "Failed to encode segmentation matte video.",
            details={"stderr": stderr[-500:]},
        )
