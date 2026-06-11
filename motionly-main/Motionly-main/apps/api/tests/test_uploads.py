import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.uploads import cancel_upload, complete_upload, get_upload_asset_urls, init_upload
from app.core.config import get_settings
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.uploads import (
    UploadCompleteRequest,
    UploadInitRequest,
    UploadStatus,
    VideoMetadata,
)
from app.services.preprocessing import VideoPreprocessor
from app.services.queue import PreprocessQueue
from app.services.upload_validation import validate_video_render_readiness


class FakeStorageSigner:
    def create_signed_upload(self, **_kwargs):
        return SimpleNamespace(
            url="https://storage.example.com/upload",
            headers={"content-type": "video/mp4"},
        )

    def create_signed_download(self, *, bucket, path, **_kwargs):
        return SimpleNamespace(url=f"https://storage.example.com/{bucket}/{path}?signed=1")


def make_context() -> tuple[SimpleNamespace, InMemoryUploadRepository, str, str, str]:
    repository = InMemoryUploadRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    settings = get_settings()
    settings.upload_sync_processing = False
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    return request, repository, str(project_id), str(workspace_id), str(user_id)


def actor(workspace_id: str, user_id: str) -> Actor:
    return Actor(
        user_id=uuid4() if user_id == "random" else UUID(user_id),
        workspace_id=UUID(workspace_id),
    )


def uuid4_from_string(value: str):
    return UUID(value)


def test_upload_init_and_complete_queues_processing_job() -> None:
    request, repository, project_id, workspace_id, user_id = make_context()

    init_response = asyncio.run(
        init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename="launch.mp4",
                mime_type="video/mp4",
                file_size_bytes=10_000,
            ),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )

    assert init_response.status == "uploading"
    assert init_response.storage_path.startswith(
        f"workspaces/{workspace_id}/projects/{project_id}/raw/"
    )
    assert init_response.signed_upload_url

    complete_response = asyncio.run(
        complete_upload(
            project_id=uuid4_from_string(project_id),
            video_id=init_response.video_id,
            payload=UploadCompleteRequest(observed_file_size_bytes=10_000),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            preprocess_queue=PreprocessQueue(),
        )
    )

    assert complete_response.video_id == init_response.video_id
    assert complete_response.status == "processing"
    assert complete_response.job_id == f"preprocess:{init_response.video_id}"


def test_upload_rejects_mime_extension_mismatch() -> None:
    request, repository, project_id, workspace_id, user_id = make_context()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            init_upload(
                project_id=uuid4_from_string(project_id),
                payload=UploadInitRequest(
                    filename="launch.mov",
                    mime_type="video/mp4",
                    file_size_bytes=10_000,
                ),
                request=request,
                actor=actor(workspace_id, user_id),
                repository=repository,
                storage_signer=FakeStorageSigner(),
            )
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "upload_mime_extension_mismatch"


def test_complete_rejects_size_mismatch() -> None:
    request, repository, project_id, workspace_id, user_id = make_context()
    init_response = asyncio.run(
        init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename="launch.webm",
                mime_type="video/webm",
                file_size_bytes=10_000,
            ),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            complete_upload(
                project_id=uuid4_from_string(project_id),
                video_id=init_response.video_id,
                payload=UploadCompleteRequest(observed_file_size_bytes=999),
                request=request,
                actor=actor(workspace_id, user_id),
                repository=repository,
                preprocess_queue=PreprocessQueue(),
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "upload_size_mismatch"


def test_upload_cross_workspace_access_is_denied() -> None:
    request, repository, project_id, _workspace_id, user_id = make_context()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            init_upload(
                project_id=uuid4_from_string(project_id),
                payload=UploadInitRequest(
                    filename="launch.mp4",
                    mime_type="video/mp4",
                    file_size_bytes=10_000,
                ),
                request=request,
                actor=actor(str(uuid4()), user_id),
                repository=repository,
                storage_signer=FakeStorageSigner(),
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "workspace_access_denied"


def test_upload_concurrency_limit_is_enforced() -> None:
    request, repository, project_id, workspace_id, user_id = make_context()
    request.app.state.settings.upload_max_concurrent_per_workspace = 1

    asyncio.run(
        init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename="first.mp4",
                mime_type="video/mp4",
                file_size_bytes=10_000,
            ),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            init_upload(
                project_id=uuid4_from_string(project_id),
                payload=UploadInitRequest(
                    filename="second.mp4",
                    mime_type="video/mp4",
                    file_size_bytes=10_000,
                ),
                request=request,
                actor=actor(workspace_id, user_id),
                repository=repository,
                storage_signer=FakeStorageSigner(),
            )
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "upload_concurrency_limit"


def test_cancel_upload_marks_failed_and_releases_concurrency_slot() -> None:
    request, repository, project_id, workspace_id, user_id = make_context()
    request.app.state.settings.upload_max_concurrent_per_workspace = 1
    init_response = asyncio.run(
        init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename="canceled.mp4",
                mime_type="video/mp4",
                file_size_bytes=10_000,
            ),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )

    canceled = asyncio.run(
        cancel_upload(
            project_id=uuid4_from_string(project_id),
            video_id=init_response.video_id,
            actor=actor(workspace_id, user_id),
            repository=repository,
        )
    )
    replacement = asyncio.run(
        init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename="replacement.mp4",
                mime_type="video/mp4",
                file_size_bytes=10_000,
            ),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )

    assert canceled.status == UploadStatus.FAILED
    assert canceled.error_code == "upload_canceled"
    assert replacement.status == UploadStatus.UPLOADING


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
