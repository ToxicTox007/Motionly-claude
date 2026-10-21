from dataclasses import dataclass
from urllib.parse import quote, quote_plus

import httpx

from app.api.errors import ApiError
from app.services.local_storage_signing import sign_local_object_query


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
        public_supabase_url: str | None = None,
        signing_secret: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # The API signs over the private network (supabase_url). Browsers open
        # links on the public origin (public_supabase_url); workers fetch on the
        # private network.
        self.supabase_url = supabase_url.rstrip("/") if supabase_url else None
        self.public_supabase_url = (
            public_supabase_url.rstrip("/") if public_supabase_url else self.supabase_url
        )
        self.service_role_key = service_role_key
        # When local file storage is used (no Supabase Storage), every link is
        # HMAC-signed with an expiry so disk objects cannot be read or written
        # through an unsigned or tampered URL (B38).
        self.signing_secret = signing_secret

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
            signed_url = self._create_supabase_signed_upload_url(
                bucket=bucket, path=path, link_base=self.public_supabase_url or self.supabase_url
            )
            return SignedUpload(
                url=signed_url,
                headers={
                    "content-type": mime_type,
                    "x-upsert": "false",
                },
                expires_in_seconds=self.SUPABASE_SIGNED_UPLOAD_TTL_SECONDS,
            )

        return SignedUpload(
            url=self._local_object_url(
                bucket=bucket,
                path=path,
                method="PUT",
                expires_in_seconds=expires_in_seconds,
            ),
            headers={"content-type": mime_type},
            expires_in_seconds=expires_in_seconds,
        )

    def create_signed_download(
        self,
        *,
        bucket: str,
        path: str,
        expires_in_seconds: int,
        download: str | None = None,
    ) -> SignedDownload:
        return self._create_signed_download_with_cap(
            bucket=bucket,
            path=path,
            expires_in_seconds=expires_in_seconds,
            maximum_ttl_seconds=self.MAX_DOWNLOAD_TTL_SECONDS,
            link_base=self.public_supabase_url,
            download=download,
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
            link_base=self.supabase_url,
        )

    def _create_signed_download_with_cap(
        self,
        *,
        bucket: str,
        path: str,
        expires_in_seconds: int,
        maximum_ttl_seconds: int,
        link_base: str | None,
        download: str | None = None,
    ) -> SignedDownload:
        expires_in_seconds = max(1, min(expires_in_seconds, maximum_ttl_seconds))
        if self.supabase_url and self.service_role_key:
            # Only forward the download option when one is requested so existing
            # callers and test stubs keep their original call signature.
            extra = {"download": download} if download else {}
            signed_url = self._create_supabase_signed_download_url(
                bucket=bucket,
                path=path,
                expires_in_seconds=expires_in_seconds,
                link_base=link_base or self.supabase_url,
                **extra,
            )
            return SignedDownload(url=signed_url)

        return SignedDownload(
            url=self._append_download(
                self._local_object_url(
                    bucket=bucket,
                    path=path,
                    method="GET",
                    expires_in_seconds=expires_in_seconds,
                ),
                download,
            )
        )

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

    def _local_object_url(
        self,
        *,
        bucket: str,
        path: str,
        method: str = "GET",
        expires_in_seconds: int | None = None,
    ) -> str:
        encoded_bucket = quote(bucket, safe="")
        encoded_path = quote(path, safe="/")
        url = f"{self.base_url}/_local/storage/{encoded_bucket}/{encoded_path}"
        if not self.signing_secret:
            return url
        ttl = (
            expires_in_seconds
            if expires_in_seconds is not None
            else self.MAX_DOWNLOAD_TTL_SECONDS
        )
        query = sign_local_object_query(
            secret=self.signing_secret,
            method=method,
            bucket=bucket,
            path=path,
            expires_in_seconds=ttl,
        )
        return f"{url}?{query}"

    def _storage_headers(self) -> dict[str, str]:
        if not self.service_role_key:
            raise ApiError(500, "storage_not_configured", "Storage signing is not configured.")
        return {
            "apikey": self.service_role_key,
            "authorization": f"Bearer {self.service_role_key}",
            "content-type": "application/json",
        }

    def _create_supabase_signed_upload_url(self, *, bucket: str, path: str, link_base: str) -> str:
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
        return f"{link_base}/storage/v1{signed_path}"

    def _create_supabase_signed_download_url(
        self,
        *,
        bucket: str,
        path: str,
        expires_in_seconds: int,
        link_base: str,
        download: str | None = None,
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
            return self._append_download(signed_path, download)
        return self._append_download(f"{link_base}/storage/v1{signed_path}", download)

    @staticmethod
    def _append_download(url: str, download: str | None) -> str:
        """Attach Supabase Storage's ``download`` option so the browser receives a
        ``Content-Disposition: attachment; filename=...`` response from the signed link."""
        if not download:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}download={quote_plus(download)}"
