import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.uploads import (
    cancel_upload,
    complete_upload,
    get_active_upload,
    get_upload,
    get_upload_asset_urls,
    init_upload,
)
from app.core.config import get_settings
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.uploads import (
    InMemoryUploadRepository,
    SupabaseUploadRepository,
)
from app.repositories.visual import InMemoryVisualRepository
from app.repositories.workflows import InMemoryWorkflowRepository
from app.schemas.uploads import (
    UploadCompleteRequest,
    UploadInitRequest,
    UploadStatus,
    VideoMetadata,
)
from app.services.local_storage import LocalObjectStorage
from app.services.preprocessing import VideoPreprocessor
from app.services.queue import PreprocessQueue, TranscriptionQueue, VisualQueue
from app.services.transcription_provider import FixtureTranscriptionProvider
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
            workflow_repository=request.app.state.workflow_repository,
        )
    )

    assert complete_response.video_id == init_response.video_id
    assert complete_response.status == "processing"
    assert complete_response.job_id is None
    assert complete_response.workflow_run_id is not None
    assert complete_response.workflow_version == "first-draft-v2"
    assert complete_response.workflow_status_url


def test_supabase_upload_initialize_batches_final_path_and_uploading_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SupabaseUploadRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/projects":
            return _supabase_response(
                [{"id": str(project_id), "workspace_id": str(workspace_id), "title": "Launch"}]
            )
        if method == "POST" and path == "/rpc/motionly_initialize_upload":
            payload = kwargs["json"]
            return _supabase_response(
                [
                    _upload_row(
                        {
                            "id": payload["p_upload_id"],
                            "workspace_id": payload["p_workspace_id"],
                            "project_id": payload["p_project_id"],
                            "storage_bucket": payload["p_storage_bucket"],
                            "storage_path": payload["p_storage_path"],
                            "original_filename": payload["p_original_filename"],
                            "mime_type": payload["p_mime_type"],
                            "byte_size": payload["p_file_size_bytes"],
                            "file_size_bytes": payload["p_file_size_bytes"],
                            "status": "uploading",
                        }
                    )
                ]
            )
        raise AssertionError(f"unexpected Supabase call: {method} {path}")

    monkeypatch.setattr("app.repositories.uploads.SupabaseRestClient.request", fake_request)

    upload = repository.initialize_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_extension="mp4",
        original_filename="launch.mp4",
        mime_type="video/mp4",
        file_size_bytes=10_000,
        max_active_uploads=1,
    )

    assert upload.status == UploadStatus.UPLOADING
    assert upload.storage_path == (
        f"workspaces/{workspace_id}/projects/{project_id}/raw/{upload.id}.mp4"
    )
    assert [call[:2] for call in calls] == [
        ("GET", "/projects"),
        ("POST", "/rpc/motionly_initialize_upload"),
    ]
    assert calls[1][2]["json"]["p_max_active_uploads"] == 1
    assert calls[1][2]["json"]["p_storage_path"].endswith(f"{upload.id}.mp4")


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


