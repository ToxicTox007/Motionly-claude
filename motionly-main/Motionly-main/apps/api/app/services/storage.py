from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.api.errors import ApiError


@dataclass(frozen=True)
class SignedUpload:
    url: str
    headers: dict[str, str]
    expires_in_seconds: int


@dataclass(frozen=True)
class SignedDownload:
    url: str


class StorageSigner:
    MAX_DOWNLOAD_TTL_SECONDS = 300
    MAX_WORKER_DOWNLOAD_TTL_SECONDS = 7200
    MAX_UPLOAD_TTL_SECONDS = 900
    SUPABASE_SIGNED_UPLOAD_TTL_SECONDS = 7200

    def __init__(
        self,
        base_url: str = "https://api.motionly.invalid",
        supabase_url: str | None = None,
        service_role_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.supabase_url = supabase_url.rstrip("/") if supabase_url else None
        self.service_role_key = service_role_key

    def create_signed_upload(
        self,
        *,
        bucket: str,
        path: str,
        mime_type: str,
        expires_in_seconds: int,
    ) -> SignedUpload:
        expires_in_seconds = max(1, min(expires_in_seconds, self.MAX_UPLOAD_TTL_SECONDS))
        if self.supabase_url and self.service_role_key:
            signed_url = self._create_supabase_signed_upload_url(bucket=bucket, path=path)
            return SignedUpload(
                url=signed_url,
                headers={
                    "content-type": mime_type,
                    "x-upsert": "false",
                },
                expires_in_seconds=self.SUPABASE_SIGNED_UPLOAD_TTL_SECONDS,
            )

        return SignedUpload(
            url=self._local_object_url(bucket=bucket, path=path),
            headers={"content-type": mime_type},
            expires_in_seconds=expires_in_seconds,
        )

    def create_signed_download(
        self,
        *,
        bucket: str,
        path: str,
        expires_in_seconds: int,
    ) -> SignedDownload:
        return self._create_signed_download_with_cap(
            bucket=bucket,
            path=path,
            expires_in_seconds=expires_in_seconds,
            maximum_ttl_seconds=self.MAX_DOWNLOAD_TTL_SECONDS,
        )

    def create_signed_worker_download(
        self,
        *,
        bucket: str,
        path: str,
        expires_in_seconds: int,
    ) -> SignedDownload:
        """Sign a worker fetch explicitly; browser callers retain the 300-second cap."""
        return self._create_signed_download_with_cap(
            bucket=bucket,
            path=path,
            expires_in_seconds=expires_in_seconds,
            maximum_ttl_seconds=self.MAX_WORKER_DOWNLOAD_TTL_SECONDS,
        )

    def _create_signed_download_with_cap(
        self,
        *,
        bucket: str,
        path: str,
        expires_in_seconds: int,
        maximum_ttl_seconds: int,
    ) -> SignedDownload:
        expires_in_seconds = max(1, min(expires_in_seconds, maximum_ttl_seconds))
        if self.supabase_url and self.service_role_key:
            signed_url = self._create_supabase_signed_download_url(
                bucket=bucket,
                path=path,
                expires_in_seconds=expires_in_seconds,
            )
            return SignedDownload(url=signed_url)

        return SignedDownload(url=self._local_object_url(bucket=bucket, path=path))

    def delete_object(self, *, bucket: str, path: str) -> None:
        if not self.supabase_url or not self.service_role_key:
            return
        response = httpx.request(
            "DELETE",
            f"{self.supabase_url}/storage/v1/object/{bucket}",
            headers=self._storage_headers(),
            json={"prefixes": [path]},
            timeout=10,
        )
        if response.status_code >= 400 and response.status_code != 404:
            raise ApiError(502, "storage_delete_failed", "Library video could not be deleted.")

    def _local_object_url(self, *, bucket: str, path: str) -> str:
        encoded_bucket = quote(bucket, safe="")
        encoded_path = quote(path, safe="/")
        return f"{self.base_url}/_local/storage/{encoded_bucket}/{encoded_path}"

    def _storage_headers(self) -> dict[str, str]:
        if not self.service_role_key:
            raise ApiError(500, "storage_not_configured", "Storage signing is not configured.")
        return {
            "apikey": self.service_role_key,
            "authorization": f"Bearer {self.service_role_key}",
            "content-type": "application/json",
        }

    def _create_supabase_signed_upload_url(self, *, bucket: str, path: str) -> str:
        encoded_path = quote(path, safe="/")
        response = httpx.post(
            f"{self.supabase_url}/storage/v1/object/upload/sign/{bucket}/{encoded_path}",
            headers=self._storage_headers(),
            json={"upsert": False},
            timeout=10,
        )
        if response.status_code >= 400:
            raise ApiError(502, "storage_signing_failed", "Upload URL could not be signed.")
        payload = response.json()
        signed_path = payload.get("signedURL") or payload.get("signedUrl") or payload.get("url")
        if not isinstance(signed_path, str):
            raise ApiError(
                502,
                "storage_signing_failed",
                "Upload URL signing response was invalid.",
            )
        if signed_path.startswith("http"):
            return signed_path
        return f"{self.supabase_url}/storage/v1{signed_path}"

    def _create_supabase_signed_download_url(
        self,
        *,
        bucket: str,
        path: str,
        expires_in_seconds: int,
    ) -> str:
        encoded_path = quote(path, safe="/")
        response = httpx.post(
            f"{self.supabase_url}/storage/v1/object/sign/{bucket}/{encoded_path}",
            headers=self._storage_headers(),
            json={"expiresIn": expires_in_seconds},
            timeout=10,
        )
        if response.status_code >= 400:
            raise ApiError(502, "storage_signing_failed", "Download URL could not be signed.")
        payload = response.json()
        signed_path = payload.get("signedURL") or payload.get("signedUrl") or payload.get("url")
        if not isinstance(signed_path, str):
            raise ApiError(
                502,
                "storage_signing_failed",
                "Download URL signing response was invalid.",
            )
        if signed_path.startswith("http"):
            return signed_path
        return f"{self.supabase_url}/storage/v1{signed_path}"
