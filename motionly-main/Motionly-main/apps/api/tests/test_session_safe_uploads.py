import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.errors import ApiError
from app.core.config import get_settings
from app.main import create_app
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.multipart_uploads import MultipartUploadInitRequest, PartCompleteRequest
from app.services.local_storage import LocalObjectStorage
from app.services.object_references import ObjectReferenceService
from app.services.storage import StorageSigner
from app.services.storage_quota import StorageQuotaService
from app.services.upload_session import UploadSessionService, UploadSessionStatus

WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
USER_ID = UUID("00000000-0000-4000-8000-000000000001")
PART_SIZE = UploadSessionService.PART_SIZE_BYTES


def _service(tmp_path: Path):
    repository = InMemoryUploadRepository()
    workspace_id = uuid4()
    project_id = uuid4()
    repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    signer = StorageSigner(base_url="http://api.local")
    storage = LocalObjectStorage(root=tmp_path)
    quota = StorageQuotaService(in_memory=True)
    references = ObjectReferenceService(storage_signer=signer, in_memory=True)
    service = UploadSessionService(
        storage_signer=signer,
        upload_repository=repository,
        object_storage=storage,
        quota_service=quota,
        object_reference_service=references,
    )
    return service, repository, storage, quota, references, workspace_id, project_id


async def _init(service, workspace_id, project_id, *, size, sha256_hash=None):
    return await service.initiate(
        workspace_id=workspace_id,
        project_id=project_id,
        filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=size,
        storage_bucket="raw-uploads",
        max_file_size_bytes=100 * 1024 * 1024,
        max_active_uploads=10,
        sha256_hash=sha256_hash,
    )


@pytest.mark.anyio
async def test_init_part_math_max_five_urls_and_resume(tmp_path: Path) -> None:
    service, _repo, _storage, _quota, _refs, workspace_id, project_id = _service(tmp_path)
    session = await _init(service, workspace_id, project_id, size=PART_SIZE * 6 + 7)

    urls = await service.create_pending_part_urls(session)
    resumed = await service.resume(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )

    assert session.part_count == 7
    assert [part.expected_size_bytes for part in session.parts] == [PART_SIZE] * 6 + [7]
    assert len(urls) == 5
    assert urls[0][1].headers == {"content-type": "video/mp4"}
    assert session.parts[0].object_path == (
        f"{session.final_storage_path}.parts/{session.id}/1"
    )
    assert resumed.id == session.id
    assert resumed.completed_parts == 0


