import os
import shutil
import time
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
        target.touch(exist_ok=True)
        return target

    def evict_cache(self, *, max_bytes: int, max_age_seconds: int) -> dict[str, int]:
        """Evict oldest cached files until both age and size bounds are satisfied."""
        now = time.time()
        files = [item for item in self.root.rglob("*") if item.is_file()]
        removed_files = 0
        removed_bytes = 0
        for item in files:
            stat = item.stat()
            if now - stat.st_mtime > max_age_seconds:
                item.unlink(missing_ok=True)
                removed_files += 1
                removed_bytes += stat.st_size
        remaining = sorted(
            (item for item in files if item.exists()), key=lambda item: item.stat().st_mtime
        )
        total = sum(item.stat().st_size for item in remaining)
        for item in remaining:
            if total <= max_bytes:
                break
            size = item.stat().st_size
            item.unlink(missing_ok=True)
            total -= size
            removed_files += 1
            removed_bytes += size
        return {"removed_files": removed_files, "removed_bytes": removed_bytes}

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
        if source.resolve() == target.resolve():
            return target
        with source.open("rb") as input_file, target.open("wb") as output_file:
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
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
        temporary = target.with_suffix(target.suffix + ".partial")
        with httpx.stream(
            "GET",
            f"{self.supabase_url}/storage/v1/object/{bucket}/{encoded_path}",
            headers=self._storage_headers(),
            timeout=180,
        ) as response:
            if response.status_code == 404:
                raise ApiError(404, "storage_object_missing", "Uploaded video was not found.")
            if response.status_code >= 400:
                raise ApiError(
                    502,
                    "storage_download_failed",
                    "Uploaded video could not be downloaded from storage.",
                )
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    output.write(chunk)
        os.replace(temporary, target)
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
            content=_file_chunks(source),
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


def _file_chunks(path: Path, *, chunk_size: int = 1024 * 1024):
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            yield chunk
