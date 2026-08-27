"""Durable, server-verified multipart sessions for non-source media.

This service intentionally mirrors the source ``UploadSessionService`` lifecycle
without creating or mutating an uploaded-video row.  Its only authority is the
server-created session: clients receive part URLs, but every part, final object,
size, and digest is verified by the service before quota is committed and a
Durable ObjectReference is exposed to a domain owner.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4

from app.api.errors import ApiError
from app.core.logging import get_logger
from app.services.local_storage import LocalObjectStorage
from app.services.object_references import ObjectReference, ObjectReferenceService
from app.services.offload import run_blocking
from app.services.storage import SignedUpload, StorageSigner
from app.services.storage_quota import StorageQuotaService
from app.services.upload_session import UploadPartStatus, UploadSessionStatus

logger = get_logger(__name__)


@dataclass
class GenericMediaUploadPart:
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
class GenericMediaUploadSession:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_type: str
    owner_id: UUID
    object_type: str
    final_storage_bucket: str
    final_storage_path: str
    original_filename: str
    mime_type: str
    declared_size_bytes: int
    expected_sha256: str | None
    part_size_bytes: int
    part_count: int
    quota_reservation_id: UUID
    expires_at: datetime
    parts: list[GenericMediaUploadPart]
    status: UploadSessionStatus = UploadSessionStatus.ACTIVE
    quota_committed_at: datetime | None = None
    verified_size_bytes: int | None = None
    verified_sha256: str | None = None
    object_reference_id: UUID | None = None
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
class GenericMediaPartConfirmation:
    session_id: UUID
    part_number: int
    verified_size_bytes: int
    completed_parts: int
    uploaded_bytes: int


@dataclass(frozen=True)
class GenericMediaUploadCompletion:
    session: GenericMediaUploadSession
    reference: ObjectReference


class GenericMediaUploadSessionService:
    """Persistent multipart state for B-roll, style references, and future media.

    ``db_client`` is a service-role Supabase REST client in production.  Tests and
    local development use the deliberately isolated in-memory session store.
    """

    PART_SIZE_BYTES = 10 * 1024 * 1024
    MAX_PART_URLS = 5
    PART_URL_TTL_SECONDS = 900
    SESSION_TTL = timedelta(hours=6)
    _COPY_CHUNK_BYTES = 1024 * 1024

    def __init__(
        self,
        *,
        storage_signer: StorageSigner,
        object_storage: LocalObjectStorage,
        quota_service: StorageQuotaService,
        object_reference_service: ObjectReferenceService,
        db_client: Any | None = None,
    ) -> None:
        self._storage_signer = storage_signer
        self._object_storage = object_storage
        self._quota_service = quota_service
        self._object_reference_service = object_reference_service
        self._db_client = db_client
        self._sessions: dict[UUID, GenericMediaUploadSession] = {}
        self._lock = asyncio.Lock()

    async def initiate(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        owner_type: str,
        owner_id: UUID,
        object_type: str,
        final_storage_bucket: str,
        final_storage_path: str,
        filename: str,
        mime_type: str,
        file_size_bytes: int,
        sha256_hash: str | None = None,
    ) -> GenericMediaUploadSession:
        workspace_id = UUID(str(workspace_id))
        project_id = UUID(str(project_id))
        owner_id = UUID(str(owner_id))
        filename = filename.strip()
        mime_type = mime_type.strip().lower()
        self._validate_hash(sha256_hash)
        self._validate_storage_path(
            final_storage_path,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if not filename or file_size_bytes <= 0:
            raise ApiError(422, "upload_size_invalid", "Upload size must be positive.")
        if not owner_type or not object_type or not mime_type:
            raise ApiError(422, "media_owner_invalid", "Media owner and type are required.")

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
            raise ApiError(
                409,
                "storage_quota_exceeded",
                reservation.reason or "Storage quota would be exceeded.",
            )
        part_count = (file_size_bytes + self.PART_SIZE_BYTES - 1) // self.PART_SIZE_BYTES
        parts = [
            GenericMediaUploadPart(
                id=uuid4(),
                workspace_id=workspace_id,
                project_id=project_id,
                session_id=session_id,
                part_number=number,
                expected_size_bytes=min(
                    self.PART_SIZE_BYTES,
                    file_size_bytes - (number - 1) * self.PART_SIZE_BYTES,
                ),
                object_path=f"{final_storage_path}.parts/{session_id}/{number}",
                created_at=now,
                updated_at=now,
            )
            for number in range(1, part_count + 1)
        ]
        session = GenericMediaUploadSession(
            id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            owner_type=owner_type,
            owner_id=owner_id,
            object_type=object_type,
            final_storage_bucket=final_storage_bucket,
            final_storage_path=final_storage_path,
            original_filename=filename,
            mime_type=mime_type,
            declared_size_bytes=file_size_bytes,
            expected_sha256=sha256_hash,
            part_size_bytes=self.PART_SIZE_BYTES,
            part_count=part_count,
            quota_reservation_id=reservation_id,
            expires_at=expires_at,
            parts=parts,
            created_at=now,
            updated_at=now,
        )
        try:
            await self._save_new_session(session)
        except Exception as exc:
            await self._quota_service.release_reservation(reservation_id)
            if isinstance(exc, ApiError):
                raise
            raise ApiError(
                503,
                "media_upload_session_unavailable",
                "Multipart media state could not be created.",
            ) from exc
        return session

    async def get(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> GenericMediaUploadSession:
        session = await self._load_session(
            session_id=UUID(str(session_id)),
            workspace_id=UUID(str(workspace_id)),
            project_id=UUID(str(project_id)),
        )
        if (
            session.status in {UploadSessionStatus.ACTIVE, UploadSessionStatus.COMPLETING}
            and session.quota_committed_at is None
            and session.expires_at <= datetime.now(UTC)
        ):
            await self._terminate(
                session,
                status=UploadSessionStatus.EXPIRED,
                code="media_upload_session_expired",
                delete_final=True,
            )
            session = await self._load_session(
                session_id=session.id,
                workspace_id=session.workspace_id,
                project_id=session.project_id,
            )
        return session

    async def resume(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> GenericMediaUploadSession:
        session = await self.get(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if session.status != UploadSessionStatus.ACTIVE:
            return session
        for part in session.parts:
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
                await self._delete_object_best_effort(
                    session.final_storage_bucket, part.object_path
                )
                continue
            await self._mark_part_uploaded(
                session=session,
                part=part,
                verified_size_bytes=metadata.size_bytes,
                etag=metadata.etag,
            )
        return await self._load_session(
            session_id=session.id,
            workspace_id=session.workspace_id,
            project_id=session.project_id,
        )

    async def create_pending_part_urls(
        self, session: GenericMediaUploadSession
    ) -> list[tuple[int, SignedUpload]]:
        current = await self.resume(
            session_id=session.id,
            workspace_id=session.workspace_id,
            project_id=session.project_id,
        )
        if current.status != UploadSessionStatus.ACTIVE:
            return []
        urls = []
        for part in [item for item in current.parts if item.status == UploadPartStatus.PENDING][
            : self.MAX_PART_URLS
        ]:
            signed = await run_blocking(
                self._storage_signer.create_signed_upload,
                bucket=current.final_storage_bucket,
                path=part.object_path,
                mime_type=current.mime_type,
                expires_in_seconds=self.PART_URL_TTL_SECONDS,
            )
            urls.append((part.part_number, signed))
        return urls

    async def confirm_part(
        self,
        *,
        session_id: UUID,
        part_number: int,
        workspace_id: UUID,
        project_id: UUID,
        etag: str | None = None,
    ) -> GenericMediaPartConfirmation:
        session = await self.get(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        self._require_active(session)
        part = next((item for item in session.parts if item.part_number == part_number), None)
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
            )
        # Client ETags are an acknowledgement hint only; persist storage's value.
        del etag
        changed = await self._mark_part_uploaded(
            session=session,
            part=part,
            verified_size_bytes=metadata.size_bytes,
            etag=metadata.etag,
        )
        if not changed:
            session = await self._load_session(
                session_id=session.id,
                workspace_id=session.workspace_id,
                project_id=session.project_id,
            )
            part = next((item for item in session.parts if item.part_number == part_number), None)
            if part is None or part.status != UploadPartStatus.UPLOADED:
                raise ApiError(
                    409, "upload_part_state_conflict", "Part state changed during confirmation."
                )
        return self._confirmation(session, part)

    async def complete(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        sha256_hash: str | None = None,
    ) -> GenericMediaUploadCompletion:
        self._validate_hash(sha256_hash)
        # Reconciliation makes one-part compatibility completion safe when the
        # browser PUT succeeded but its confirmation request was lost or omitted.
        session = await self.resume(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if session.status == UploadSessionStatus.COMPLETED:
            return await self._completed_result(session)
        self._require_active_or_completing(session)
        if session.completed_parts != session.part_count or any(
            item.status != UploadPartStatus.UPLOADED
            or item.verified_size_bytes != item.expected_size_bytes
            for item in session.parts
        ):
            raise ApiError(409, "upload_parts_incomplete", "Confirm every upload part first.")
        if session.status == UploadSessionStatus.ACTIVE:
            if not await self._transition(
                session,
                expected=UploadSessionStatus.ACTIVE,
                status=UploadSessionStatus.COMPLETING,
            ):
                current = await self._load_session(
                    session_id=session.id,
                    workspace_id=session.workspace_id,
                    project_id=session.project_id,
                )
                if current.status == UploadSessionStatus.COMPLETED:
                    return await self._completed_result(current)
                raise ApiError(
                    409, "upload_completion_in_progress", "Upload completion is in progress."
                )

        temporary_path: Path | None = None
        reference: ObjectReference | None = None
        quota_committed = session.quota_committed_at is not None
        try:
            temporary_path, observed_size, observed_hash = await run_blocking(
                self._assemble_parts, session
            )
            self._verify(session, observed_size, observed_hash, sha256_hash)
            await run_blocking(
                self._object_storage.upload_file,
                bucket=session.final_storage_bucket,
                path=session.final_storage_path,
                source_path=temporary_path,
                content_type=session.mime_type,
            )
            final = await run_blocking(
                self._object_storage.inspect_object,
                bucket=session.final_storage_bucket,
                path=session.final_storage_path,
                compute_sha256=True,
            )
            if final.size_bytes != observed_size or final.sha256 != observed_hash:
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
                owner_type=session.owner_type,
                owner_id=session.owner_id,
                object_type=session.object_type,
                mime_type=session.mime_type,
                size_bytes=observed_size,
                sha256=observed_hash,
            )
            if self._db_client is not None:
                finalized = await self._finalize(session, reference, observed_size, observed_hash)
                if not finalized:
                    await self._object_reference_service.mark_deleted(
                        workspace_id=session.workspace_id,
                        project_id=session.project_id,
                        reference_id=reference.id,
                    )
                    await self._delete_object_best_effort(
                        session.final_storage_bucket, session.final_storage_path
                    )
                    raise ApiError(409, "upload_session_state_conflict", "Completion lost a race.")
                session = await self._load_session(
                    session_id=session.id,
                    workspace_id=session.workspace_id,
                    project_id=session.project_id,
                )
            else:
                if not quota_committed:
                    quota_committed = await self._quota_service.commit(session.quota_reservation_id)
                if not quota_committed:
                    raise ApiError(
                        409, "quota_reservation_invalid", "Quota could not be committed."
                    )
                if not await self._transition(
                    session,
                    expected=UploadSessionStatus.COMPLETING,
                    status=UploadSessionStatus.COMPLETED,
                    quota_committed_at=datetime.now(UTC),
                    verified_size_bytes=observed_size,
                    verified_sha256=observed_hash,
                    object_reference_id=reference.id,
                    completed_at=datetime.now(UTC),
                    failure_code=None,
                ):
                    current = await self._load_session(
                        session_id=session.id,
                        workspace_id=session.workspace_id,
                        project_id=session.project_id,
                    )
                    if current.status != UploadSessionStatus.COMPLETED:
                        raise ApiError(
                            409, "upload_session_state_conflict", "Completion was not recorded."
                        )
                    session = current
            await self._delete_parts_best_effort(session)
            return GenericMediaUploadCompletion(session=session, reference=reference)
        except Exception as exc:
            if not quota_committed:
                await self._terminate(
                    session,
                    status=UploadSessionStatus.FAILED,
                    code=exc.code
                    if isinstance(exc, ApiError)
                    else "media_upload_completion_failed",
                    delete_final=True,
                    reference=reference,
                )
            if isinstance(exc, ApiError):
                raise
            logger.exception(
                "generic_media_upload_completion_failed", extra={"session_id": str(session.id)}
            )
            raise ApiError(
                503, "media_upload_completion_failed", "Multipart media completion failed."
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
    ) -> GenericMediaUploadSession:
        session = await self.get(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if session.status == UploadSessionStatus.COMPLETED:
            raise ApiError(
                409, "upload_session_not_abortable", "Completed media cannot be aborted."
            )
        if session.status == UploadSessionStatus.COMPLETING:
            raise ApiError(409, "upload_completion_in_progress", "Completion is in progress.")
        status = (
            session.status
            if session.status in {UploadSessionStatus.EXPIRED, UploadSessionStatus.FAILED}
            else UploadSessionStatus.ABORTED
        )
        return await self._terminate(
            session,
            status=status,
            code=session.failure_code or "media_upload_aborted",
            delete_final=True,
        )

    async def delete_completed(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> None:
        """Retire a finalized media object through the reference/quota lifecycle."""
        session = await self.get(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if session.status != UploadSessionStatus.COMPLETED or session.object_reference_id is None:
            raise ApiError(409, "media_upload_not_completed", "Media is not a finalized upload.")
        reference = await self._object_reference_service.get_active_reference(
            workspace_id=workspace_id,
            project_id=project_id,
            reference_id=session.object_reference_id,
        )
        await self._object_reference_service.mark_deleted(
            workspace_id=workspace_id,
            project_id=project_id,
            reference_id=reference.id,
        )
        await self._delete_object_best_effort(reference.storage_bucket, reference.storage_path)
        deleted = await self._quota_service.delete_committed(session.quota_reservation_id)
        if not deleted:
            raise ApiError(
                503, "storage_quota_cleanup_incomplete", "Media quota cleanup must be retried."
            )

    async def _completed_result(
        self, session: GenericMediaUploadSession
    ) -> GenericMediaUploadCompletion:
        if session.object_reference_id is None:
            raise ApiError(503, "upload_session_invalid", "Completed media lacks a reference.")
        reference = await self._object_reference_service.get_active_reference(
            workspace_id=session.workspace_id,
            project_id=session.project_id,
            reference_id=session.object_reference_id,
        )
        await self._delete_parts_best_effort(session)
        return GenericMediaUploadCompletion(session=session, reference=reference)

    async def _terminate(
        self,
        session: GenericMediaUploadSession,
        *,
        status: UploadSessionStatus,
        code: str,
        delete_final: bool,
        reference: ObjectReference | None = None,
    ) -> GenericMediaUploadSession:
        if status not in {
            UploadSessionStatus.ABORTED,
            UploadSessionStatus.EXPIRED,
            UploadSessionStatus.FAILED,
        }:
            raise ValueError("invalid generic media terminal status")
        if session.quota_committed_at is not None:
            raise ApiError(
                409, "upload_completion_in_progress", "Committed media must resume completion."
            )
        if self._db_client is not None:
            response = await self._db_client.async_request(
                "POST",
                "/rpc/motionly_terminate_generic_media_upload_session",
                json={
                    "p_session_id": str(session.id),
                    "p_workspace_id": str(session.workspace_id),
                    "p_project_id": str(session.project_id),
                    "p_status": status.value,
                    "p_failure_code": code,
                    "p_delete_final": delete_final,
                },
            )
            self._raise_db(response)
            session = await self._load_session(
                session_id=session.id,
                workspace_id=session.workspace_id,
                project_id=session.project_id,
            )
        else:
            if session.status in {UploadSessionStatus.ACTIVE, UploadSessionStatus.COMPLETING}:
                await self._transition(
                    session,
                    expected=session.status,
                    status=status,
                    aborted_at=datetime.now(UTC) if status == UploadSessionStatus.ABORTED else None,
                    failed_at=datetime.now(UTC) if status != UploadSessionStatus.ABORTED else None,
                    failure_code=code,
                )
            released = await self._quota_service.release_reservation(session.quota_reservation_id)
            if not released and await self._quota_service.get_reservation_state(
                session.quota_reservation_id
            ) not in {None, "released"}:
                raise ApiError(
                    503, "upload_cleanup_incomplete", "Media quota cleanup must be retried."
                )
        await self._delete_parts_best_effort(session)
        if delete_final:
            await self._delete_object_best_effort(
                session.final_storage_bucket, session.final_storage_path
            )
        if reference is not None:
            await self._object_reference_service.mark_deleted(
                workspace_id=session.workspace_id,
                project_id=session.project_id,
                reference_id=reference.id,
            )
        elif session.object_reference_id is not None:
            try:
                await self._object_reference_service.mark_deleted(
                    workspace_id=session.workspace_id,
                    project_id=session.project_id,
                    reference_id=session.object_reference_id,
                )
            except ApiError:
                pass
        return session

    def _assemble_parts(self, session: GenericMediaUploadSession) -> tuple[Path, int, str]:
        descriptor, temporary_name = tempfile.mkstemp(prefix="motionly-media-", suffix=".part")
        os.close(descriptor)
        path = Path(temporary_name)
        digest = hashlib.sha256()
        total = 0
        try:
            with path.open("wb") as output:
                for part in sorted(session.parts, key=lambda item: item.part_number):
                    source = self._object_storage.ensure_object(
                        bucket=session.final_storage_bucket, path=part.object_path
                    )
                    part_size = 0
                    with source.open("rb") as stream:
                        while chunk := stream.read(self._COPY_CHUNK_BYTES):
                            output.write(chunk)
                            digest.update(chunk)
                            part_size += len(chunk)
                            total += len(chunk)
                    if part_size != part.expected_size_bytes:
                        raise ApiError(
                            409, "upload_part_size_mismatch", "Part changed before assembly."
                        )
            return path, total, digest.hexdigest()
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _verify(
        session: GenericMediaUploadSession,
        size: int,
        digest: str,
        requested_hash: str | None,
    ) -> None:
        if size != session.declared_size_bytes:
            raise ApiError(409, "upload_size_mismatch", "Assembled size did not match preflight.")
        if any(value != digest for value in (session.expected_sha256, requested_hash) if value):
            raise ApiError(409, "upload_hash_mismatch", "Assembled SHA256 did not match.")

    async def _save_new_session(self, session: GenericMediaUploadSession) -> None:
        if self._db_client is None:
            async with self._lock:
                self._sessions[session.id] = session
            return
        response = await self._db_client.async_request(
            "POST",
            "/generic_media_upload_sessions",
            params={"select": "id"},
            json=self._session_row(session),
        )
        self._raise_db(response)
        response = await self._db_client.async_request(
            "POST",
            "/generic_media_upload_session_parts",
            params={"select": "id"},
            json=[self._part_row(part) for part in session.parts],
        )
        self._raise_db(response)

    async def _load_session(
        self, *, session_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> GenericMediaUploadSession:
        if self._db_client is None:
            session = self._sessions.get(session_id)
            if session is None or session.project_id != project_id:
                raise ApiError(
                    404, "media_upload_session_not_found", "Media upload session was not found."
                )
            if session.workspace_id != workspace_id:
                raise ApiError(
                    403, "workspace_access_denied", "You do not have access to this media session."
                )
            return session
        response = await self._db_client.async_request(
            "GET",
            "/generic_media_upload_sessions",
            params={
                "id": f"eq.{session_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "limit": "1",
            },
        )
        self._raise_db(response)
        rows = response.json()
        if not rows:
            raise ApiError(
                404, "media_upload_session_not_found", "Media upload session was not found."
            )
        parts_response = await self._db_client.async_request(
            "GET",
            "/generic_media_upload_session_parts",
            params={
                "session_id": f"eq.{session_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "part_number.asc",
            },
        )
        self._raise_db(parts_response)
        try:
            return self._from_rows(rows[0], parts_response.json())
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError(
                503, "media_upload_session_invalid", "Media upload state was invalid."
            ) from exc

    async def _mark_part_uploaded(
        self,
        *,
        session: GenericMediaUploadSession,
        part: GenericMediaUploadPart,
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
            "/generic_media_upload_session_parts",
            params={
                "session_id": f"eq.{session.id}",
                "workspace_id": f"eq.{session.workspace_id}",
                "project_id": f"eq.{session.project_id}",
                "part_number": f"eq.{part.part_number}",
                "status": "eq.pending",
                "select": "id",
            },
            json={
                "status": "uploaded",
                "verified_size_bytes": verified_size_bytes,
                "etag": etag,
                "uploaded_at": now.isoformat(),
            },
        )
        self._raise_db(response)
        if not response.json():
            return False
        part.status = UploadPartStatus.UPLOADED
        part.verified_size_bytes = verified_size_bytes
        part.etag = etag
        part.uploaded_at = now
        return True

    async def _transition(
        self,
        session: GenericMediaUploadSession,
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
                name: value.isoformat()
                if isinstance(value, datetime)
                else str(value)
                if isinstance(value, UUID)
                else value
                for name, value in values.items()
            },
        }
        response = await self._db_client.async_request(
            "PATCH",
            "/generic_media_upload_sessions",
            params={
                "id": f"eq.{session.id}",
                "workspace_id": f"eq.{session.workspace_id}",
                "project_id": f"eq.{session.project_id}",
                "status": f"eq.{expected.value}",
                "select": "id",
            },
            json=payload,
        )
        self._raise_db(response)
        if not response.json():
            return False
        session.status = status
        for name, value in values.items():
            setattr(session, name, value)
        return True

    async def _finalize(
        self, session: GenericMediaUploadSession, reference: ObjectReference, size: int, digest: str
    ) -> bool:
        response = await self._db_client.async_request(
            "POST",
            "/rpc/motionly_finalize_generic_media_upload_session",
            json={
                "p_session_id": str(session.id),
                "p_workspace_id": str(session.workspace_id),
                "p_project_id": str(session.project_id),
                "p_reference_id": str(reference.id),
                "p_verified_size_bytes": size,
                "p_verified_sha256": digest,
            },
        )
        self._raise_db(response)
        return response.json() is True

    async def _delete_parts_best_effort(self, session: GenericMediaUploadSession) -> None:
        for part in session.parts:
            await self._delete_object_best_effort(session.final_storage_bucket, part.object_path)

    async def _delete_object_best_effort(self, bucket: str, path: str) -> None:
        try:
            await run_blocking(self._object_storage.delete_object, bucket=bucket, path=path)
        except Exception:
            logger.warning(
                "generic_media_upload_cleanup_failed", extra={"bucket": bucket, "path": path}
            )

    @staticmethod
    def _confirmation(
        session: GenericMediaUploadSession, part: GenericMediaUploadPart
    ) -> GenericMediaPartConfirmation:
        if part.verified_size_bytes is None:
            raise ApiError(503, "upload_part_invalid", "Confirmed part lacks verification.")
        return GenericMediaPartConfirmation(
            session.id,
            part.part_number,
            part.verified_size_bytes,
            session.completed_parts,
            session.uploaded_bytes,
        )

    @staticmethod
    def _require_active(session: GenericMediaUploadSession) -> None:
        if session.status != UploadSessionStatus.ACTIVE:
            raise ApiError(
                409, f"upload_session_{session.status.value}", "Media upload is not active."
            )

    @staticmethod
    def _require_active_or_completing(session: GenericMediaUploadSession) -> None:
        if session.status not in {UploadSessionStatus.ACTIVE, UploadSessionStatus.COMPLETING}:
            code = (
                "upload_session_expired"
                if session.status == UploadSessionStatus.EXPIRED
                else f"upload_session_{session.status.value}"
            )
            raise ApiError(
                410 if session.status == UploadSessionStatus.EXPIRED else 409,
                code,
                "Media upload is not active.",
            )

    @staticmethod
    def _validate_hash(value: str | None) -> None:
        if value is not None and (
            len(value) != 64 or any(item not in "0123456789abcdef" for item in value)
        ):
            raise ApiError(
                422,
                "upload_hash_invalid",
                "Upload SHA256 must be 64 lowercase hexadecimal characters.",
            )

    @staticmethod
    def _validate_storage_path(path: str, *, workspace_id: UUID, project_id: UUID) -> None:
        decoded = unquote(path)
        segments = path.split("/")
        prefix = f"workspaces/{workspace_id}/projects/{project_id}/"
        if (
            decoded != path
            or not path.startswith(prefix)
            or path.startswith("/")
            or "\\" in path
            or "" in segments
            or any(item in {".", ".."} for item in segments)
        ):
            raise ApiError(
                422, "storage_path_invalid", "Media storage path is outside this project."
            )

    @staticmethod
    def _raise_db(response: Any) -> None:
        if response.status_code >= 400:
            raise ApiError(
                503,
                "media_upload_session_unavailable",
                "Multipart media state is temporarily unavailable.",
            )

    @staticmethod
    def _session_row(session: GenericMediaUploadSession) -> dict[str, Any]:
        return {
            "id": str(session.id),
            "workspace_id": str(session.workspace_id),
            "project_id": str(session.project_id),
            "owner_type": session.owner_type,
            "owner_id": str(session.owner_id),
            "object_type": session.object_type,
            "final_storage_bucket": session.final_storage_bucket,
            "final_storage_path": session.final_storage_path,
            "original_filename": session.original_filename,
            "mime_type": session.mime_type,
            "declared_size_bytes": session.declared_size_bytes,
            "expected_sha256": session.expected_sha256,
            "part_size_bytes": session.part_size_bytes,
            "part_count": session.part_count,
            "status": session.status.value,
            "quota_reservation_id": str(session.quota_reservation_id),
            "expires_at": session.expires_at.isoformat(),
        }

    @staticmethod
    def _part_row(part: GenericMediaUploadPart) -> dict[str, Any]:
        return {
            "id": str(part.id),
            "workspace_id": str(part.workspace_id),
            "project_id": str(part.project_id),
            "session_id": str(part.session_id),
            "part_number": part.part_number,
            "expected_size_bytes": part.expected_size_bytes,
            "object_path": part.object_path,
            "status": part.status.value,
        }

    @classmethod
    def _from_rows(
        cls, row: dict[str, Any], rows: list[dict[str, Any]]
    ) -> GenericMediaUploadSession:
        parts = [
            GenericMediaUploadPart(
                id=UUID(str(part["id"])),
                workspace_id=UUID(str(part["workspace_id"])),
                project_id=UUID(str(part["project_id"])),
                session_id=UUID(str(part["session_id"])),
                part_number=int(part["part_number"]),
                expected_size_bytes=int(part["expected_size_bytes"]),
                object_path=str(part["object_path"]),
                status=UploadPartStatus(str(part["status"])),
                verified_size_bytes=int(part["verified_size_bytes"])
                if part.get("verified_size_bytes") is not None
                else None,
                etag=str(part["etag"]) if part.get("etag") else None,
                uploaded_at=cls._datetime(part.get("uploaded_at")),
                created_at=cls._datetime(part.get("created_at")) or datetime.now(UTC),
                updated_at=cls._datetime(part.get("updated_at")) or datetime.now(UTC),
            )
            for part in rows
        ]
        session = GenericMediaUploadSession(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            owner_type=str(row["owner_type"]),
            owner_id=UUID(str(row["owner_id"])),
            object_type=str(row["object_type"]),
            final_storage_bucket=str(row["final_storage_bucket"]),
            final_storage_path=str(row["final_storage_path"]),
            original_filename=str(row["original_filename"]),
            mime_type=str(row["mime_type"]),
            declared_size_bytes=int(row["declared_size_bytes"]),
            expected_sha256=str(row["expected_sha256"]) if row.get("expected_sha256") else None,
            part_size_bytes=int(row["part_size_bytes"]),
            part_count=int(row["part_count"]),
            quota_reservation_id=UUID(str(row["quota_reservation_id"])),
            expires_at=cls._datetime(row["expires_at"]) or datetime.now(UTC),
            parts=parts,
            status=UploadSessionStatus(str(row["status"])),
            quota_committed_at=cls._datetime(row.get("quota_committed_at")),
            verified_size_bytes=int(row["verified_size_bytes"])
            if row.get("verified_size_bytes") is not None
            else None,
            verified_sha256=str(row["verified_sha256"]) if row.get("verified_sha256") else None,
            object_reference_id=UUID(str(row["object_reference_id"]))
            if row.get("object_reference_id")
            else None,
            completed_at=cls._datetime(row.get("completed_at")),
            aborted_at=cls._datetime(row.get("aborted_at")),
            failed_at=cls._datetime(row.get("failed_at")),
            failure_code=str(row["failure_code"]) if row.get("failure_code") else None,
            created_at=cls._datetime(row.get("created_at")) or datetime.now(UTC),
            updated_at=cls._datetime(row.get("updated_at")) or datetime.now(UTC),
        )
        if len(parts) != session.part_count or [part.part_number for part in parts] != list(
            range(1, session.part_count + 1)
        ):
            raise ValueError("generic session parts are incomplete")
        return session

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("session timestamp is not timezone-aware")
        return parsed
