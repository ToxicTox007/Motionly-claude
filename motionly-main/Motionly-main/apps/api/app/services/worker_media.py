"""Resolve durable media references immediately before worker execution."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
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
    signed_url = _sign_worker_reference(
        workspace_id=workspace_id,
        project_id=project_id,
        reference_id=reference_id,
    )
    if signed_url is None:
        return storage.ensure_object(bucket=bucket, path=path)

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
        with safe_http_stream(signed_url, timeout=180) as response:
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


def _sign_worker_reference(
    *,
    workspace_id: UUID,
    project_id: UUID,
    reference_id: UUID | None,
) -> str | None:
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
    if not isinstance(signed_url, str) or not signed_url.startswith("https://"):
        raise ApiError(
            502,
            "worker_media_signing_invalid",
            "Worker media signing returned an invalid URL.",
        )
    return signed_url
