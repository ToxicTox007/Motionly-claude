"""Resolve durable media references immediately before worker execution."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import SplitResult, urlsplit
from uuid import UUID

import httpx

from app.api.errors import ApiError
from app.repositories.uploads import UploadRecord
from app.services.local_storage import LocalObjectStorage
from app.services.media_url_policy import MediaUrlPolicyError, safe_http_stream

MAX_SOURCE_BYTES = 512 * 1024 * 1024


def materialize_upload_source(
    *,
    upload: UploadRecord,
    storage: LocalObjectStorage,
) -> Path:
    """Sign an upload's durable reference just in time and verify the fetched object."""
    return materialize_worker_object(
        workspace_id=upload.workspace_id,
        project_id=upload.project_id,
        reference_id=upload.source_object_reference_id,
        storage=storage,
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        expected_size_bytes=upload.verified_size_bytes or upload.file_size_bytes,
        expected_sha256=upload.verified_sha256,
    )


def materialize_worker_object(
    *,
    workspace_id: UUID,
    project_id: UUID,
    reference_id: UUID | None,
    storage: LocalObjectStorage,
    bucket: str,
    path: str,
    expected_size_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> Path:
    """Fetch a reference-signed object into the worker cache, with bounded verification."""
    signed = _sign_worker_reference(
        workspace_id=workspace_id,
        project_id=project_id,
        reference_id=reference_id,
    )
    if signed is None:
        return storage.ensure_object(bucket=bucket, path=path)
    signed_url, internal = signed

    if expected_size_bytes is not None and not 0 < expected_size_bytes <= MAX_SOURCE_BYTES:
        raise ApiError(
            409,
            "worker_media_size_invalid",
            "Durable media size is outside the worker source limit.",
        )

    target = storage.object_path(bucket=bucket, path=path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".worker-download")
    hasher = hashlib.sha256()
    downloaded = 0
    try:
        # A link on the configured internal storage origin is fetched directly
        # (no redirects); any other link goes through the public-address guard.
        stream = (
            httpx.stream("GET", signed_url, timeout=180, follow_redirects=False)
            if internal
            else safe_http_stream(signed_url, timeout=180)
        )
        with stream as response:
            if response.status_code >= 400:
                raise ApiError(
                    502,
                    "worker_media_download_failed",
                    "Worker media could not be downloaded.",
                )
            try:
                content_length = int(response.headers.get("content-length") or 0)
            except ValueError as exc:
                raise ApiError(
                    502,
                    "worker_media_download_failed",
                    "Worker media size was invalid.",
                ) from exc
            if content_length > MAX_SOURCE_BYTES or (
                expected_size_bytes is not None
                and content_length not in {0, expected_size_bytes}
            ):
                raise ApiError(
                    409,
                    "worker_media_size_mismatch",
                    "Worker media size did not match the verified source.",
                )
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > MAX_SOURCE_BYTES or (
                        expected_size_bytes is not None and downloaded > expected_size_bytes
                    ):
                        raise ApiError(
                            409,
                            "worker_media_size_mismatch",
                            "Worker media exceeded the verified source size.",
                        )
                    hasher.update(chunk)
                    output.write(chunk)
        if downloaded <= 0 or (
            expected_size_bytes is not None and downloaded != expected_size_bytes
        ):
            raise ApiError(
                409,
                "worker_media_size_mismatch",
                "Worker media size did not match the verified source.",
            )
        digest = hasher.hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ApiError(
                409,
                "worker_media_hash_mismatch",
                "Worker media hash did not match the verified source.",
            )
        os.replace(temporary, target)
        return target
    except ApiError:
        temporary.unlink(missing_ok=True)
        raise
    except (httpx.HTTPError, MediaUrlPolicyError, OSError) as exc:
        temporary.unlink(missing_ok=True)
        raise ApiError(
            502,
            "worker_media_download_failed",
            "Worker media could not be downloaded.",
        ) from exc


def _origin(parsed: SplitResult) -> str | None:
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    return f"{parsed.scheme}://{parsed.hostname.lower()}:{port}"


def _internal_storage_origin() -> str | None:
    """The origin of SUPABASE_URL, the private storage gateway workers reach."""
    value = os.getenv("SUPABASE_URL", "").strip()
    return _origin(urlsplit(value)) if value else None


def _sign_worker_reference(
    *,
    workspace_id: UUID,
    project_id: UUID,
    reference_id: UUID | None,
) -> tuple[str, bool] | None:
    """A signed worker link and whether it is on the internal storage origin."""
    app_env = os.getenv("APP_ENV", "").strip().lower()
    api_base_url = (
        os.getenv("WORKER_API_BASE_URL") or os.getenv("API_PUBLIC_BASE_URL") or ""
    ).rstrip("/")
    worker_token = os.getenv("WORKER_API_TOKEN", "")

    if reference_id is None:
        if app_env == "production":
            raise ApiError(
                409,
                "durable_media_reference_missing",
                "Worker media requires a durable object reference.",
            )
        return None
    if not api_base_url or not worker_token:
        if app_env == "production":
            raise ApiError(
                503,
                "worker_media_signing_unavailable",
                "Worker media signing is not configured.",
            )
        return None

    try:
        response = httpx.post(
            f"{api_base_url}/internal/media/sign-urls",
            headers={
                "content-type": "application/json",
                "x-motionly-worker-token": worker_token,
            },
            json={
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "reference_ids": [str(reference_id)],
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise ApiError(
            503,
            "worker_media_signing_unavailable",
            "Worker media signing is temporarily unavailable.",
        ) from exc
    if response.status_code >= 400:
        raise ApiError(
            503,
            "worker_media_signing_unavailable",
            "Worker media signing is temporarily unavailable.",
        )
    try:
        payload = response.json()
        signed_url = payload["signed_urls"][str(reference_id)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError(
            502,
            "worker_media_signing_invalid",
            "Worker media signing returned an invalid response.",
        ) from exc
    if not isinstance(signed_url, str):
        raise ApiError(
            502,
            "worker_media_signing_invalid",
            "Worker media signing returned an invalid URL.",
        )
    # The API signs worker links on SUPABASE_URL (StorageSigner
    # create_signed_worker_download), the private gateway, which speaks plain
    # HTTP inside the network. Only that exact origin is trusted; anything else
    # must be HTTPS and still passes the public-address guard when fetched.
    parsed = urlsplit(signed_url)
    origin = _origin(parsed)
    internal_origin = _internal_storage_origin()
    if origin is not None and internal_origin is not None and origin == internal_origin:
        return signed_url, True
    if origin is None or parsed.scheme != "https":
        raise ApiError(
            502,
            "worker_media_signing_invalid",
            "Worker media signing returned an invalid URL.",
        )
    return signed_url, False
