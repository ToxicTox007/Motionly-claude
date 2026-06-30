from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.repositories.uploads import InMemoryUploadRepository
from app.repositories.visual import InMemoryVisualRepository
from app.schemas.uploads import UploadStatus, VideoMetadata
from app.schemas.visual import VisualManifestStatus
from app.services.local_storage import LocalObjectStorage
from app.services.visual_intelligence import (
    LocalMLVisualIntelligenceProvider,
    run_visual_analysis_for_upload,
)


class MockTorchCuda:
    def __init__(self):
        self.empty_cache_called = 0

    def empty_cache(self):
        self.empty_cache_called += 1

class MockTorch:
    def __init__(self):
        self.cuda = MockTorchCuda()


def _make_mock_models(mock_torch):
    return {
        "cv2": SimpleNamespace(
            cvtColor=lambda frame, code: frame,
            COLOR_BGR2RGB=1,
            COLOR_BGR2GRAY=2,
        ),
        "np": SimpleNamespace(),
        "Image": SimpleNamespace(fromarray=lambda arr: arr),
        "torch": mock_torch,
        "device": "cuda",
        "clip_processor": SimpleNamespace(),
        "clip_model": SimpleNamespace(),
        "blip_processor": SimpleNamespace(),
        "blip_model": SimpleNamespace(),
        "yolo_model": SimpleNamespace(),
        "face_detector": SimpleNamespace(),
    }


def test_visual_analysis_cuda_oom_handling(monkeypatch) -> None:
    mock_torch = MockTorch()
    monkeypatch.setattr(
        LocalMLVisualIntelligenceProvider,
        "_load_models",
        lambda self: _make_mock_models(mock_torch),
    )

    def mock_sample_frames(*args, **kwargs):
        raise RuntimeError("CUDA out of memory. Tried to allocate 4.00 GiB")

    monkeypatch.setattr("app.services.visual_intelligence._sample_video_frames", mock_sample_frames)

    provider = LocalMLVisualIntelligenceProvider(
        clip_model="clip",
        blip_model="blip",
        yolo_model="yolo",
    )

    with pytest.raises(ApiError) as exc_info:
        provider.analyze(
            source_path=Path(__file__),
            metadata=VideoMetadata(width=1920, height=1080, duration_ms=5000, has_audio=True),
            sample_rate_fps=1.0,
            scene_threshold=0.3,
        )

    assert exc_info.value.code == "cuda_oom"
    assert exc_info.value.status_code == 503
    assert mock_torch.cuda.empty_cache_called == 1


def test_visual_analysis_non_oom_exception_propagation(monkeypatch) -> None:
    mock_torch = MockTorch()
    monkeypatch.setattr(
        LocalMLVisualIntelligenceProvider,
        "_load_models",
        lambda self: _make_mock_models(mock_torch),
    )

    def mock_sample_frames(*args, **kwargs):
        raise ValueError("Invalid frame rate configuration")

    monkeypatch.setattr("app.services.visual_intelligence._sample_video_frames", mock_sample_frames)

    provider = LocalMLVisualIntelligenceProvider(
        clip_model="clip",
        blip_model="blip",
        yolo_model="yolo",
    )

    with pytest.raises(ValueError) as exc_info:
        provider.analyze(
            source_path=Path(__file__),
            metadata=VideoMetadata(width=1920, height=1080, duration_ms=5000, has_audio=True),
            sample_rate_fps=1.0,
            scene_threshold=0.3,
        )

    assert "Invalid frame rate" in str(exc_info.value)
    assert mock_torch.cuda.empty_cache_called == 0


def test_run_visual_analysis_transitions_to_failed_on_cuda_oom(monkeypatch, tmp_path) -> None:
    upload_repository = InMemoryUploadRepository()
    visual_repository = InMemoryVisualRepository(upload_repository)
    project_id = uuid4()
    workspace_id = uuid4()

    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = upload_repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="raw.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=100,
    )
    upload.status = UploadStatus.READY
    upload.metadata = VideoMetadata(
        duration_ms=6000,
        width=1080,
        height=1920,
        fps=30,
        codec="h264",
        has_audio=True,
        orientation="vertical",
    )
    upload_repository.attach_active_video(project_id=project_id, video_id=upload.id)
    LocalObjectStorage(root=str(tmp_path)).write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"fake mp4",
    )

    class MockOomProvider:
        provider_name = "local_ml"
        model = "clip|blip|yolo"

        def analyze(self, **kwargs):
            raise ApiError(500, "cuda_oom", "GPU out of memory")

    monkeypatch.setattr(
        "app.services.visual_intelligence.create_object_storage",
        lambda **kwargs: SimpleNamespace(ensure_object=lambda **k: Path(__file__)),
    )

    with pytest.raises(ApiError) as exc_info:
        run_visual_analysis_for_upload(
            repository=visual_repository,
            provider=MockOomProvider(),
            workspace_id=workspace_id,
            project_id=project_id,
            video_id=upload.id,
            media_work_dir=str(tmp_path),
            supabase_url=None,
            service_role_key=None,
            sample_rate_fps=1.0,
            scene_threshold=0.3,
        )

    assert exc_info.value.code == "cuda_oom"

    latest = visual_repository.latest_for_upload(
        uploaded_video_id=upload.id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    assert latest is not None
    assert latest.status == VisualManifestStatus.FAILED
    assert latest.error_code == "cuda_oom"
