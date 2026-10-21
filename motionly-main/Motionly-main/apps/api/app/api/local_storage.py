import os

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse

from app.api.errors import ApiError
from app.services.local_storage import LocalObjectStorage
from app.services.local_storage_signing import (
    LocalStorageSignatureError,
    verify_local_object_signature,
)

router = APIRouter(prefix="/_local/storage", tags=["local storage"])


def _local_storage(request: Request) -> LocalObjectStorage:
    settings = request.app.state.settings
    if settings.app_env == "production":
        raise ApiError(404, "local_storage_unavailable", "Local storage is not available.")
    if (
        settings.supabase_url
        and settings.supabase_service_role_key
        and not settings.force_local_media_storage
    ):
        raise ApiError(
            404,
            "local_storage_unavailable",
            "Local storage is disabled when Supabase Storage is configured.",
        )
    return LocalObjectStorage(root=settings.media_work_dir)


def _signing_secret(request: Request) -> str | None:
    """The secret the storage signer uses to sign local links.

    When present, every local-storage link must carry a valid, unexpired
    signature. It is intentionally the same key the signer is constructed with
    in ``app.main`` (SUPABASE_JWT_SECRET)."""
    signer = getattr(request.app.state, "storage_signer", None)
    if signer is not None and getattr(signer, "signing_secret", None):
        return signer.signing_secret
    return getattr(request.app.state.settings, "supabase_jwt_secret", None)


def _require_signed_link(
    request: Request,
    *,
    method: str,
    bucket: str,
    object_path: str,
    exp: str | None,
    sig: str | None,
) -> None:
    secret = _signing_secret(request)
    if not secret:
        # No signing secret is configured (bare local dev with no JWT secret);
        # there is no shared key to verify against, so links stay unsigned.
        return
    try:
        verify_local_object_signature(
            secret=secret,
            method=method,
            bucket=bucket,
            path=object_path,
            expires_at=exp,
            signature=sig,
        )
    except LocalStorageSignatureError as error:
        raise ApiError(
            403,
            "local_storage_link_invalid",
            "Local storage link is unsigned, tampered, or expired.",
            details={"reason": error.reason},
        ) from error


@router.put("/{bucket}/{object_path:path}", status_code=204)
async def put_local_object(
    bucket: str,
    object_path: str,
    request: Request,
    exp: str | None = Query(default=None),
    sig: str | None = Query(default=None),
) -> Response:
    _require_signed_link(
        request, method="PUT", bucket=bucket, object_path=object_path, exp=exp, sig=sig
    )
    storage = _local_storage(request)
    target = storage.object_path(bucket=bucket, path=object_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    received = 0
    try:
        with partial.open("wb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if received > request.app.state.settings.upload_max_file_size_bytes:
                    raise ApiError(413, "upload_too_large", "Upload exceeds the file size limit.")
                output.write(chunk)
        os.replace(partial, target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return Response(status_code=204)


@router.get("/{bucket}/{object_path:path}")
async def get_local_object(
    bucket: str,
    object_path: str,
    request: Request,
    exp: str | None = Query(default=None),
    sig: str | None = Query(default=None),
) -> FileResponse:
    _require_signed_link(
        request, method="GET", bucket=bucket, object_path=object_path, exp=exp, sig=sig
    )
    path = _local_storage(request).ensure_object(bucket=bucket, path=object_path)
    return FileResponse(path)
