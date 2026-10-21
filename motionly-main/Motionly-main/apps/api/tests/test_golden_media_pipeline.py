import subprocess
from pathlib import Path
from shutil import which
from uuid import uuid4

import pytest

from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.renders import InMemoryRenderRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.renders import CutSegment, RenderJobStatus, RenderSpecs
from app.services.render_jobs import complete_render_job


@pytest.mark.skipif(
    which("ffmpeg") is None or which("ffprobe") is None,
    reason="FFmpeg integration tools are required",
)
def test_upload_to_export_ffmpeg_golden_media(tmp_path: Path) -> None:
    workspace_id, project_id = uuid4(), uuid4()
    uploads = InMemoryUploadRepository()
    uploads.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = uploads.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="golden/source.mp4",
        original_filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=1024,
    )
    source = tmp_path / "raw-uploads" / upload.storage_path
    source.parent.mkdir(parents=True)
    _fixture_video(source)

    repository = InMemoryRenderRepository(InMemoryCaptionRepository(uploads))
    job = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        caption_track_id=uuid4(),
        status=RenderJobStatus.QUEUED,
        planner_version="golden-v1",
        manifest={
            "source": {"bucket": "raw-uploads", "path": upload.storage_path},
            "include_captions": False,
            "export_settings": {
                "framing": {
                    "output_aspect": "16:9",
                    "fit_mode": "fill",
                    "crop": {"x": 0.5, "y": 0.5, "zoom": 1},
                }
            },
            "render_props": {
                "sourceVideo": {"src": f"supabase://raw-uploads/{upload.storage_path}"},
                "exportSettings": {"fps": 10},
            },
        },
        keep_segments=[CutSegment(start_ms=2000, end_ms=3000, reason="blue payoff")],
        specs=RenderSpecs(width=160, height=90, fps=10),
    )

    completed = complete_render_job(
        job=job,
        repository=repository,
        export_bucket="exports",
        storage_root=str(tmp_path),
        supabase_url=None,
        service_role_key=None,
    )
    output = tmp_path / "exports" / str(completed.artifact_path)
    probe = _probe(output)
    pixel = _pixel(output)

    assert completed.status == RenderJobStatus.COMPLETE
    assert probe["codec_name"] == "h264"
    assert probe["width"] == 160 and probe["height"] == 90
    assert 0.9 <= probe["duration"] <= 1.2
    assert pixel[2] > pixel[0] + 150, f"expected blue encoded payoff, got {pixel}"


def _fixture_video(path: Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=160x90:rate=10:duration=2",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=160x90:rate=10:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=4",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "2:a",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _probe(path: Path) -> dict[str, float | int | str]:
    import json

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    return {
        "codec_name": stream["codec_name"],
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "duration": float(payload["format"]["duration"]),
    }


def _pixel(path: Path) -> tuple[int, int, int]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0.2",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=1:1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return tuple(result.stdout[:3])  # type: ignore[return-value]