@pytest.mark.anyio
async def test_supabase_upload_async_init_and_complete_use_async_client(
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

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/projects" and method == "GET":
            return _supabase_response(
                [{"id": str(project_id), "workspace_id": str(workspace_id), "title": "Launch"}]
            )
        if method == "GET" and path == "/uploaded_videos":
            return _supabase_response([_upload_row({**base, "status": "uploading"})])
        if method == "POST" and path == "/rpc/motionly_initialize_upload":
            payload = kwargs["json"]
            return _supabase_response(
                [
                    _upload_row(
                        {
                            "id": payload["p_upload_id"],
                            "workspace_id": payload["p_workspace_id"],
                            "project_id": payload["p_project_id"],
                            "storage_bucket": payload["p_storage_bucket"],
                            "storage_path": payload["p_storage_path"],
                            "original_filename": payload["p_original_filename"],
                            "mime_type": payload["p_mime_type"],
                            "byte_size": payload["p_file_size_bytes"],
                            "file_size_bytes": payload["p_file_size_bytes"],
                            "status": "uploading",
                        }
                    )
                ]
            )
        if method == "PATCH" and path == "/uploaded_videos":
            return _supabase_response([_upload_row({**base, "status": "processing"})])
        if method == "PATCH" and path == "/projects":
            return _supabase_response([])
        raise AssertionError(f"unexpected Supabase call: {method} {path}")

    monkeypatch.setattr(
        "app.repositories.uploads.SupabaseRestClient.async_request",
        fake_async_request,
    )

    initialized = await repository.initialize_upload_async(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_extension="mp4",
        original_filename="launch.mp4",
        mime_type="video/mp4",
        file_size_bytes=10_000,
        max_active_uploads=1,
    )
    completed = await repository.complete_upload_for_processing_async(
        project_id=project_id,
        video_id=upload_id,
        workspace_id=workspace_id,
        observed_file_size_bytes=10_000,
    )

    assert initialized.status == UploadStatus.UPLOADING
    assert completed.status == UploadStatus.PROCESSING
    assert [call[:2] for call in calls] == [
        ("GET", "/projects"),
        ("POST", "/rpc/motionly_initialize_upload"),
        ("GET", "/uploaded_videos"),
        ("PATCH", "/uploaded_videos"),
        ("PATCH", "/projects"),
    ]
    assert calls[1][2]["json"]["p_max_active_uploads"] == 1
    assert calls[1][2]["json"]["p_storage_path"].endswith(f"{initialized.id}.mp4")
    assert calls[3][2]["json"]["status"] == "processing"
    assert calls[4][2]["json"]["active_video_id"] == str(upload_id)


def test_upload_api_prefers_async_repository_methods() -> None:
    request, _repository, project_id, workspace_id, user_id = make_context()

    class AsyncUploadRepository(InMemoryUploadRepository):
        def __init__(self) -> None:
            super().__init__()
            self.async_calls: list[str] = []

        async def initialize_upload_async(self, **kwargs):
            self.async_calls.append("init")
            return self.initialize_upload(**kwargs)

        async def complete_upload_for_processing_async(self, **kwargs):
            self.async_calls.append("complete")
            return self.complete_upload_for_processing(**kwargs)

        async def get_upload_for_project_async(self, **kwargs):
            self.async_calls.append("get")
            return self.get_upload_for_project(**kwargs)

        async def get_active_upload_async(self, **kwargs):
            self.async_calls.append("active")
            return self.get_active_upload(**kwargs)

        async def mark_failed_async(self, **kwargs):
            self.async_calls.append("failed")
            return self.mark_failed(**kwargs)

    repository = AsyncUploadRepository()
    repository.seed_project(
        project_id=uuid4_from_string(project_id),
        workspace_id=uuid4_from_string(workspace_id),
    )
    user = actor(workspace_id, user_id)
    init_response = asyncio.run(
        init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename="async.mp4",
                mime_type="video/mp4",
                file_size_bytes=10_000,
            ),
            request=request,
            actor=user,
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )
    asyncio.run(
        get_active_upload(
            project_id=uuid4_from_string(project_id),
            actor=user,
            repository=repository,
        )
    )
    asyncio.run(
        get_upload(
            project_id=uuid4_from_string(project_id),
            video_id=init_response.video_id,
            actor=user,
            repository=repository,
        )
    )
    with pytest.raises(ApiError):
        # A created/uploading upload cannot expose asset URLs, but the route still
        # has to fetch it through the async repository method before rejecting.
        asyncio.run(
            get_upload_asset_urls(
                project_id=uuid4_from_string(project_id),
                video_id=init_response.video_id,
                request=request,
                actor=user,
                repository=repository,
                storage_signer=FakeStorageSigner(),
            )
        )

    complete_response = asyncio.run(
        complete_upload(
            project_id=uuid4_from_string(project_id),
            video_id=init_response.video_id,
            payload=UploadCompleteRequest(observed_file_size_bytes=10_000),
            request=request,
            actor=user,
            repository=repository,
            preprocess_queue=PreprocessQueue(),
            workflow_repository=request.app.state.workflow_repository,
        )
    )
    canceled_init = asyncio.run(
        init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename="cancel-me.mp4",
                mime_type="video/mp4",
                file_size_bytes=10_000,
            ),
            request=request,
            actor=user,
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )
    asyncio.run(
        cancel_upload(
            project_id=uuid4_from_string(project_id),
            video_id=canceled_init.video_id,
            request=request,
            actor=user,
            repository=repository,
        )
    )

    assert complete_response.status == UploadStatus.PROCESSING
    assert repository.async_calls == [
        "init",
        "active",
        "get",
        "get",
        "get",
        "complete",
        "init",
        "get",
        "failed",
    ]


