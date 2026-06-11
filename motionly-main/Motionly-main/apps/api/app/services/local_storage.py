from pathlib import Path
from urllib.parse import quote, unquote

import httpx

from app.api.errors import ApiError


class LocalObjectStorage:
    def __init__(self, *, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def object_path(self, *, bucket: str, path: str) -> Path:
        safe_bucket = _safe_part(bucket)
        decoded = unquote(path)
        candidate = (self.root / safe_bucket / decoded).resolve()
        bucket_root = (self.root / safe_bucket).resolve()
        if bucket_root != candidate and bucket_root not in candidate.parents:
            raise ApiError(400, "storage_path_invalid", "Storage path is invalid.")
        return candidate

    def write_object(self, *, bucket: str, path: str, content: bytes) -> Path:
        target = self.object_path(bucket=bucket, path=path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def ensure_object(self, *, bucket: str, path: str) -> Path:
        target = self.object_path(bucket=bucket, path=path)
        if not target.exists() or not target.is_file():
            raise ApiError(404, "storage_object_missing", "Uploaded video was not found.")
        return target

    def upload_file(
        self,
        *,
        bucket: str,
        path: str,
        source_path: str | Path,
        content_type: str = "application/octet-stream",
    ) -> Path:
        source = Path(source_path)
        target = self.object_path(bucket=bucket, path=path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        return target


class SupabaseBackedObjectStorage(LocalObjectStorage):
    def __init__(
        self,
        *,
        root: str | Path,
        supabase_url: str,
        service_role_key: str,
    ) -> None:
        super().__init__(root=root)
        self.supabase_url = supabase_url.rstrip("/")
        self.service_role_key = service_role_key

    def ensure_object(self, *, bucket: str, path: str) -> Path:
        target = self.object_path(bucket=bucket, path=path)
        if target.exists() and target.is_file():
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        encoded_path = quote(path, safe="/")
        response = httpx.get(
            f"{self.supabase_url}/storage/v1/object/{bucket}/{encoded_path}",
            headers=self._storage_headers(),
            timeout=60,
        )
        if response.status_code == 404:
            raise ApiError(404, "storage_object_missing", "Uploaded video was not found.")
        if response.status_code >= 400:
            raise ApiError(
                502,
                "storage_download_failed",
                "Uploaded video could not be downloaded from storage.",
            )
        target.write_bytes(response.content)
        return target

    def upload_file(
        self,
        *,
        bucket: str,
        path: str,
        source_path: str | Path,
        content_type: str = "application/octet-stream",
    ) -> Path:
        source = Path(source_path)
        if not source.exists() or not source.is_file():
            raise ApiError(404, "storage_object_missing", "Generated export was not found.")

        encoded_path = quote(path, safe="/")
        headers = {
            **self._storage_headers(),
            "content-type": content_type,
            "x-upsert": "true",
        }
        response = httpx.post(
            f"{self.supabase_url}/storage/v1/object/{bucket}/{encoded_path}",
            headers=headers,
            content=source.read_bytes(),
            timeout=60,
        )
        if response.status_code >= 400:
            raise ApiError(
                502,
                "storage_upload_failed",
                "Generated export could not be uploaded to storage.",
            )
        return self.object_path(bucket=bucket, path=path)

    def _storage_headers(self) -> dict[str, str]:
        return {
            "apikey": self.service_role_key,
            "authorization": f"Bearer {self.service_role_key}",
        }


def create_object_storage(
    *,
    root: str | Path,
    supabase_url: str | None,
    service_role_key: str | None,
) -> LocalObjectStorage:
    if supabase_url and service_role_key:
        return SupabaseBackedObjectStorage(
            root=root,
            supabase_url=supabase_url,
            service_role_key=service_role_key,
        )
    return LocalObjectStorage(root=root)


def _safe_part(value: str) -> str:
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise ApiError(400, "storage_bucket_invalid", "Storage bucket is invalid.")
    return value
