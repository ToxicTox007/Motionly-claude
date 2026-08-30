import asyncio
import hashlib
import tempfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.services.generic_media_upload import GenericMediaUploadSessionService
from app.services.local_storage import LocalObjectStorage
from app.services.object_references import ObjectReferenceService
from app.services.storage import StorageSigner
from app.services.storage_quota import StorageQuotaService
from app.services.upload_session import UploadSessionStatus


def make_service() -> tuple[
    GenericMediaUploadSessionService,
    LocalObjectStorage,
    StorageQuotaService,
    ObjectReferenceService,
]:
    signer = StorageSigner(base_url="http://localhost:8000")
    storage = LocalObjectStorage(root=tempfile.mkdtemp(prefix="motionly-generic-media-"))
    quota = StorageQuotaService(in_memory=True)
    references = ObjectReferenceService(storage_signer=signer, in_memory=True)
    return (
        GenericMediaUploadSessionService(
            storage_signer=signer,
            object_storage=storage,
            quota_service=quota,
            object_reference_service=references,
        ),
        storage,
        quota,
        references,
    )


def initiate_broll_session(
    service: GenericMediaUploadSessionService,
    *,
    workspace_id,
    project_id,
    owner_id,
    content: bytes,
    sha256_hash: str | None = None,
):
    return asyncio.run(
        service.initiate(
            workspace_id=workspace_id,
            project_id=project_id,
            owner_type="stock_asset",
            owner_id=owner_id,
            object_type="broll_asset",
            final_storage_bucket="raw-uploads",
            final_storage_path=(
                f"workspaces/{workspace_id}/projects/{project_id}/broll/{owner_id}.mp4"
            ),
            filename="cutaway.mp4",
            mime_type="video/mp4",
            file_size_bytes=len(content),
            sha256_hash=sha256_hash,
        )
    )


def write_part(storage: LocalObjectStorage, session, content: bytes) -> None:
    storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
        content=content,
    )