def test_upload_sync_flag_cannot_run_media_inside_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    request, repository, project_id, workspace_id, user_id = make_context()
    settings = request.app.state.settings
    settings.media_work_dir = str(tmp_path)
    request.app.state.caption_repository = InMemoryCaptionRepository(repository)
    request.app.state.transcription_queue = TranscriptionQueue()
    request.app.state.transcription_provider = FixtureTranscriptionProvider()
    request.app.state.visual_repository = InMemoryVisualRepository(repository)
    request.app.state.visual_queue = VisualQueue()

    class FakePreprocessor:
        def __init__(self, **_kwargs):
            pass

        def process(self, **kwargs):
            return (
                VideoMetadata(
                    duration_ms=3000,
                    width=1080,
                    height=1920,
                    fps=30,
                    codec="h264",
                    has_audio=True,
                    orientation="vertical",
                ),
                kwargs["thumbnail_path"],
            )

    monkeypatch.setattr("app.services.upload_jobs.VideoPreprocessor", FakePreprocessor)

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
    LocalObjectStorage(root=tmp_path).write_object(
        bucket="raw-uploads",
        path=init_response.storage_path,
        content=b"fake mp4",
    )

    complete_response = asyncio.run(
        complete_upload(
            project_id=uuid4_from_string(project_id),
            video_id=init_response.video_id,
            payload=UploadCompleteRequest(observed_file_size_bytes=10_000),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            preprocess_queue=PreprocessQueue(),
            workflow_repository=request.app.state.workflow_repository,
        )
    )

    assert complete_response.status == "processing"
    assert complete_response.job_id is None
    assert complete_response.workflow_run_id is not None
    assert complete_response.workflow_version == "first-draft-v2"
    assert complete_response.workflow_status_url
    assert (
        request.app.state.caption_repository.latest_transcript(
            project_id=uuid4_from_string(project_id),
            workspace_id=uuid4_from_string(workspace_id),
        )
        is None
    )


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
                workflow_repository=request.app.state.workflow_repository,
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


def test_concurrent_upload_init_respects_workspace_limit() -> None:
    request, repository, project_id, workspace_id, user_id = make_context()
    request.app.state.settings.upload_max_concurrent_per_workspace = 1

    async def initialize(filename: str):
        return await init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename=filename,
                mime_type="video/mp4",
                file_size_bytes=10_000,
            ),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )

    async def initialize_both():
        return await asyncio.gather(
            initialize("one.mp4"),
            initialize("two.mp4"),
            return_exceptions=True,
        )

    results = asyncio.run(initialize_both())
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, ApiError)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code == "upload_concurrency_limit"
    assert repository.count_active_uploads(workspace_id=uuid4_from_string(workspace_id)) == 1


