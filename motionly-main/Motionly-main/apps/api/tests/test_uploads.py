import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.uploads import (
    get_upload_asset_urls,
)
from app.core.config import get_settings
from app.repositories.uploads import (
    InMemoryUploadRepository,
    SupabaseUploadRepository,
)
from app.repositories.workflows import InMemoryWorkflowRepository
from app.schemas.uploads import (
    UploadStatus,
    VideoMetadata,
)
from app.services.preprocessing import VideoPreprocessor
from app.services.upload_validation import validate_video_render_readiness


class FakeStorageSigner:
    def create_signed_upload(self, **_kwargs):
        return SimpleNamespace(
            url="https://storage.example.com/upload",
            headers={"content-type": "video/mp4"},
        )

    def create_signed_download(self, *, bucket, path, **_kwargs):
        return SimpleNamespace(url=f"https://storage.example.com/{bucket}/{path}?signed=1")


class ThumbnailFailingStorageSigner(FakeStorageSigner):
    def create_signed_download(self, *, bucket, path, **_kwargs):
        if bucket == "thumbnails":
            raise RuntimeError(f"missing thumbnail: {path}")
        return super().create_signed_download(bucket=bucket, path=path, **_kwargs)


def make_context() -> tuple[SimpleNamespace, InMemoryUploadRepository, str, str, str]:
    repository = InMemoryUploadRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    settings = get_settings()
    settings.app_env = "test"
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                workflow_repository=InMemoryWorkflowRepository(),
            )
        )
    )
    return request, repository, str(project_id), str(workspace_id), str(user_id)


def actor(workspace_id: str, user_id: str) -> Actor:
    return Actor(
        user_id=uuid4() if user_id == "random" else UUID(user_id),
        workspace_id=UUID(workspace_id),
    )


def uuid4_from_string(value: str):
    return UUID(value)