def test_generic_media_completion_is_verified_idempotent_and_deletes_lifecycle() -> None:
    service, storage, quota, references = make_service()
    workspace_id, project_id, owner_id = uuid4(), uuid4(), uuid4()
    content = b"verified B-roll media"
    session = initiate_broll_session(
        service,
        workspace_id=workspace_id,
        project_id=project_id,
        owner_id=owner_id,
        content=content,
        sha256_hash=hashlib.sha256(content).hexdigest(),
    )

    usage = asyncio.run(quota.get_usage(workspace_id))
    assert usage.reserved_bytes == len(content)
    write_part(storage, session, content)
    completed = asyncio.run(
        service.complete(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    retry = asyncio.run(
        service.complete(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )

    assert completed.session.status == UploadSessionStatus.COMPLETED
    assert completed.session.verified_size_bytes == len(content)
    assert completed.session.verified_sha256 == hashlib.sha256(content).hexdigest()
    assert retry.reference.id == completed.reference.id
    assert completed.reference.owner_type == "stock_asset"
    assert completed.reference.owner_id == owner_id
    assert completed.reference.object_type == "broll_asset"
    assert asyncio.run(quota.get_reservation_state(session.quota_reservation_id)) == "committed"
    assert asyncio.run(quota.get_usage(workspace_id)).used_bytes == len(content)
    assert storage.object_path(
        bucket=completed.reference.storage_bucket,
        path=completed.reference.storage_path,
    ).exists()

    asyncio.run(
        service.delete_completed(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    assert asyncio.run(quota.get_reservation_state(session.quota_reservation_id)) == "deleted"
    assert asyncio.run(quota.get_usage(workspace_id)).used_bytes == 0
    assert not storage.object_path(
        bucket=completed.reference.storage_bucket,
        path=completed.reference.storage_path,
    ).exists()
    with pytest.raises(ApiError) as error:
        asyncio.run(
            references.get_active_reference(
                workspace_id=workspace_id,
                project_id=project_id,
                reference_id=completed.reference.id,
            )
        )
    assert error.value.code == "object_reference_not_found"


def test_generic_media_rejects_cross_tenant_and_releases_failed_or_aborted_uploads() -> None:
    service, storage, quota, _ = make_service()
    workspace_id, project_id, owner_id = uuid4(), uuid4(), uuid4()
    session = initiate_broll_session(
        service,
        workspace_id=workspace_id,
        project_id=project_id,
        owner_id=owner_id,
        content=b"four",
    )

    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.get(
                session_id=session.id,
                workspace_id=uuid4(),
                project_id=project_id,
            )
        )
    assert error.value.code == "workspace_access_denied"

    write_part(storage, session, b"bad")
    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.confirm_part(
                session_id=session.id,
                part_number=1,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        )
    assert error.value.code == "upload_part_size_mismatch"
    aborted = asyncio.run(
        service.abort(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    assert aborted.status == UploadSessionStatus.ABORTED
    assert asyncio.run(quota.get_reservation_state(session.quota_reservation_id)) == "released"
    assert asyncio.run(quota.get_usage(workspace_id)).reserved_bytes == 0

    hash_session = initiate_broll_session(
        service,
        workspace_id=workspace_id,
        project_id=project_id,
        owner_id=uuid4(),
        content=b"four",
        sha256_hash=hashlib.sha256(b"expected").hexdigest(),
    )
    write_part(storage, hash_session, b"four")
    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.complete(
                session_id=hash_session.id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        )
    assert error.value.code == "upload_hash_mismatch"
    failed = asyncio.run(
        service.get(
            session_id=hash_session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    assert failed.status == UploadSessionStatus.FAILED
    assert asyncio.run(quota.get_reservation_state(hash_session.quota_reservation_id)) == "released"


def test_generic_media_expires_unfinished_sessions_and_releases_quota() -> None:
    service, _, quota, _ = make_service()
    workspace_id, project_id, owner_id = uuid4(), uuid4(), uuid4()
    session = initiate_broll_session(
        service,
        workspace_id=workspace_id,
        project_id=project_id,
        owner_id=owner_id,
        content=b"expire",
    )
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    expired = asyncio.run(
        service.get(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    assert expired.status == UploadSessionStatus.EXPIRED
    assert asyncio.run(quota.get_reservation_state(session.quota_reservation_id)) == "released"
    assert asyncio.run(quota.get_usage(workspace_id)).reserved_bytes == 0


def test_generic_media_keeps_committed_quota_until_physical_delete_succeeds() -> None:
    class FailingDeleteStorage(LocalObjectStorage):
        blocked_path: str | None = None

        def delete_object(self, *, bucket: str, path: str) -> None:
            if path == self.blocked_path:
                raise OSError("storage temporarily unavailable")
            super().delete_object(bucket=bucket, path=path)

    signer = StorageSigner(base_url="http://localhost:8000")
    storage = FailingDeleteStorage(root=tempfile.mkdtemp(prefix="motionly-generic-strict-"))
    quota = StorageQuotaService(in_memory=True)
    references = ObjectReferenceService(storage_signer=signer, in_memory=True)
    service = GenericMediaUploadSessionService(
        storage_signer=signer,
        object_storage=storage,
        quota_service=quota,
        object_reference_service=references,
    )
    workspace_id, project_id, owner_id = uuid4(), uuid4(), uuid4()
    content = b"strict physical deletion"
    session = initiate_broll_session(
        service,
        workspace_id=workspace_id,
        project_id=project_id,
        owner_id=owner_id,
        content=content,
        sha256_hash=hashlib.sha256(content).hexdigest(),
    )
    write_part(storage, session, content)
    completed = asyncio.run(
        service.complete(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )

    storage.blocked_path = completed.reference.storage_path
    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.delete_completed(
                session_id=session.id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        )
    assert error.value.code == "media_deletion_pending"
    assert asyncio.run(quota.get_reservation_state(session.quota_reservation_id)) == "committed"
    assert asyncio.run(quota.get_usage(workspace_id)).used_bytes == len(content)
    assert (
        asyncio.run(
            references.get_active_reference(
                workspace_id=workspace_id,
                project_id=project_id,
                reference_id=completed.reference.id,
            )
        ).id
        == completed.reference.id
    )

    storage.blocked_path = None
    asyncio.run(
        service.delete_completed(
            session_id=session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    assert asyncio.run(quota.get_reservation_state(session.quota_reservation_id)) == "deleted"
    assert asyncio.run(quota.get_usage(workspace_id)).used_bytes == 0


def test_persistent_generic_media_creation_uses_single_atomic_rpc() -> None:
    from types import SimpleNamespace

    class DatabaseClient:
        def __init__(self) -> None:
            self.calls = []

        async def async_request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            return SimpleNamespace(status_code=200, json=lambda: True)

    signer = StorageSigner(base_url="http://localhost:8000")
    quota = StorageQuotaService(in_memory=True)
    client = DatabaseClient()
    service = GenericMediaUploadSessionService(
        storage_signer=signer,
        object_storage=LocalObjectStorage(root=tempfile.mkdtemp(prefix="motionly-generic-rpc-")),
        quota_service=quota,
        object_reference_service=ObjectReferenceService(storage_signer=signer, in_memory=True),
        db_client=client,
    )
    workspace_id, project_id, owner_id = uuid4(), uuid4(), uuid4()

    session = asyncio.run(
        service.initiate(
            workspace_id=workspace_id,
            project_id=project_id,
            owner_type="stock_asset",
            owner_id=owner_id,
            object_type="broll_asset",
            final_storage_bucket="raw-uploads",
            final_storage_path=(
                f"workspaces/{workspace_id}/projects/{project_id}/broll/{owner_id}.mp4"
            ),
            filename="atomic.mp4",
            mime_type="video/mp4",
            file_size_bytes=4,
        )
    )

    assert len(client.calls) == 1
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "/rpc/motionly_create_generic_media_upload_session")
    assert kwargs["json"]["p_session_id"] == str(session.id)
    assert kwargs["json"]["p_reservation_id"] == str(session.quota_reservation_id)
    assert len(kwargs["json"]["p_parts"]) == 1
    assert asyncio.run(quota.get_usage(workspace_id)).reserved_bytes == 0
