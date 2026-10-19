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
                    upload_max_file_size_bytes=536_870_912,
                ),
            ),
        )

        async def stream(self):
            yield body

    return Request()


def test_supabase_storage_downloads_missing_object(monkeypatch, tmp_path: Path) -> None:
    calls = {}

    class FakeStream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_bytes(self, chunk_size: int):
            assert chunk_size == 1024 * 1024
            yield b"video-bytes"

    def fake_stream(method: str, url: str, *, headers: dict[str, str], timeout: int):
        assert method == "GET"
        calls["url"] = url
        calls["headers"] = headers
        calls["timeout"] = timeout
        return FakeStream()

    monkeypatch.setattr("app.services.local_storage.httpx.stream", fake_stream)
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
        content,
        timeout: int,
    ):
        calls["url"] = url
        calls["headers"] = headers
        calls["content"] = b"".join(content)
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


# Supabase Storage answers a missing object with HTTP 400 and a body whose
# statusCode is "404" (storage-api v1.14 and v1.60), and a HEAD request has no
# body to tell that apart from a missing bucket or a bad key.
_MISSING_OBJECT = {"statusCode": "404", "error": "not_found", "message": "Object not found"}
_MISSING_BUCKET = {"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"}


def _supabase_storage(tmp_path: Path) -> SupabaseBackedObjectStorage:
    return SupabaseBackedObjectStorage(
        root=tmp_path,
        supabase_url="https://example.supabase.co",
        service_role_key="service-role",
    )


def test_supabase_storage_inspects_an_object_from_its_info(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_get(url: str, *, headers: dict[str, str], timeout: int):
        calls.append(url)
        return httpx.Response(200, json={"name": "a/b.mp4", "size": 11, "etag": '"abc"'})

    monkeypatch.setattr("app.services.local_storage.httpx.get", fake_get)

    metadata = _supabase_storage(tmp_path).inspect_object(bucket="raw-uploads", path="a/b.mp4")

    assert calls == ["https://example.supabase.co/storage/v1/object/info/raw-uploads/a/b.mp4"]
    assert (metadata.size_bytes, metadata.etag, metadata.sha256) == (11, '"abc"', None)


@pytest.mark.parametrize("status", [400, 404])
def test_supabase_storage_reports_a_missing_object_as_missing(
    monkeypatch, tmp_path: Path, status: int
) -> None:
    monkeypatch.setattr(
        "app.services.local_storage.httpx.get",
        lambda *_args, **_kwargs: httpx.Response(status, json=_MISSING_OBJECT),
    )

    with pytest.raises(ApiError) as exc_info:
        _supabase_storage(tmp_path).inspect_object(bucket="raw-uploads", path="a/b.mp4")

    assert (exc_info.value.status_code, exc_info.value.code) == (404, "storage_object_missing")


def test_supabase_storage_reports_a_missing_bucket_as_a_storage_failure(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.local_storage.httpx.get",
        lambda *_args, **_kwargs: httpx.Response(400, json=_MISSING_BUCKET),
    )

    with pytest.raises(ApiError) as exc_info:
        _supabase_storage(tmp_path).inspect_object(bucket="raw-uploads", path="a/b.mp4")

    assert (exc_info.value.status_code, exc_info.value.code) == (502, "storage_inspection_failed")


def test_supabase_storage_download_reports_a_missing_object_as_missing(
    monkeypatch, tmp_path: Path
) -> None:
    class MissingStream:
        status_code = 400

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return httpx.Response(400, json=_MISSING_OBJECT).content

    monkeypatch.setattr(
        "app.services.local_storage.httpx.stream", lambda *_args, **_kwargs: MissingStream()
    )

    with pytest.raises(ApiError) as exc_info:
        _supabase_storage(tmp_path).ensure_object(bucket="raw-uploads", path="a/b.mp4")

    assert (exc_info.value.status_code, exc_info.value.code) == (404, "storage_object_missing")