@pytest.mark.anyio
async def test_resume_reconciles_part_stored_before_acknowledgement(tmp_path: Path) -> None:
    service, _repo, storage, _quota, _refs, workspace_id, project_id = _service(tmp_path)
    session = await _init(service, workspace_id, project_id, size=11)
    part = session.parts[0]

    # Simulate storage accepting the signed PUT while the browser loses the
    # response before it can call the part-confirmation endpoint.
    storage.write_object(
        bucket=session.final_storage_bucket,
        path=part.object_path,
        content=b"hello world",
    )

    resumed = await service.resume(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    pending = await service.create_pending_part_urls(resumed)

    assert resumed.completed_parts == 1
    assert resumed.uploaded_bytes == 11
    assert resumed.parts[0].status.value == "uploaded"
    assert pending == []


@pytest.mark.anyio
async def test_resume_deletes_wrong_sized_unacknowledged_part(tmp_path: Path) -> None:
    service, _repo, storage, _quota, _refs, workspace_id, project_id = _service(tmp_path)
    session = await _init(service, workspace_id, project_id, size=11)
    part = session.parts[0]
    storage.write_object(
        bucket=session.final_storage_bucket,
        path=part.object_path,
        content=b"partial",
    )

    resumed = await service.resume(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    pending = await service.create_pending_part_urls(resumed)

    assert resumed.completed_parts == 0
    assert [part_number for part_number, _signed in pending] == [1]
    assert not storage.object_path(
        bucket=session.final_storage_bucket,
        path=part.object_path,
    ).exists()


@pytest.mark.anyio
async def test_confirm_inspects_exact_server_part_and_is_idempotent(tmp_path: Path) -> None:
    service, _repo, storage, _quota, _refs, workspace_id, project_id = _service(tmp_path)
    session = await _init(service, workspace_id, project_id, size=11)
    part = session.parts[0]

    with pytest.raises(ApiError) as missing:
        await service.confirm_part(
            session_id=session.id,
            part_number=1,
            workspace_id=workspace_id,
            project_id=project_id,
            etag="client-value",
        )
    assert missing.value.code == "storage_object_missing"

    storage.write_object(
        bucket=session.final_storage_bucket,
        path=part.object_path,
        content=b"wrong",
    )
    with pytest.raises(ApiError) as wrong:
        await service.confirm_part(
            session_id=session.id,
            part_number=1,
            workspace_id=workspace_id,
            project_id=project_id,
            etag="client-value",
        )
    assert wrong.value.code == "upload_part_size_mismatch"

    storage.write_object(
        bucket=session.final_storage_bucket,
        path=part.object_path,
        content=b"hello world",
    )
    first = await service.confirm_part(
        session_id=session.id,
        part_number=1,
        workspace_id=workspace_id,
        project_id=project_id,
        etag="client-value",
    )
    duplicate = await service.confirm_part(
        session_id=session.id,
        part_number=1,
        workspace_id=workspace_id,
        project_id=project_id,
        etag="different-untrusted-value",
    )

    assert first.verified_size_bytes == 11
    assert duplicate == first
    assert session.completed_parts == 1


@pytest.mark.anyio
async def test_complete_rejects_unconfirmed_and_hash_mismatch_releases_quota(
    tmp_path: Path,
) -> None:
    service, repository, storage, quota, _refs, workspace_id, project_id = _service(tmp_path)
    session = await _init(service, workspace_id, project_id, size=4, sha256_hash="0" * 64)

    with pytest.raises(ApiError) as incomplete:
        await service.complete(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    assert incomplete.value.code == "upload_parts_incomplete"

    storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
        content=b"data",
    )
    await service.confirm_part(
        session_id=session.id,
        part_number=1,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    with pytest.raises(ApiError) as mismatch:
        await service.complete(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )

    usage = await quota.get_usage(workspace_id)
    upload = repository.uploads[session.uploaded_video_id]
    assert mismatch.value.code == "upload_hash_mismatch"
    assert session.status == UploadSessionStatus.FAILED
    assert upload.status.value == "failed"
    assert usage.reserved_bytes == 0
    assert usage.used_bytes == 0


@pytest.mark.anyio
async def test_successful_ordered_assembly_commit_reference_and_retry(tmp_path: Path) -> None:
    service, repository, storage, quota, references, workspace_id, project_id = _service(tmp_path)
    first = b"a" * PART_SIZE
    second = b"ordered-tail"
    expected = hashlib.sha256(first + second).hexdigest()
    session = await _init(
        service,
        workspace_id,
        project_id,
        size=len(first) + len(second),
        sha256_hash=expected,
    )
    storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[1].object_path,
        content=second,
    )
    storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
        content=first,
    )
    await service.confirm_part(
        session_id=session.id,
        part_number=2,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    await service.confirm_part(
        session_id=session.id,
        part_number=1,
        workspace_id=workspace_id,
        project_id=project_id,
    )

    completed = await service.complete(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    retried = await service.complete(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    usage = await quota.get_usage(workspace_id)
    final_path = storage.ensure_object(
        bucket=session.final_storage_bucket,
        path=session.final_storage_path,
    )
    upload = repository.uploads[session.uploaded_video_id]

    assert final_path.read_bytes() == first + second
    assert completed.session.verified_sha256 == expected
    assert retried.reference.id == completed.reference.id
    assert upload.status.value == "processing"
    assert upload.source_object_reference_id == completed.reference.id
    assert usage.used_bytes == len(first) + len(second)
    assert usage.used_object_count == 1
    assert usage.reserved_bytes == 0
    assert not storage.object_path(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
    ).exists()
    assert len(references.grants) == 0


@pytest.mark.anyio
async def test_post_commit_completion_failure_resumes_from_completing(tmp_path: Path) -> None:
    service, repository, storage, quota, _refs, workspace_id, project_id = _service(tmp_path)
    session = await _init(service, workspace_id, project_id, size=4)
    storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
        content=b"data",
    )
    await service.confirm_part(
        session_id=session.id,
        part_number=1,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    original_complete = repository.complete_upload_for_processing
    attempts = 0

    async def fail_once(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient upload transition failure")
        return original_complete(**kwargs)

    repository.complete_upload_for_processing_async = fail_once
    with pytest.raises(ApiError) as first_failure:
        await service.complete(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )

    usage_after_failure = await quota.get_usage(workspace_id)
    assert first_failure.value.code == "upload_completion_failed"
    assert session.status == UploadSessionStatus.COMPLETING
    assert session.quota_committed_at is not None
    assert usage_after_failure.used_bytes == 4
    assert storage.object_path(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
    ).exists()

    recovered = await service.complete(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    assert recovered.session.status == UploadSessionStatus.COMPLETED
    assert recovered.upload.status.value == "processing"
    assert not storage.object_path(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
    ).exists()


@pytest.mark.anyio
async def test_abort_retries_release_failure_and_part_cleanup(tmp_path: Path) -> None:
    service, _repository, storage, quota, _refs, workspace_id, project_id = _service(
        tmp_path
    )
    session = await _init(service, workspace_id, project_id, size=4)
    part_path = storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
        content=b"data",
    )
    original_release = quota.release_reservation
    attempts = 0

    async def fail_release_once(reservation_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient quota release failure")
        return await original_release(reservation_id)

    quota.release_reservation = fail_release_once
    with pytest.raises(RuntimeError, match="transient quota release failure"):
        await service.abort(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )

    assert session.status == UploadSessionStatus.ABORTED
    assert part_path.exists()
    assert (await quota.get_usage(workspace_id)).reserved_bytes == 4

    retried = await service.abort(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    assert retried.status == UploadSessionStatus.ABORTED
    assert not part_path.exists()
    assert (await quota.get_usage(workspace_id)).reserved_bytes == 0


@pytest.mark.anyio
async def test_abort_retries_storage_deletion_failure(tmp_path: Path) -> None:
    service, _repository, storage, quota, _refs, workspace_id, project_id = _service(
        tmp_path
    )
    session = await _init(service, workspace_id, project_id, size=4)
    part_path = storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
        content=b"data",
    )
    original_delete = storage.delete_object
    attempts = 0

    def fail_delete_once(*, bucket, path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient storage deletion failure")
        return original_delete(bucket=bucket, path=path)

    storage.delete_object = fail_delete_once
    with pytest.raises(RuntimeError, match="transient storage deletion failure"):
        await service.abort(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )

    assert session.status == UploadSessionStatus.ABORTED
    assert part_path.exists()
    assert (await quota.get_usage(workspace_id)).reserved_bytes == 0

    retried = await service.abort(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    assert retried.status == UploadSessionStatus.ABORTED
    assert not part_path.exists()


@pytest.mark.anyio
async def test_completed_retry_repairs_part_deletion_failure(tmp_path: Path) -> None:
    service, _repository, storage, _quota, _refs, workspace_id, project_id = _service(
        tmp_path
    )
    session = await _init(service, workspace_id, project_id, size=4)
    part_path = storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
        content=b"data",
    )
    await service.confirm_part(
        session_id=session.id,
        part_number=1,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    original_delete = storage.delete_object
    attempts = 0

    def fail_delete_once(*, bucket, path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient completed-part deletion failure")
        return original_delete(bucket=bucket, path=path)

    storage.delete_object = fail_delete_once
    completed = await service.complete(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    assert completed.session.status == UploadSessionStatus.COMPLETED
    assert part_path.exists()

    retried = await service.complete(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    assert retried.reference.id == completed.reference.id
    assert not part_path.exists()


@pytest.mark.anyio
async def test_quota_denial_and_reservation_transitions_are_idempotent() -> None:
    quota = StorageQuotaService(in_memory=True)
    workspace_id = uuid4()
    reservation_id = uuid4()
    expiry = datetime(2099, 1, 1, tzinfo=UTC)
    quota.set_in_memory_limits(workspace_id=workspace_id, max_bytes=10, max_objects=1)

    reserved = await quota.reserve(
        reservation_id=reservation_id,
        workspace_id=workspace_id,
        byte_amount=10,
        object_amount=1,
        expires_at=expiry,
    )
    duplicate = await quota.reserve(
        reservation_id=reservation_id,
        workspace_id=workspace_id,
        byte_amount=10,
        object_amount=1,
        expires_at=expiry,
    )
    denied = await quota.reserve(
        reservation_id=uuid4(),
        workspace_id=workspace_id,
        byte_amount=1,
        object_amount=1,
        expires_at=expiry,
    )
    assert reserved.allowed and duplicate.allowed
    assert not denied.allowed
    assert await quota.commit(reservation_id)
    assert await quota.commit(reservation_id)
    assert not await quota.release_reservation(reservation_id)
    usage = await quota.get_usage(workspace_id)
    assert usage.used_bytes == 10
    assert usage.used_object_count == 1
    assert usage.reserved_bytes == 0


@pytest.mark.anyio
async def test_abort_releases_once_deletes_parts_and_cross_tenant_denied(tmp_path: Path) -> None:
    service, _repo, storage, quota, _refs, workspace_id, project_id = _service(tmp_path)
    session = await _init(service, workspace_id, project_id, size=4)
    storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
        content=b"data",
    )

    with pytest.raises(ApiError) as foreign:
        await service.get(
            session_id=session.id,
            workspace_id=uuid4(),
            project_id=project_id,
        )
    assert foreign.value.code == "workspace_access_denied"

    aborted = await service.abort(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    duplicate = await service.abort(
        session_id=session.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    usage = await quota.get_usage(workspace_id)
    assert aborted.status == UploadSessionStatus.ABORTED
    assert duplicate.status == UploadSessionStatus.ABORTED
    assert usage.reserved_bytes == 0
    assert not storage.object_path(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
    ).exists()


@pytest.mark.anyio
async def test_reference_refresh_expiry_grants_and_worker_batch_scope(tmp_path: Path) -> None:
    signer = StorageSigner(base_url="http://api.local")
    service = ObjectReferenceService(storage_signer=signer, in_memory=True)
    workspace_id = uuid4()
    project_id = uuid4()
    reference = await service.create_reference(
        workspace_id=workspace_id,
        project_id=project_id,
        storage_bucket="raw-uploads",
        storage_path=f"workspaces/{workspace_id}/projects/{project_id}/raw/source.mp4",
        owner_type="uploaded_video",
        owner_id=uuid4(),
        object_type="source_video",
        mime_type="video/mp4",
        size_bytes=4,
        sha256=hashlib.sha256(b"data").hexdigest(),
    )
    same = await service.create_reference(
        workspace_id=workspace_id,
        project_id=project_id,
        storage_bucket="raw-uploads",
        storage_path=f"workspaces/{workspace_id}/projects/{project_id}/raw/source.mp4",
        owner_type="uploaded_video",
        owner_id=reference.owner_id,
        object_type="source_video",
        mime_type="video/mp4",
        size_bytes=4,
        sha256=hashlib.sha256(b"data").hexdigest(),
    )
    before = datetime.now(UTC)
    refreshed = await service.refresh_urls(
        workspace_id=workspace_id,
        project_id=project_id,
        reference_ids=[reference.id],
        principal_id=USER_ID,
    )
    assert same.id == reference.id
    assert refreshed[0].expires_at >= before.replace(microsecond=0)
    assert 299 <= (refreshed[0].expires_at - before).total_seconds() <= 301
    browser_grant = service.grants[-1]
    assert browser_grant["ttl_seconds"] == 300
    assert "url" not in browser_grant and "signed_url" not in browser_grant

    grant_count = len(service.grants)
    with pytest.raises(ApiError):
        await service.get_worker_urls(
            workspace_id=workspace_id,
            project_id=project_id,
            reference_ids=[reference.id, uuid4()],
        )
    assert len(service.grants) == grant_count

    worker = await service.get_worker_urls(
        workspace_id=workspace_id,
        project_id=project_id,
        reference_ids=[reference.id],
    )
    assert worker[reference.id].ttl_seconds == 7200
    assert service.grants[-1]["purpose"] == "worker_processing"
    with pytest.raises(ApiError):
        await service.get_worker_urls(
            workspace_id=uuid4(),
            project_id=project_id,
            reference_ids=[reference.id],
        )


@pytest.mark.anyio
async def test_worker_route_auth_and_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SETTINGS_ENV_FILE", "none")
    monkeypatch.setenv("WORKER_API_TOKEN", "task6-worker-token")
    monkeypatch.setenv("MEDIA_WORK_DIR", str(tmp_path))
    get_settings.cache_clear()
    app = create_app()
    project_id = UUID("10000000-0000-4000-8000-000000000001")
    reference = await app.state.object_reference_service.create_reference(
        workspace_id=WORKSPACE_ID,
        project_id=project_id,
        storage_bucket="raw-uploads",
        storage_path=(
            f"workspaces/{WORKSPACE_ID}/projects/{project_id}/raw/source.mp4"
        ),
        owner_type="uploaded_video",
        owner_id=uuid4(),
        object_type="source_video",
    )
    client = TestClient(app)
    payload = {
        "workspace_id": str(WORKSPACE_ID),
        "project_id": str(project_id),
        "reference_ids": [str(reference.id)],
    }
    assert client.post("/internal/media/sign-urls", json=payload).status_code == 403
    response = client.post(
        "/internal/media/sign-urls",
        json=payload,
        headers={"x-motionly-worker-token": "task6-worker-token"},
    )
    assert response.status_code == 200
    assert response.json()["ttl_seconds"] == 7200
    payload["workspace_id"] = str(uuid4())
    denied = client.post(
        "/internal/media/sign-urls",
        json=payload,
        headers={"x-motionly-worker-token": "task6-worker-token"},
    )
    assert denied.status_code == 404



def test_schema_validation_storage_signer_caps_and_route_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        MultipartUploadInitRequest(
            filename="x.mp4",
            mime_type="video/mp4",
            file_size_bytes=1,
            sha256_hash="A" * 64,
        )
    with pytest.raises(ValidationError):
        PartCompleteRequest(etag="x" * 257)

    observed = []
    signer = StorageSigner(
        supabase_url="https://example.supabase.co",
        service_role_key="service-role",
    )

    def fake_sign(*, bucket, path, expires_in_seconds):
        observed.append(expires_in_seconds)
        return f"https://storage.invalid/{bucket}/{path}"

    monkeypatch.setattr(signer, "_create_supabase_signed_download_url", fake_sign)
    signer.create_signed_download(bucket="raw", path="x", expires_in_seconds=7200)
    signer.create_signed_worker_download(bucket="raw", path="x", expires_in_seconds=9999)
    assert observed == [300, 7200]

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SETTINGS_ENV_FILE", "none")
    get_settings.cache_clear()
    app = create_app()
    paths = set(app.openapi()["paths"])
    assert {
        "/projects/{project_id}/uploads/multipart/init",
        "/projects/{project_id}/uploads/multipart/{session_id}",
        "/projects/{project_id}/uploads/multipart/{session_id}/parts/{part_number}/complete",
        "/projects/{project_id}/uploads/multipart/{session_id}/complete",
        "/projects/{project_id}/uploads/multipart/{session_id}/abort",
        "/projects/{project_id}/media/refresh-urls",
        "/projects/{project_id}/media/quota",
        "/internal/media/sign-urls",
    }.issubset(paths)