def test_legacy_completion_transient_failure_remains_retryable(tmp_path) -> None:
    from app.services.object_references import ObjectReferenceService
    from app.services.storage_quota import StorageQuotaService

    class FailOnceObjectReferenceService(ObjectReferenceService):
        def __init__(self) -> None:
            super().__init__(storage_signer=FakeStorageSigner(), in_memory=True)
            self.failed = False

        async def create_reference(self, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("transient reference write failure")
            return await super().create_reference(**kwargs)

    request, repository, project_id, workspace_id, user_id = make_context()
    storage = LocalObjectStorage(root=tmp_path)
    quota = StorageQuotaService(in_memory=True)
    request.app.state.object_storage = storage
    request.app.state.storage_quota_service = quota
    request.app.state.object_reference_service = FailOnceObjectReferenceService()
    init_response = asyncio.run(
        init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename="retry.mp4",
                mime_type="video/mp4",
                file_size_bytes=10_000,
            ),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )
    upload = repository.uploads[init_response.video_id]
    storage.write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"x" * upload.file_size_bytes,
    )

    with pytest.raises(RuntimeError, match="transient reference write failure"):
        asyncio.run(
            complete_upload(
                project_id=uuid4_from_string(project_id),
                video_id=init_response.video_id,
                payload=UploadCompleteRequest(observed_file_size_bytes=10_000),
                request=request,
                actor=actor(workspace_id, user_id),
                repository=repository,
                preprocess_queue=PreprocessQueue(),
                workflow_repository=request.app.state.workflow_repository,
            )
        )

    held_usage = asyncio.run(quota.get_usage(uuid4_from_string(workspace_id)))
    assert repository.uploads[init_response.video_id].status == UploadStatus.UPLOADING
    assert held_usage.reserved_bytes == 10_000
    assert held_usage.used_bytes == 0

    completed = asyncio.run(
        complete_upload(
            project_id=uuid4_from_string(project_id),
            video_id=init_response.video_id,
            payload=UploadCompleteRequest(observed_file_size_bytes=10_000),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            preprocess_queue=PreprocessQueue(),
            workflow_repository=request.app.state.workflow_repository,
        )
    )
    committed_usage = asyncio.run(quota.get_usage(uuid4_from_string(workspace_id)))

    assert completed.status == UploadStatus.PROCESSING
    assert committed_usage.reserved_bytes == 0
    assert committed_usage.used_bytes == 10_000


def test_legacy_post_commit_failure_recovers_after_deadline_and_cannot_cancel(
    tmp_path,
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.services.object_references import ObjectReferenceService
    from app.services.storage_quota import StorageQuotaService

    request, repository, project_id, workspace_id, user_id = make_context()
    storage = LocalObjectStorage(root=tmp_path)
    quota = StorageQuotaService(in_memory=True)
    references = ObjectReferenceService(
        storage_signer=FakeStorageSigner(),
        in_memory=True,
    )
    request.app.state.object_storage = storage
    request.app.state.storage_quota_service = quota
    request.app.state.object_reference_service = references
    init_response = asyncio.run(
        init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename="post-commit.mp4",
                mime_type="video/mp4",
                file_size_bytes=4,
            ),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )
    upload = repository.uploads[init_response.video_id]
    stored_object = storage.write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"data",
    )
    original_complete = repository.complete_upload_for_processing
    attempts = 0

    async def fail_once(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient processing transition failure")
        return original_complete(**kwargs)

    repository.complete_upload_for_processing_async = fail_once
    with pytest.raises(RuntimeError, match="transient processing transition failure"):
        asyncio.run(
            complete_upload(
                project_id=uuid4_from_string(project_id),
                video_id=init_response.video_id,
                payload=UploadCompleteRequest(observed_file_size_bytes=4),
                request=request,
                actor=actor(workspace_id, user_id),
                repository=repository,
                preprocess_queue=PreprocessQueue(),
                workflow_repository=request.app.state.workflow_repository,
            )
        )

    usage = asyncio.run(quota.get_usage(uuid4_from_string(workspace_id)))
    upload.legacy_upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ApiError) as cancel_error:
        asyncio.run(
            cancel_upload(
                project_id=uuid4_from_string(project_id),
                video_id=init_response.video_id,
                request=request,
                actor=actor(workspace_id, user_id),
                repository=repository,
            )
        )

    recovered = asyncio.run(
        complete_upload(
            project_id=uuid4_from_string(project_id),
            video_id=init_response.video_id,
            payload=UploadCompleteRequest(observed_file_size_bytes=4),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            preprocess_queue=PreprocessQueue(),
            workflow_repository=request.app.state.workflow_repository,
        )
    )

    assert usage.used_bytes == 4
    assert usage.reserved_bytes == 0
    assert cancel_error.value.code == "upload_not_cancelable"
    assert stored_object.exists()
    assert recovered.status == UploadStatus.PROCESSING


