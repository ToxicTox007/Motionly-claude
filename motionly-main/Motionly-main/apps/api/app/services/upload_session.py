"""Session-safe multipart uploads with durable quota and object references."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4

from app.api.errors import ApiError
from app.core.logging import get_logger
from app.repositories.uploads import UploadRecord
from app.schemas.uploads import UploadStatus
from app.services.local_storage import LocalObjectStorage
from app.services.object_references import ObjectReference, ObjectReferenceService
from app.services.offload import run_blocking
from app.services.storage import SignedUpload, StorageSigner
from app.services.storage_quota import StorageQuotaService
from app.services.upload_validation import validate_upload_preflight

logger = get_logger(__name__)


class UploadSessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETING = "completing"
    COMPLETED = "completed"
    ABORTED = "aborted"
    EXPIRED = "expired"
    FAILED = "failed"


class UploadPartStatus(StrEnum):
    PENDING = "pending"
    UPLOADED = "uploaded"


@dataclass
class UploadSessionPart:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    session_id: UUID
    part_number: int
    expected_size_bytes: int
    object_path: str
    status: UploadPartStatus = UploadPartStatus.PENDING
    verified_size_bytes: int | None = None
    etag: str | None = None
    uploaded_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class UploadSession:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    uploaded_video_id: UUID
    final_storage_bucket: str
    final_storage_path: str
    original_filename: str
    mime_type: str
    declared_size_bytes: int
    expected_sha256: str | None
    workflow_version: str
    part_size_bytes: int
    part_count: int
    quota_reservation_id: UUID
    expires_at: datetime
    parts: list[UploadSessionPart]
    warnings: list[str] = field(default_factory=list)
    status: UploadSessionStatus = UploadSessionStatus.ACTIVE
    quota_committed_at: datetime | None = None
    verified_size_bytes: int | None = None
    verified_sha256: str | None = None
    source_reference_id: UUID | None = None
    completed_at: datetime | None = None
    aborted_at: datetime | None = None
    failed_at: datetime | None = None
    failure_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def completed_parts(self) -> int:
        return sum(part.status == UploadPartStatus.UPLOADED for part in self.parts)

    @property
    def uploaded_bytes(self) -> int:
        return sum(
            part.verified_size_bytes or 0
            for part in self.parts
            if part.status == UploadPartStatus.UPLOADED
        )


@dataclass(frozen=True)
class PartConfirmation:
    session_id: UUID
    part_number: int
    verified_size_bytes: int
    completed_parts: int
    uploaded_bytes: int


@dataclass(frozen=True)
class UploadCompletion:
    session: UploadSession
    upload: UploadRecord
    reference: ObjectReference


class UploadSessionService:
    """Coordinate multipart uploads without trusting client-reported object state."""

    PART_SIZE_BYTES = 10 * 1024 * 1024
    MAX_PART_URLS = 5
    PART_URL_TTL_SECONDS = 900
    SESSION_TTL = timedelta(hours=6)
    _COPY_CHUNK_BYTES = 1024 * 1024

    def __init__(
        self,
        *,
        storage_signer: StorageSigner,
        upload_repository: Any,
        object_storage: LocalObjectStorage,
        quota_service: StorageQuotaService,
        object_reference_service: ObjectReferenceService,
        db_client: Any | None = None,
    ) -> None:
        self._storage_signer = storage_signer
        self._upload_repository = upload_repository
        self._object_storage = object_storage
        self._quota_service = quota_service
        self._object_reference_service = object_reference_service
        self._db_client = db_client
        self._sessions: dict[UUID, UploadSession] = {}
        self._lock = asyncio.Lock()

    async def initiate(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        filename: str,
        mime_type: str,
        file_size_bytes: int,
        storage_bucket: str,
        max_file_size_bytes: int,
        max_active_uploads: int,
        sha256_hash: str | None = None,
    ) -> UploadSession:
        workspace_id = UUID(str(workspace_id))
        project_id = UUID(str(project_id))
        filename = filename.strip()
        mime_type = mime_type.strip().lower()
        self._validate_hash(sha256_hash)
        if file_size_bytes <= 0:
            raise ApiError(422, "upload_size_invalid", "Upload size must be positive.")

        validation = validate_upload_preflight(
            filename=filename,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            max_file_size_bytes=max_file_size_bytes,
        )
        upload = await self._repository_call(
            "initialize_upload_async",
            "initialize_upload",
            project_id=project_id,
            workspace_id=workspace_id,
            storage_bucket=storage_bucket,
            storage_extension=validation.extension,
            original_filename=filename,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            max_active_uploads=max_active_uploads,
        )
        self._validate_storage_path(
            upload.storage_path,
            workspace_id=workspace_id,
            project_id=project_id,
        )

        now = datetime.now(UTC)
        session_id = uuid4()
        reservation_id = uuid4()
        expires_at = now + self.SESSION_TTL
        reservation = await self._quota_service.reserve(
            reservation_id=reservation_id,
            workspace_id=workspace_id,
            byte_amount=file_size_bytes,
            object_amount=1,
            expires_at=expires_at,
        )
        if not reservation.allowed:
            await self._mark_upload_failed_best_effort(
                upload,
                code="storage_quota_exceeded",
                message=reservation.reason or "Storage quota would be exceeded.",
            )
            raise ApiError(
                409,
                "storage_quota_exceeded",
                reservation.reason or "Storage quota would be exceeded.",
            )

        upload = await self._repository_call(
            "mark_workflow_owned_async",
            "mark_workflow_owned",
            upload=upload,
            workflow_version="first-draft-v2",
        )

        part_count = (file_size_bytes + self.PART_SIZE_BYTES - 1) // self.PART_SIZE_BYTES
        parts = [
            UploadSessionPart(
                id=uuid4(),
                workspace_id=workspace_id,
                project_id=project_id,
                session_id=session_id,
                part_number=part_number,
                expected_size_bytes=min(
                    self.PART_SIZE_BYTES,
                    file_size_bytes - (part_number - 1) * self.PART_SIZE_BYTES,
                ),
                object_path=(
                    f"{upload.storage_path}.parts/{session_id}/{part_number}"
                ),
                created_at=now,
                updated_at=now,
            )
            for part_number in range(1, part_count + 1)
        ]
        session = UploadSession(
            id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=upload.id,
            final_storage_bucket=upload.storage_bucket,
            final_storage_path=upload.storage_path,
            original_filename=filename,
            mime_type=mime_type,
            declared_size_bytes=file_size_bytes,
            expected_sha256=sha256_hash,
            workflow_version="first-draft-v2",
            part_size_bytes=self.PART_SIZE_BYTES,
            part_count=part_count,
            quota_reservation_id=reservation_id,
            expires_at=expires_at,
            parts=parts,
            warnings=validation.warnings,
            created_at=now,
            updated_at=now,
        )
        try:
            await self._save_new_session(session)
        except Exception as exc:
            await self._quota_service.release_reservation(reservation_id)
            await self._mark_upload_failed_best_effort(
                upload,
                code="upload_session_unavailable",
                message="Multipart upload state could not be created.",
            )
            if isinstance(exc, ApiError):
                raise
            raise ApiError(
                503,
                "upload_session_unavailable",
                "Multipart upload state could not be created.",
            ) from exc
        return session

    async def get(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> UploadSession:
        session_id = UUID(str(session_id))
        workspace_id = UUID(str(workspace_id))
        project_id = UUID(str(project_id))
        session = await self._load_session(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if (
            session.status in {
                UploadSessionStatus.ACTIVE,
                UploadSessionStatus.COMPLETING,
            }
            and session.quota_committed_at is None
            and session.expires_at <= datetime.now(UTC)
        ):
            await self._expire(session)
        elif self._db_client is None and session.status in {
            UploadSessionStatus.ABORTED,
            UploadSessionStatus.EXPIRED,
            UploadSessionStatus.FAILED,
        }:
            messages = {
                UploadSessionStatus.ABORTED: "Upload was aborted before completion.",
                UploadSessionStatus.EXPIRED: "Multipart upload session expired.",
                UploadSessionStatus.FAILED: "Multipart upload failed.",
            }
            await self._terminate_session(
                session,
                status=session.status,
                code=session.failure_code or f"upload_session_{session.status.value}",
                message=messages[session.status],
                delete_final=session.status == UploadSessionStatus.FAILED,
            )
        return session

    async def resume(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> UploadSession:
        session = await self.get(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if session.status != UploadSessionStatus.ACTIVE:
            return session
        return await self._reconcile_pending_parts(session)

    async def create_pending_part_urls(
        self,
        session: UploadSession,
    ) -> list[tuple[int, SignedUpload]]:
        current = await self.resume(
            session_id=session.id,
            workspace_id=session.workspace_id,
            project_id=session.project_id,
        )
        if current.status != UploadSessionStatus.ACTIVE:
            return []
        pending = sorted(
            (part for part in current.parts if part.status == UploadPartStatus.PENDING),
            key=lambda part: part.part_number,
        )[: self.MAX_PART_URLS]
        signed_parts: list[tuple[int, SignedUpload]] = []
        for part in pending:
            signed = await run_blocking(
                self._storage_signer.create_signed_upload,
                bucket=current.final_storage_bucket,
                path=part.object_path,
                mime_type=current.mime_type,
                expires_in_seconds=self.PART_URL_TTL_SECONDS,
            )
            signed_parts.append((part.part_number, signed))
        return signed_parts

    async def _reconcile_pending_parts(self, session: UploadSession) -> UploadSession:
        """Rebuild pending-part state from storage before issuing replacement URLs."""
        for part in sorted(session.parts, key=lambda item: item.part_number):
            if part.status != UploadPartStatus.PENDING:
                continue
            try:
                metadata = await run_blocking(
                    self._object_storage.inspect_object,
                    bucket=session.final_storage_bucket,
                    path=part.object_path,
                    compute_sha256=False,
                )
            except ApiError as exc:
                if exc.code == "storage_object_missing":
                    continue
                raise

            if metadata.size_bytes != part.expected_size_bytes:
                # Signed part uploads use x-upsert=false. Remove a partial or corrupt
                # object so the replacement URL can be used successfully.
                await self._delete_object_best_effort(
                    session.final_storage_bucket,
                    part.object_path,
                )
                continue

            await self._mark_part_uploaded(
                session=session,
                part=part,
                verified_size_bytes=metadata.size_bytes,
                etag=metadata.etag,
            )

        # Counters are properties of authoritative part rows. Always reload after
        # reconciliation so concurrent confirmations are represented as well.
        return await self._load_session(
            session_id=session.id,
            workspace_id=session.workspace_id,
            project_id=session.project_id,
        )

    async def confirm_part(
        self,
        *,
        session_id: UUID,
        part_number: int,
        workspace_id: UUID,
        project_id: UUID,
        etag: str | None = None,
    ) -> PartConfirmation:
        session = await self.get(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        self._require_active(session)
        part = next(
            (candidate for candidate in session.parts if candidate.part_number == part_number),
            None,
        )
        if part is None:
            raise ApiError(404, "upload_part_not_found", "Upload part was not found.")
        if part.status == UploadPartStatus.UPLOADED:
            return self._confirmation(session, part)

        metadata = await run_blocking(
            self._object_storage.inspect_object,
            bucket=session.final_storage_bucket,
            path=part.object_path,
            compute_sha256=False,
        )
        if metadata.size_bytes != part.expected_size_bytes:
            raise ApiError(
                409,
                "upload_part_size_mismatch",
                "Stored upload part size did not match the expected size.",
                details={
                    "part_number": part.part_number,
                    "expected_size_bytes": part.expected_size_bytes,
                    "observed_size_bytes": metadata.size_bytes,
                },
            )

        # A client ETag is not authoritative. Persist only an ETag observed by storage.
        _ = etag
        updated = await self._mark_part_uploaded(
            session=session,
            part=part,
            verified_size_bytes=metadata.size_bytes,
            etag=metadata.etag,
        )
        if not updated:
            session = await self._load_session(
                session_id=session.id,
                workspace_id=session.workspace_id,
                project_id=session.project_id,
            )
            part = next(
                (candidate for candidate in session.parts if candidate.part_number == part_number),
                None,
            )
            if part is None or part.status != UploadPartStatus.UPLOADED:
                raise ApiError(
                    409,
                    "upload_part_state_conflict",
                    "Upload part state changed before it could be confirmed.",
                )
        return self._confirmation(session, part)

    async def complete(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        sha256_hash: str | None = None,
    ) -> UploadCompletion:
        self._validate_hash(sha256_hash)
        session = await self.get(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if session.status == UploadSessionStatus.COMPLETED:
            return await self._completed_result(session)
        if session.status not in {
            UploadSessionStatus.ACTIVE,
            UploadSessionStatus.COMPLETING,
        }:
            self._require_active(session)
        if (
            session.completed_parts != session.part_count
            or any(
                part.status != UploadPartStatus.UPLOADED
                or part.verified_size_bytes != part.expected_size_bytes
                for part in session.parts
            )
        ):
            raise ApiError(
                409,
                "upload_parts_incomplete",
                "Confirm every upload part before completing the upload.",
            )

        if session.status == UploadSessionStatus.ACTIVE:
            transitioned = await self._transition_session(
                session,
                expected=UploadSessionStatus.ACTIVE,
                status=UploadSessionStatus.COMPLETING,
            )
            if not transitioned:
                session = await self._load_session(
                    session_id=session.id,
                    workspace_id=session.workspace_id,
                    project_id=session.project_id,
                )
                if session.status == UploadSessionStatus.COMPLETED:
                    return await self._completed_result(session)
                raise ApiError(
                    409,
                    "upload_completion_in_progress",
                    "Upload completion is already in progress.",
                )

        temporary_path: Path | None = None
        quota_committed = session.quota_committed_at is not None
        quota_commit_attempted = quota_committed
        reference: ObjectReference | None = None
        try:
            temporary_path, assembled_size, assembled_hash = await run_blocking(
                self._assemble_parts,
                session,
            )
            self._verify_assembled_upload(
                session=session,
                observed_size=assembled_size,
                observed_hash=assembled_hash,
                requested_hash=sha256_hash,
            )
            await run_blocking(
                self._object_storage.upload_file,
                bucket=session.final_storage_bucket,
                path=session.final_storage_path,
                source_path=temporary_path,
                content_type=session.mime_type,
            )
            final_metadata = await run_blocking(
                self._object_storage.inspect_object,
                bucket=session.final_storage_bucket,
                path=session.final_storage_path,
                compute_sha256=True,
            )
            if (
                final_metadata.size_bytes != assembled_size
                or final_metadata.sha256 != assembled_hash
            ):
                raise ApiError(
                    502,
                    "upload_final_verification_failed",
                    "The assembled upload could not be verified in storage.",
                )

            reference = await self._object_reference_service.create_reference(
                workspace_id=session.workspace_id,
                project_id=session.project_id,
                storage_bucket=session.final_storage_bucket,
                storage_path=session.final_storage_path,
                owner_type="uploaded_video",
                owner_id=session.uploaded_video_id,
                object_type="source_video",
                mime_type=session.mime_type,
                size_bytes=assembled_size,
                sha256=assembled_hash,
            )
            if self._db_client is not None:
                finalized = await self._finalize_upload_session(
                    session,
                    reference=reference,
                    verified_size_bytes=assembled_size,
                    verified_sha256=assembled_hash,
                )
                if not finalized:
                    await self._object_reference_service.mark_deleted(
                        workspace_id=session.workspace_id,
                        project_id=session.project_id,
                        reference_id=reference.id,
                    )
                    await self._delete_object_best_effort(
                        session.final_storage_bucket,
                        session.final_storage_path,
                    )
                    raise ApiError(
                        409,
                        "upload_session_state_conflict",
                        "Upload completion lost a race with terminal cleanup.",
                    )
                session = await self._load_session(
                    session_id=session.id,
                    workspace_id=session.workspace_id,
                    project_id=session.project_id,
                )
                upload = await self._get_upload(session)
                await self._delete_parts_best_effort(session)
                return UploadCompletion(
                    session=session,
                    upload=upload,
                    reference=reference,
                )

            upload = await self._get_upload(session)
            upload = await self._repository_call(
                "record_verified_source_async",
                "record_verified_source",
                upload=upload,
                source_object_reference_id=reference.id,
                verified_size_bytes=assembled_size,
                verified_sha256=assembled_hash,
            )
            if not quota_committed:
                quota_commit_attempted = True
                quota_committed = await self._commit_session_quota(session)
            if not quota_committed:
                raise ApiError(
                    409,
                    "quota_reservation_invalid",
                    "Upload quota reservation could not be committed.",
                )
            upload = await self._repository_call(
                "complete_upload_for_processing_async",
                "complete_upload_for_processing",
                project_id=session.project_id,
                video_id=session.uploaded_video_id,
                workspace_id=session.workspace_id,
                observed_file_size_bytes=assembled_size,
            )
            completed_at = datetime.now(UTC)
            transitioned = await self._transition_session(
                session,
                expected=UploadSessionStatus.COMPLETING,
                status=UploadSessionStatus.COMPLETED,
                verified_size_bytes=assembled_size,
                verified_sha256=assembled_hash,
                source_reference_id=reference.id,
                completed_at=completed_at,
                failure_code=None,
            )
            if not transitioned:
                current = await self._load_session(
                    session_id=session.id,
                    workspace_id=session.workspace_id,
                    project_id=session.project_id,
                )
                if current.status != UploadSessionStatus.COMPLETED:
                    raise ApiError(
                        409,
                        "upload_session_state_conflict",
                        "Upload completion could not be recorded safely.",
                    )
                session = current
            await self._delete_parts_best_effort(session)
            return UploadCompletion(session=session, upload=upload, reference=reference)
        except Exception as exc:
            if not quota_commit_attempted:
                await self._fail_session(
                    session,
                    code=exc.code if isinstance(exc, ApiError) else "upload_completion_failed",
                    message=(
                        exc.message
                        if isinstance(exc, ApiError)
                        else "Multipart upload completion failed."
                    ),
                    reference=reference,
                    delete_final=True,
                )
            if isinstance(exc, ApiError):
                raise
            logger.exception(
                "multipart_upload_completion_failed",
                extra={"session_id": str(session.id)},
            )
            raise ApiError(
                503,
                "upload_completion_failed",
                "Multipart upload completion failed.",
            ) from exc
        finally:
            if temporary_path is not None:
                await run_blocking(temporary_path.unlink, missing_ok=True)

    async def abort(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> UploadSession:
        session = await self.get(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if session.status == UploadSessionStatus.COMPLETED:
            raise ApiError(
                409,
                "upload_session_not_abortable",
                "A completed upload cannot be aborted.",
            )
        if session.status == UploadSessionStatus.COMPLETING:
            raise ApiError(
                409,
                "upload_completion_in_progress",
                "Upload completion is already in progress.",
            )
        if session.status == UploadSessionStatus.EXPIRED:
            return await self._terminate_session(
                session,
                status=UploadSessionStatus.EXPIRED,
                code=session.failure_code or "upload_session_expired",
                message="Multipart upload session expired.",
            )
        if session.status == UploadSessionStatus.FAILED:
            return await self._terminate_session(
                session,
                status=UploadSessionStatus.FAILED,
                code=session.failure_code or "upload_completion_failed",
                message="Multipart upload failed.",
            )
        return await self._terminate_session(
            session,
            status=UploadSessionStatus.ABORTED,
            code="upload_aborted",
            message="Upload was aborted before completion.",
        )

    async def _completed_result(self, session: UploadSession) -> UploadCompletion:
        if session.verified_size_bytes is None or session.verified_sha256 is None:
            raise ApiError(
                503,
                "upload_session_invalid",
                "Completed upload verification state is incomplete.",
            )
        reference = await self._object_reference_service.create_reference(
            workspace_id=session.workspace_id,
            project_id=session.project_id,
            storage_bucket=session.final_storage_bucket,
            storage_path=session.final_storage_path,
            owner_type="uploaded_video",
            owner_id=session.uploaded_video_id,
            object_type="source_video",
            mime_type=session.mime_type,
            size_bytes=session.verified_size_bytes,
            sha256=session.verified_sha256,
        )
        session.source_reference_id = reference.id
        upload = await self._get_upload(session)
        upload = await self._repository_call(
            "record_verified_source_async",
            "record_verified_source",
            upload=upload,
            source_object_reference_id=reference.id,
            verified_size_bytes=session.verified_size_bytes,
            verified_sha256=session.verified_sha256,
        )
        upload = await self._repository_call(
            "complete_upload_for_processing_async",
            "complete_upload_for_processing",
            project_id=session.project_id,
            video_id=session.uploaded_video_id,
            workspace_id=session.workspace_id,
            observed_file_size_bytes=session.verified_size_bytes,
        )
        await self._delete_parts_best_effort(session)
        return UploadCompletion(session=session, upload=upload, reference=reference)

    async def _expire(self, session: UploadSession) -> None:
        await self._terminate_session(
            session,
            status=UploadSessionStatus.EXPIRED,
            code="upload_session_expired",
            message="Multipart upload session expired.",
            delete_final=True,
        )

    async def _fail_session(
        self,
        session: UploadSession,
        *,
        code: str,
        message: str,
        reference: ObjectReference | None = None,
        delete_final: bool = False,
    ) -> None:
        await self._terminate_session(
            session,
            status=UploadSessionStatus.FAILED,
            code=code,
            message=message,
            reference=reference,
            delete_final=delete_final,
        )

    async def _terminate_session(
        self,
        session: UploadSession,
        *,
        status: UploadSessionStatus,
        code: str,
        message: str,
        reference: ObjectReference | None = None,
        delete_final: bool = False,
    ) -> UploadSession:
        if status not in {
            UploadSessionStatus.ABORTED,
            UploadSessionStatus.EXPIRED,
            UploadSessionStatus.FAILED,
        }:
            raise ValueError("terminal cleanup requires an abort, expiry, or failure status")
        if session.quota_committed_at is not None:
            raise ApiError(
                409,
                "upload_completion_in_progress",
                "Committed upload finalization must be retried instead of terminated.",
            )

        if self._db_client is not None:
            response = await self._db_client.async_request(
                "POST",
                "/rpc/motionly_terminate_upload_session",
                json={
                    "p_session_id": str(session.id),
                    "p_workspace_id": str(session.workspace_id),
                    "p_project_id": str(session.project_id),
                    "p_status": status.value,
                    "p_failure_code": code,
                    "p_error_message": message,
                    "p_delete_final": delete_final,
                },
            )
            self._raise_db_error(response)
            return await self._load_session(
                session_id=session.id,
                workspace_id=session.workspace_id,
                project_id=session.project_id,
            )

        if session.status in {
            UploadSessionStatus.ACTIVE,
            UploadSessionStatus.COMPLETING,
        }:
            transitioned = await self._transition_session(
                session,
                expected=session.status,
                status=status,
                aborted_at=datetime.now(UTC)
                if status == UploadSessionStatus.ABORTED
                else None,
                failed_at=datetime.now(UTC)
                if status in {UploadSessionStatus.EXPIRED, UploadSessionStatus.FAILED}
                else None,
                failure_code=code,
            )
            if not transitioned:
                session = await self._load_session(
                    session_id=session.id,
                    workspace_id=session.workspace_id,
                    project_id=session.project_id,
                )
        released = await self._quota_service.release_reservation(
            session.quota_reservation_id
        )
        if not released:
            reservation_state = await self._quota_service.get_reservation_state(
                session.quota_reservation_id
            )
            if reservation_state not in {None, "released"}:
                raise ApiError(
                    503,
                    "upload_cleanup_incomplete",
                    "Upload quota cleanup must be retried.",
                )
        await self._delete_parts(session)
        if delete_final:
            await run_blocking(
                self._object_storage.delete_object,
                bucket=session.final_storage_bucket,
                path=session.final_storage_path,
            )
        if reference is not None:
            await self._object_reference_service.mark_deleted(
                workspace_id=session.workspace_id,
                project_id=session.project_id,
                reference_id=reference.id,
            )
        upload = await self._get_upload(session)
        await self._mark_upload_failed(upload, code=code, message=message)
        return session

    def _assemble_parts(self, session: UploadSession) -> tuple[Path, int, str]:
        descriptor, temporary_name = tempfile.mkstemp(prefix="motionly-upload-", suffix=".part")
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        hasher = hashlib.sha256()
        total_size = 0
        try:
            with temporary_path.open("wb") as destination:
                for part in sorted(session.parts, key=lambda item: item.part_number):
                    source_path = self._object_storage.ensure_object(
                        bucket=session.final_storage_bucket,
                        path=part.object_path,
                    )
                    part_size = 0
                    with source_path.open("rb") as source:
                        while chunk := source.read(self._COPY_CHUNK_BYTES):
                            destination.write(chunk)
                            hasher.update(chunk)
                            chunk_size = len(chunk)
                            part_size += chunk_size
                            total_size += chunk_size
                    if part_size != part.expected_size_bytes:
                        raise ApiError(
                            409,
                            "upload_part_size_mismatch",
                            "Stored upload part size changed before assembly.",
                        )
            return temporary_path, total_size, hasher.hexdigest()
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _verify_assembled_upload(
        *,
        session: UploadSession,
        observed_size: int,
        observed_hash: str,
        requested_hash: str | None,
    ) -> None:
        if observed_size != session.declared_size_bytes:
            raise ApiError(
                409,
                "upload_size_mismatch",
                "Assembled upload size did not match the declared size.",
            )
        expected_hashes = {
            value for value in (session.expected_sha256, requested_hash) if value is not None
        }
        if any(expected != observed_hash for expected in expected_hashes):
            raise ApiError(
                409,
                "upload_hash_mismatch",
                "Assembled upload SHA256 did not match the expected hash.",
            )

    async def _save_new_session(self, session: UploadSession) -> None:
        if self._db_client is None:
            async with self._lock:
                if session.id in self._sessions:
                    raise ApiError(
                        409,
                        "upload_session_conflict",
                        "Multipart upload session already exists.",
                    )
                self._sessions[session.id] = session
            return
        session_response = await self._db_client.async_request(
            "POST",
            "/upload_sessions",
            params={"select": "*"},
            json=self._session_row(session),
        )
        self._raise_db_error(session_response)
        parts_response = await self._db_client.async_request(
            "POST",
            "/upload_session_parts",
            params={"select": "*"},
            json=[self._part_row(part) for part in session.parts],
        )
        self._raise_db_error(parts_response)

    async def _load_session(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> UploadSession:
        if self._db_client is None:
            session = self._sessions.get(session_id)
            if session is None:
                raise ApiError(
                    404,
                    "upload_session_not_found",
                    "Multipart upload session was not found.",
                )
            if session.workspace_id != workspace_id:
                raise ApiError(
                    403,
                    "workspace_access_denied",
                    "You do not have access to this upload session.",
                )
            if session.project_id != project_id:
                raise ApiError(
                    404,
                    "upload_session_not_found",
                    "Multipart upload session was not found.",
                )
            return session

        response = await self._db_client.async_request(
            "GET",
            "/upload_sessions",
            params={
                "id": f"eq.{session_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "limit": "1",
            },
        )
        self._raise_db_error(response)
        rows = response.json()
        if not rows:
            raise ApiError(
                404,
                "upload_session_not_found",
                "Multipart upload session was not found.",
            )
        parts_response = await self._db_client.async_request(
            "GET",
            "/upload_session_parts",
            params={
                "session_id": f"eq.{session_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "part_number.asc",
            },
        )
        self._raise_db_error(parts_response)
        try:
            session = self._session_from_rows(rows[0], parts_response.json())
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError(
                503,
                "upload_session_invalid",
                "Multipart upload state was invalid.",
            ) from exc
        upload = await self._get_upload(session)
        session.source_reference_id = upload.source_object_reference_id
        return session

    async def _mark_part_uploaded(
        self,
        *,
        session: UploadSession,
        part: UploadSessionPart,
        verified_size_bytes: int,
        etag: str | None,
    ) -> bool:
        now = datetime.now(UTC)
        if self._db_client is None:
            async with self._lock:
                if part.status == UploadPartStatus.UPLOADED:
                    return True
                if session.status != UploadSessionStatus.ACTIVE:
                    return False
                part.status = UploadPartStatus.UPLOADED
                part.verified_size_bytes = verified_size_bytes
                part.etag = etag
                part.uploaded_at = now
                part.updated_at = now
                session.updated_at = now
                return True
        response = await self._db_client.async_request(
            "PATCH",
            "/upload_session_parts",
            params={
                "session_id": f"eq.{session.id}",
                "workspace_id": f"eq.{session.workspace_id}",
                "project_id": f"eq.{session.project_id}",
                "part_number": f"eq.{part.part_number}",
                "status": f"eq.{UploadPartStatus.PENDING.value}",
                "select": "*",
            },
            json={
                "status": UploadPartStatus.UPLOADED.value,
                "verified_size_bytes": verified_size_bytes,
                "etag": etag,
                "uploaded_at": now.isoformat(),
            },
        )
        self._raise_db_error(response)
        if not response.json():
            return False
        part.status = UploadPartStatus.UPLOADED
        part.verified_size_bytes = verified_size_bytes
        part.etag = etag
        part.uploaded_at = now
        part.updated_at = now
        return True

    async def _transition_session(
        self,
        session: UploadSession,
        *,
        expected: UploadSessionStatus,
        status: UploadSessionStatus,
        **values: Any,
    ) -> bool:
        now = datetime.now(UTC)
        if self._db_client is None:
            async with self._lock:
                stored = self._sessions.get(session.id)
                if stored is None or stored.status != expected:
                    return False
                stored.status = status
                stored.updated_at = now
                for name, value in values.items():
                    setattr(stored, name, value)
                return True
        payload = {
            "status": status.value,
            **{
                name: value.isoformat() if isinstance(value, datetime) else value
                for name, value in values.items()
                if name != "source_reference_id"
            },
        }
        response = await self._db_client.async_request(
            "PATCH",
            "/upload_sessions",
            params={
                "id": f"eq.{session.id}",
                "workspace_id": f"eq.{session.workspace_id}",
                "project_id": f"eq.{session.project_id}",
                "status": f"eq.{expected.value}",
                "select": "*",
            },
            json=payload,
        )
        self._raise_db_error(response)
        if not response.json():
            return False
        session.status = status
        session.updated_at = now
        for name, value in values.items():
            setattr(session, name, value)
        return True

    async def _finalize_upload_session(
        self,
        session: UploadSession,
        *,
        reference: ObjectReference,
        verified_size_bytes: int,
        verified_sha256: str,
    ) -> bool:
        if self._db_client is None:
            raise RuntimeError("atomic upload finalization requires a database client")
        response = await self._db_client.async_request(
            "POST",
            "/rpc/motionly_finalize_upload_session",
            json={
                "p_session_id": str(session.id),
                "p_workspace_id": str(session.workspace_id),
                "p_project_id": str(session.project_id),
                "p_reference_id": str(reference.id),
                "p_verified_size_bytes": verified_size_bytes,
                "p_verified_sha256": verified_sha256,
            },
        )
        self._raise_db_error(response)
        return response.json() is True

    async def _commit_session_quota(self, session: UploadSession) -> bool:
        if session.quota_committed_at is not None:
            return True
        if self._db_client is None:
            committed = await self._quota_service.commit(session.quota_reservation_id)
            if committed:
                session.quota_committed_at = datetime.now(UTC)
                session.updated_at = session.quota_committed_at
            return committed

        response = await self._db_client.async_request(
            "POST",
            "/rpc/motionly_commit_upload_session_quota",
            json={
                "p_session_id": str(session.id),
                "p_workspace_id": str(session.workspace_id),
                "p_project_id": str(session.project_id),
            },
        )
        self._raise_db_error(response)
        committed = response.json() is True
        if committed:
            session.quota_committed_at = datetime.now(UTC)
            session.updated_at = session.quota_committed_at
        return committed

    async def _get_upload(self, session: UploadSession) -> UploadRecord:
        return await self._repository_call(
            "get_upload_for_project_async",
            "get_upload_for_project",
            project_id=session.project_id,
            video_id=session.uploaded_video_id,
            workspace_id=session.workspace_id,
        )

    async def _repository_call(
        self,
        async_name: str,
        sync_name: str,
        **kwargs: Any,
    ) -> Any:
        async_method = getattr(self._upload_repository, async_name, None)
        if async_method is not None:
            return await async_method(**kwargs)
        return await run_blocking(getattr(self._upload_repository, sync_name), **kwargs)

    async def _mark_upload_failed(
        self,
        upload: UploadRecord,
        *,
        code: str,
        message: str,
    ) -> None:
        if upload.status == UploadStatus.FAILED:
            return
        await self._repository_call(
            "mark_failed_async",
            "mark_failed",
            upload=upload,
            error_code=code,
            error_message=message,
        )

    async def _mark_upload_failed_best_effort(
        self,
        upload: UploadRecord,
        *,
        code: str,
        message: str,
    ) -> None:
        try:
            await self._mark_upload_failed(
                upload,
                code=code,
                message=message,
            )
        except Exception:
            logger.warning(
                "multipart_upload_mark_failed_failed",
                extra={"video_id": str(upload.id), "failure_code": code},
            )

    async def _delete_parts(self, session: UploadSession) -> None:
        for part in session.parts:
            await run_blocking(
                self._object_storage.delete_object,
                bucket=session.final_storage_bucket,
                path=part.object_path,
            )

    async def _delete_parts_best_effort(self, session: UploadSession) -> None:
        for part in session.parts:
            await self._delete_object_best_effort(
                session.final_storage_bucket,
                part.object_path,
            )

    async def _delete_object_best_effort(self, bucket: str, path: str) -> None:
        try:
            await run_blocking(
                self._object_storage.delete_object,
                bucket=bucket,
                path=path,
            )
        except Exception:
            logger.warning(
                "multipart_upload_object_cleanup_failed",
                extra={"bucket": bucket, "path": path},
            )

    @staticmethod
    def _confirmation(
        session: UploadSession,
        part: UploadSessionPart,
    ) -> PartConfirmation:
        if part.verified_size_bytes is None:
            raise ApiError(
                503,
                "upload_part_invalid",
                "Confirmed upload part was missing verification state.",
            )
        return PartConfirmation(
            session_id=session.id,
            part_number=part.part_number,
            verified_size_bytes=part.verified_size_bytes,
            completed_parts=session.completed_parts,
            uploaded_bytes=session.uploaded_bytes,
        )

    @staticmethod
    def _require_active(session: UploadSession) -> None:
        errors = {
            UploadSessionStatus.COMPLETING: (
                409,
                "upload_completion_in_progress",
                "Upload completion is already in progress.",
            ),
            UploadSessionStatus.COMPLETED: (
                409,
                "upload_session_completed",
                "Upload session is already complete.",
            ),
            UploadSessionStatus.ABORTED: (
                409,
                "upload_session_aborted",
                "Upload session was aborted.",
            ),
            UploadSessionStatus.EXPIRED: (
                410,
                "upload_session_expired",
                "Upload session expired.",
            ),
            UploadSessionStatus.FAILED: (
                409,
                "upload_session_failed",
                "Upload session failed.",
            ),
        }
        if session.status != UploadSessionStatus.ACTIVE:
            status_code, code, message = errors[session.status]
            raise ApiError(status_code, code, message)

    @staticmethod
    def _validate_hash(value: str | None) -> None:
        if value is not None and (
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ApiError(
                422,
                "upload_hash_invalid",
                "Upload SHA256 must be 64 lowercase hexadecimal characters.",
            )

    @staticmethod
    def _validate_storage_path(
        path: str,
        *,
        workspace_id: UUID,
        project_id: UUID,
    ) -> None:
        decoded = unquote(path)
        segments = path.split("/")
        expected_prefix = f"workspaces/{workspace_id}/projects/{project_id}/"
        if (
            decoded != path
            or not path.startswith(expected_prefix)
            or path.startswith("/")
            or "\\" in path
            or "" in segments
            or any(segment in {".", ".."} for segment in segments)
        ):
            raise ApiError(
                422,
                "storage_path_invalid",
                "Upload storage path is outside this project.",
            )

    @staticmethod
    def _raise_db_error(response: Any) -> None:
        if response.status_code < 400:
            return
        raise ApiError(
            503,
            "upload_session_unavailable",
            "Multipart upload state is temporarily unavailable.",
        )

    @staticmethod
    def _session_row(session: UploadSession) -> dict[str, Any]:
        return {
            "id": str(session.id),
            "workspace_id": str(session.workspace_id),
            "project_id": str(session.project_id),
            "uploaded_video_id": str(session.uploaded_video_id),
            "final_storage_bucket": session.final_storage_bucket,
            "final_storage_path": session.final_storage_path,
            "original_filename": session.original_filename,
            "mime_type": session.mime_type,
            "declared_size_bytes": session.declared_size_bytes,
            "expected_sha256": session.expected_sha256,
            "workflow_version": session.workflow_version,
            "verified_size_bytes": session.verified_size_bytes,
            "verified_sha256": session.verified_sha256,
            "part_size_bytes": session.part_size_bytes,
            "part_count": session.part_count,
            "status": session.status.value,
            "quota_reservation_id": str(session.quota_reservation_id),
            "quota_committed_at": (
                session.quota_committed_at.isoformat()
                if session.quota_committed_at
                else None
            ),
            "expires_at": session.expires_at.isoformat(),
            "completed_at": None,
            "aborted_at": None,
            "failed_at": None,
            "failure_code": None,
        }

    @staticmethod
    def _part_row(part: UploadSessionPart) -> dict[str, Any]:
        return {
            "id": str(part.id),
            "workspace_id": str(part.workspace_id),
            "project_id": str(part.project_id),
            "session_id": str(part.session_id),
            "part_number": part.part_number,
            "expected_size_bytes": part.expected_size_bytes,
            "object_path": part.object_path,
            "status": part.status.value,
            "verified_size_bytes": part.verified_size_bytes,
            "etag": part.etag,
            "uploaded_at": part.uploaded_at.isoformat() if part.uploaded_at else None,
        }

    @classmethod
    def _session_from_rows(
        cls,
        row: dict[str, Any],
        part_rows: list[dict[str, Any]],
    ) -> UploadSession:
        parts = [
            UploadSessionPart(
                id=UUID(str(part["id"])),
                workspace_id=UUID(str(part["workspace_id"])),
                project_id=UUID(str(part["project_id"])),
                session_id=UUID(str(part["session_id"])),
                part_number=int(part["part_number"]),
                expected_size_bytes=int(part["expected_size_bytes"]),
                object_path=str(part["object_path"]),
                status=UploadPartStatus(str(part["status"])),
                verified_size_bytes=(
                    int(part["verified_size_bytes"])
                    if part.get("verified_size_bytes") is not None
                    else None
                ),
                etag=str(part["etag"]) if part.get("etag") else None,
                uploaded_at=cls._parse_datetime(part.get("uploaded_at")),
                created_at=cls._parse_datetime(part.get("created_at"))
                or datetime.now(UTC),
                updated_at=cls._parse_datetime(part.get("updated_at"))
                or datetime.now(UTC),
            )
            for part in part_rows
        ]
        parts.sort(key=lambda item: item.part_number)
        session = UploadSession(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            uploaded_video_id=UUID(str(row["uploaded_video_id"])),
            final_storage_bucket=str(row["final_storage_bucket"]),
            final_storage_path=str(row["final_storage_path"]),
            original_filename=str(row["original_filename"]),
            mime_type=str(row["mime_type"]),
            declared_size_bytes=int(row["declared_size_bytes"]),
            expected_sha256=(
                str(row["expected_sha256"]) if row.get("expected_sha256") else None
            ),
            workflow_version=str(row.get("workflow_version") or "first-draft-v2"),
            verified_size_bytes=(
                int(row["verified_size_bytes"])
                if row.get("verified_size_bytes") is not None
                else None
            ),
            verified_sha256=(
                str(row["verified_sha256"]) if row.get("verified_sha256") else None
            ),
            part_size_bytes=int(row["part_size_bytes"]),
            part_count=int(row["part_count"]),
            status=UploadSessionStatus(str(row["status"])),
            quota_reservation_id=UUID(str(row["quota_reservation_id"])),
            quota_committed_at=cls._parse_datetime(row.get("quota_committed_at")),
            expires_at=cls._parse_datetime(row["expires_at"]) or datetime.now(UTC),
            completed_at=cls._parse_datetime(row.get("completed_at")),
            aborted_at=cls._parse_datetime(row.get("aborted_at")),
            failed_at=cls._parse_datetime(row.get("failed_at")),
            failure_code=(
                str(row["failure_code"]) if row.get("failure_code") else None
            ),
            parts=parts,
            created_at=cls._parse_datetime(row.get("created_at")) or datetime.now(UTC),
            updated_at=cls._parse_datetime(row.get("updated_at")) or datetime.now(UTC),
        )
        if (
            len(parts) != session.part_count
            or any(part.session_id != session.id for part in parts)
            or any(part.workspace_id != session.workspace_id for part in parts)
            or any(part.project_id != session.project_id for part in parts)
            or [part.part_number for part in parts]
            != list(range(1, session.part_count + 1))
        ):
            raise ValueError("upload session parts are incomplete or out of scope")
        return session

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("upload session timestamps must be timezone-aware")
        return parsed