def test_supabase_upload_complete_batches_processing_transition_and_active_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SupabaseUploadRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    upload_id = uuid4()
    calls = []
    base = {
        "id": str(upload_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "storage_bucket": "raw-uploads",
        "storage_path": f"workspaces/{workspace_id}/projects/{project_id}/raw/{upload_id}.mp4",
        "original_filename": "launch.mp4",
        "mime_type": "video/mp4",
        "file_size_bytes": 10_000,
        "byte_size": 10_000,
    }

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET" and path == "/uploaded_videos":
            return _supabase_response([_upload_row({**base, "status": "uploading"})])
        if method == "PATCH" and path == "/uploaded_videos":
            return _supabase_response([_upload_row({**base, "status": "processing"})])
        if method == "PATCH" and path == "/projects":
            return _supabase_response([])
        raise AssertionError(f"unexpected Supabase call: {method} {path}")

    monkeypatch.setattr("app.repositories.uploads.SupabaseRestClient.request", fake_request)

    upload = repository.complete_upload_for_processing(
        project_id=project_id,
        video_id=upload_id,
        workspace_id=workspace_id,
        observed_file_size_bytes=10_000,
    )

    assert upload.status == UploadStatus.PROCESSING
    assert [call[:2] for call in calls] == [
        ("GET", "/uploaded_videos"),
        ("PATCH", "/uploaded_videos"),
        ("PATCH", "/projects"),
    ]
    assert calls[1][2]["json"]["status"] == "processing"
    assert calls[2][2]["json"]["active_video_id"] == str(upload_id)


def test_upload_asset_urls_are_signed_after_workspace_check() -> None:
    request, repository, project_id, workspace_id, user_id = make_context()
    upload = repository.create_upload(
        project_id=uuid4_from_string(project_id),
        workspace_id=uuid4_from_string(workspace_id),
        storage_bucket="raw-uploads",
        storage_path="workspaces/one/projects/two/raw/video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=10_000,
    )
    upload.status = UploadStatus.READY
    upload.thumbnail_path = "workspaces/one/projects/two/thumbnails/video.jpg"
    upload.metadata = VideoMetadata(duration_ms=3000, width=1080, height=1920)

    response = asyncio.run(
        get_upload_asset_urls(
            project_id=uuid4_from_string(project_id),
            video_id=upload.id,
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )

    assert response.source_video_url.endswith(
        "/raw-uploads/workspaces/one/projects/two/raw/video.mp4?signed=1"
    )
    assert response.thumbnail_url
    assert response.thumbnail_url.endswith(
        "/thumbnails/workspaces/one/projects/two/thumbnails/video.jpg?signed=1"
    )


def test_upload_asset_urls_keep_source_when_thumbnail_signing_fails() -> None:
    request, repository, project_id, workspace_id, user_id = make_context()
    upload = repository.create_upload(
        project_id=uuid4_from_string(project_id),
        workspace_id=uuid4_from_string(workspace_id),
        storage_bucket="raw-uploads",
        storage_path="workspaces/one/projects/two/raw/video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=10_000,
    )
    upload.status = UploadStatus.READY
    upload.thumbnail_path = "workspaces/one/projects/two/thumbnails/video.jpg"
    upload.metadata = VideoMetadata(duration_ms=3000, width=1080, height=1920)

    response = asyncio.run(
        get_upload_asset_urls(
            project_id=uuid4_from_string(project_id),
            video_id=upload.id,
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=ThumbnailFailingStorageSigner(),
        )
    )

    assert response.source_video_url.endswith(
        "/raw-uploads/workspaces/one/projects/two/raw/video.mp4?signed=1"
    )
    assert response.thumbnail_url is None


def test_upload_asset_urls_keep_source_when_reference_thumbnail_signing_fails() -> None:
    from app.services.object_references import ObjectReferenceService

    class ThumbnailFailingReferenceService(ObjectReferenceService):
        def __init__(self) -> None:
            super().__init__(storage_signer=FakeStorageSigner(), in_memory=True)
            self.sign_calls = 0

        async def get_signed_url(self, **kwargs):
            self.sign_calls += 1
            if self.sign_calls == 2:
                raise RuntimeError("thumbnail signing failed")
            return await super().get_signed_url(**kwargs)

    request, repository, project_id, workspace_id, user_id = make_context()
    request.app.state.object_reference_service = ThumbnailFailingReferenceService()
    upload = repository.create_upload(
        project_id=uuid4_from_string(project_id),
        workspace_id=uuid4_from_string(workspace_id),
        storage_bucket="raw-uploads",
        storage_path=(f"workspaces/{workspace_id}/projects/{project_id}/raw/video.mp4"),
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=10_000,
    )
    upload.status = UploadStatus.READY
    upload.thumbnail_path = f"workspaces/{workspace_id}/projects/{project_id}/thumbnails/video.jpg"
    upload.metadata = VideoMetadata(duration_ms=3000, width=1080, height=1920)

    response = asyncio.run(
        get_upload_asset_urls(
            project_id=uuid4_from_string(project_id),
            video_id=upload.id,
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )

    assert response.source_video_url.endswith("/raw/video.mp4?signed=1")
    assert response.source_reference_id is not None
    assert upload.source_object_reference_id == response.source_reference_id
    assert response.thumbnail_url is None
    assert response.thumbnail_reference_id is None


def test_media_readiness_blocks_missing_audio_and_unsupported_codec() -> None:
    result = validate_video_render_readiness(
        VideoMetadata(
            duration_ms=12_000,
            width=1080,
            height=1920,
            fps=30,
            codec="prores",
            has_audio=False,
            orientation="vertical",
        )
    )

    assert not result.is_render_ready
    assert "Video codec is not supported yet" in " ".join(result.blocking_messages)
    assert "No audio track was detected" in " ".join(result.blocking_messages)


def test_media_readiness_reports_actionable_warnings_without_blocking() -> None:
    result = validate_video_render_readiness(
        VideoMetadata(
            duration_ms=240_000,
            width=1920,
            height=1080,
            fps=12,
            codec="h264",
            has_audio=True,
            orientation="landscape",
        )
    )

    assert result.is_render_ready
    assert result.warning_codes == ["long_video", "unusual_frame_rate", "landscape_source"]


def test_preprocessing_fails_non_render_ready_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    def fake_probe(_source_path):
        return VideoMetadata(
            duration_ms=8_000,
            width=1080,
            height=1920,
            fps=30,
            codec="theora",
            has_audio=True,
            orientation="vertical",
        )

    monkeypatch.setattr("app.services.preprocessing.probe_video", fake_probe)

    with pytest.raises(ApiError) as exc_info:
        VideoPreprocessor(storage_root=tmp_path).process(
            source_path=tmp_path / "source.ogv",
            thumbnail_bucket="thumbnails",
            thumbnail_path="thumbs/source.jpg",
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "video_not_render_ready"


def _supabase_response(rows, *, headers: dict[str, str] | None = None):
    return SimpleNamespace(status_code=200, headers=headers or {}, json=lambda: rows)


def _upload_row(values: dict[str, object]) -> dict[str, object]:
    now = "2026-07-05T00:00:00+00:00"
    return {
        "id": str(values["id"]),
        "project_id": str(values["project_id"]),
        "workspace_id": str(values["workspace_id"]),
        "storage_bucket": str(values["storage_bucket"]),
        "storage_path": str(values["storage_path"]),
        "original_filename": str(values["original_filename"]),
        "mime_type": str(values["mime_type"]),
        "file_size_bytes": int(values["file_size_bytes"]),
        "byte_size": int(values["byte_size"]),
        "status": str(values.get("status") or "uploading"),
        "thumbnail_path": None,
        "metadata": {},
        "error_code": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