def test_cancel_upload_deletes_object_and_releases_concurrency_slot(tmp_path) -> None:
    request, repository, project_id, workspace_id, user_id = make_context()
    request.app.state.settings.upload_max_concurrent_per_workspace = 1
    storage = LocalObjectStorage(root=tmp_path)
    request.app.state.object_storage = storage
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
    upload = repository.uploads[init_response.video_id]
    stored_object = storage.write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"uploaded bytes",
    )

    canceled = asyncio.run(
        cancel_upload(
            project_id=uuid4_from_string(project_id),
            video_id=init_response.video_id,
            request=request,
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
    assert not stored_object.exists()
    assert replacement.status == UploadStatus.UPLOADING


def test_cancel_upload_retries_reference_and_object_cleanup(tmp_path) -> None:
    from app.services.object_references import ObjectReferenceService
    from app.services.storage_quota import StorageQuotaService

    request, repository, project_id, workspace_id, user_id = make_context()
    storage = LocalObjectStorage(root=tmp_path)
    quota = StorageQuotaService(in_memory=True)
    references = ObjectReferenceService(
        storage_signer=FakeStorageSigner(),
        in_memory=True,
    )
    request.app.state.object_storage = storage
    request.app.state.storage_quota_service = quota
    request.app.state.object_reference_service = references
    init_response = asyncio.run(
        init_upload(
            project_id=uuid4_from_string(project_id),
            payload=UploadInitRequest(
                filename="cancel-retry.mp4",
                mime_type="video/mp4",
                file_size_bytes=4,
            ),
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
            storage_signer=FakeStorageSigner(),
        )
    )
    upload = repository.uploads[init_response.video_id]
    stored_object = storage.write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"data",
    )

    async def fail_commit(_reservation_id):
        raise RuntimeError("transient quota commit failure")

    quota.commit = fail_commit
    with pytest.raises(RuntimeError, match="transient quota commit failure"):
        asyncio.run(
            complete_upload(
                project_id=uuid4_from_string(project_id),
                video_id=init_response.video_id,
                payload=UploadCompleteRequest(observed_file_size_bytes=4),
                request=request,
                actor=actor(workspace_id, user_id),
                repository=repository,
                preprocess_queue=PreprocessQueue(),
                workflow_repository=request.app.state.workflow_repository,
            )
        )
    assert upload.source_object_reference_id is not None

    original_delete = storage.delete_object
    delete_attempts = 0

    def fail_delete_once(*, bucket, path):
        nonlocal delete_attempts
        delete_attempts += 1
        if delete_attempts == 1:
            raise RuntimeError("transient object deletion failure")
        return original_delete(bucket=bucket, path=path)

    storage.delete_object = fail_delete_once
    with pytest.raises(RuntimeError, match="transient object deletion failure"):
        asyncio.run(
            cancel_upload(
                project_id=uuid4_from_string(project_id),
                video_id=init_response.video_id,
                request=request,
                actor=actor(workspace_id, user_id),
                repository=repository,
            )
        )
    assert stored_object.exists()

    retried = asyncio.run(
        cancel_upload(
            project_id=uuid4_from_string(project_id),
            video_id=init_response.video_id,
            request=request,
            actor=actor(workspace_id, user_id),
            repository=repository,
        )
    )
    usage = asyncio.run(quota.get_usage(uuid4_from_string(workspace_id)))
    assert retried.error_code == "upload_canceled"
    assert usage.reserved_bytes == 0
    assert not stored_object.exists()


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
