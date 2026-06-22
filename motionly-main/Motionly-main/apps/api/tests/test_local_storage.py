import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.api.errors import ApiError
from app.api.local_storage import get_local_object, put_local_object
from app.services.local_storage import (
    LocalObjectStorage,
    SupabaseBackedObjectStorage,
    create_object_storage,
)
from app.services.storage import StorageSigner


def test_create_object_storage_uses_supabase_when_configured(tmp_path: Path) -> None:
    storage = create_object_storage(
        root=tmp_path,
        supabase_url="https://example.supabase.co",
        service_role_key="service-role",
    )

    assert isinstance(storage, SupabaseBackedObjectStorage)


def test_local_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalObjectStorage(root=tmp_path)

    with pytest.raises(ApiError) as exc_info:
        storage.object_path(bucket="raw-uploads", path="../secret.mp4")

    assert exc_info.value.code == "storage_path_invalid"


def test_storage_signer_falls_back_to_local_urls_without_supabase() -> None:
    signer = StorageSigner(base_url="http://api.local")

    upload = signer.create_signed_upload(
        bucket="raw-uploads",
        path="workspaces/one/raw/video.mp4",
        mime_type="video/mp4",
        expires_in_seconds=900,
    )
    download = signer.create_signed_download(
        bucket="exports",
        path="workspaces/one/exports/video.mp4",
        expires_in_seconds=300,
    )

    assert upload.url == "http://api.local/_local/storage/raw-uploads/workspaces/one/raw/video.mp4"
    assert upload.headers == {"content-type": "video/mp4"}
    assert (
        download.url == "http://api.local/_local/storage/exports/workspaces/one/exports/video.mp4"
    )


def test_local_storage_route_writes_and_reads_development_object(tmp_path: Path) -> None:
    request = _local_request(tmp_path, body=b"video-bytes")

    response = asyncio.run(put_local_object("raw-uploads", "workspaces/one/raw/video.mp4", request))
    file_response = asyncio.run(
        get_local_object("raw-uploads", "workspaces/one/raw/video.mp4", request)
    )

    assert response.status_code == 204
    assert Path(file_response.path).read_bytes() == b"video-bytes"


def test_local_storage_route_is_unavailable_in_production(tmp_path: Path) -> None:
    request = _local_request(tmp_path, app_env="production")

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(put_local_object("raw-uploads", "workspaces/one/raw/video.mp4", request))

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "local_storage_unavailable"


def _local_request(
    tmp_path: Path,
    *,
    body: bytes = b"",
    app_env: str = "development",
    supabase_url: str | None = None,
    supabase_service_role_key: str | None = None,
):
    class Request:
        app = SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env=app_env,
                    supabase_url=supabase_url,
                    supabase_service_role_key=supabase_service_role_key,
                    media_work_dir=str(tmp_path),
                ),
            ),
        )

        async def body(self) -> bytes:
            return body

    return Request()


def test_supabase_storage_downloads_missing_object(monkeypatch, tmp_path: Path) -> None:
    calls = {}

    def fake_get(url: str, *, headers: dict[str, str], timeout: int):
        calls["url"] = url
        calls["headers"] = headers
        calls["timeout"] = timeout
        return httpx.Response(200, content=b"video-bytes")

    monkeypatch.setattr("app.services.local_storage.httpx.get", fake_get)
    storage = SupabaseBackedObjectStorage(
        root=tmp_path,
        supabase_url="https://example.supabase.co",
        service_role_key="service-role",
    )

    target = storage.ensure_object(bucket="raw-uploads", path="workspaces/one/video.mp4")

    assert target.read_bytes() == b"video-bytes"
    assert calls["url"].endswith("/storage/v1/object/raw-uploads/workspaces/one/video.mp4")
    assert calls["headers"]["authorization"] == "Bearer service-role"


def test_supabase_storage_uploads_file(monkeypatch, tmp_path: Path) -> None:
    calls = {}

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
        timeout: int,
    ):
        calls["url"] = url
        calls["headers"] = headers
        calls["content"] = content
        calls["timeout"] = timeout
        return httpx.Response(200, json={"Key": "exports/video.mp4"})

    monkeypatch.setattr("app.services.local_storage.httpx.post", fake_post)
    source = tmp_path / "render.mp4"
    source.write_bytes(b"render-bytes")
    storage = SupabaseBackedObjectStorage(
        root=tmp_path,
        supabase_url="https://example.supabase.co",
        service_role_key="service-role",
    )

    storage.upload_file(
        bucket="exports",
        path="workspaces/one/export.mp4",
        source_path=source,
        content_type="video/mp4",
    )

    assert calls["url"].endswith("/storage/v1/object/exports/workspaces/one/export.mp4")
    assert calls["headers"]["content-type"] == "video/mp4"
    assert calls["headers"]["x-upsert"] == "true"
    assert calls["content"] == b"render-bytes"
