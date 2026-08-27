import hashlib
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.errors import ApiError
from app.repositories.uploads import UploadRecord
from app.schemas.multipart_uploads import MultipartUploadInitRequest
from app.schemas.uploads import UploadInitRequest, UploadStatus
from app.services.local_storage import LocalObjectStorage
from app.services.retention import SIGNED_UPLOAD_CLEANUP_GRACE_SECONDS
from app.services.storage import StorageSigner
from app.services.upload_validation import (
    MAX_UPLOAD_FILE_SIZE_BYTES,
    validate_upload_preflight,
)
from app.services.worker_media import materialize_upload_source


def test_upload_contract_hard_caps_512_mib_even_with_larger_runtime_limit() -> None:
    oversized = MAX_UPLOAD_FILE_SIZE_BYTES + 1
    with pytest.raises(ValidationError):
        MultipartUploadInitRequest(
            filename="source.mp4",
            mime_type="video/mp4",
            file_size_bytes=oversized,
        )
    with pytest.raises(ValidationError):
        UploadInitRequest(
            filename="source.mp4",
            mime_type="video/mp4",
            file_size_bytes=oversized,
        )
    with pytest.raises(ApiError) as exc_info:
        validate_upload_preflight(
            filename="source.mp4",
            mime_type="video/mp4",
            file_size_bytes=oversized,
            max_file_size_bytes=oversized * 2,
        )
    assert exc_info.value.code == "upload_file_too_large"


def test_worker_signs_reference_at_execution_and_consumes_verified_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import app.services.worker_media as worker_media

    workspace_id = uuid4()
    project_id = uuid4()
    reference_id = uuid4()
    content = b"verified source bytes"
    digest = hashlib.sha256(content).hexdigest()
    observed: dict[str, object] = {}

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_BASE_URL", "https://api.motionly.test")
    monkeypatch.setenv("WORKER_API_TOKEN", "worker-secret")

    class SignResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "signed_urls": {
                    str(reference_id): "https://storage.example.test/source.mp4?token=short-lived"
                }
            }

    def fake_post(url, *, headers, json, timeout):
        observed.update(
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
        )
        return SignResponse()

    class StreamResponse:
        status_code = 200
        headers = {"content-length": str(len(content))}

        @staticmethod
        def iter_bytes(*, chunk_size):
            assert chunk_size == 1024 * 1024
            yield content

    @contextmanager
    def fake_stream(url, *, timeout):
        observed["download_url"] = url
        observed["download_timeout"] = timeout
        yield StreamResponse()

    monkeypatch.setattr(worker_media.httpx, "post", fake_post)
    monkeypatch.setattr(worker_media, "safe_http_stream", fake_stream)

    storage = LocalObjectStorage(root=tmp_path)
    upload = UploadRecord(
        id=uuid4(),
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path=(f"workspaces/{workspace_id}/projects/{project_id}/raw/source.mp4"),
        original_filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=len(content),
        status=UploadStatus.READY,
        source_object_reference_id=reference_id,
        verified_size_bytes=len(content),
        verified_sha256=digest,
    )

    source_path = materialize_upload_source(upload=upload, storage=storage)

    assert source_path.read_bytes() == content
    assert observed["url"] == "https://api.motionly.test/internal/media/sign-urls"
    assert observed["headers"] == {
        "content-type": "application/json",
        "x-motionly-worker-token": "worker-secret",
    }
    assert observed["json"] == {
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "reference_ids": [str(reference_id)],
    }
    assert str(observed["download_url"]).startswith("https://storage.example.test/source.mp4")


def test_production_worker_refuses_storage_path_without_durable_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    workspace_id = uuid4()
    project_id = uuid4()
    upload = UploadRecord(
        id=uuid4(),
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path=(f"workspaces/{workspace_id}/projects/{project_id}/raw/source.mp4"),
        original_filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=4,
        status=UploadStatus.READY,
    )

    with pytest.raises(ApiError) as exc_info:
        materialize_upload_source(
            upload=upload,
            storage=LocalObjectStorage(root=tmp_path),
        )
    assert exc_info.value.code == "durable_media_reference_missing"


def test_supabase_upload_ttl_is_truthful_and_cleanup_outlives_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = StorageSigner(
        supabase_url="https://example.supabase.co",
        service_role_key="service-role",
    )
    monkeypatch.setattr(
        signer,
        "_create_supabase_signed_upload_url",
        lambda **_kwargs: "https://example.supabase.co/storage/v1/upload/sign/source",
    )

    signed = signer.create_signed_upload(
        bucket="raw-uploads",
        path="workspaces/w/projects/p/raw/source.mp4",
        mime_type="video/mp4",
        expires_in_seconds=900,
    )

    assert signed.expires_in_seconds == 7200
    assert SIGNED_UPLOAD_CLEANUP_GRACE_SECONDS > signed.expires_in_seconds
