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


def test_local_signed_download_appends_download_filename() -> None:
    signer = StorageSigner(base_url="http://api.local")

    download = signer.create_signed_download(
        bucket="exports",
        path="workspaces/one/exports/video.mp4",
        expires_in_seconds=300,
        download="My Reel-v2.mp4",
    )

    # The download option carries the Content-Disposition attachment filename and
    # is URL-encoded so it is safe to place in a query string.
    assert download.url.endswith("video.mp4?download=My+Reel-v2.mp4")


def test_supabase_signed_download_appends_download_option(monkeypatch: pytest.MonkeyPatch) -> None:
    signer = StorageSigner(
        base_url="http://api.local",
        supabase_url="http://kong.internal",
        service_role_key="service-role",
        public_supabase_url="https://public.example.com",
    )

    def fake_post(url, headers, json, timeout):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"signedURL": "/object/sign/exports/video.mp4?token=abc"},
        )

    monkeypatch.setattr("app.services.storage.httpx.post", fake_post)

    download = signer.create_signed_download(
        bucket="exports",
        path="workspaces/one/exports/video.mp4",
        expires_in_seconds=300,
        download="Launch Reel-v1.mp4",
    )

    # Links use the public origin and append the download option after the existing query.
    assert download.url.startswith("https://public.example.com/storage/v1/object/sign/")
    assert download.url.endswith("&download=Launch+Reel-v1.mp4")


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
    signing_secret: str | None = None,
):
    state_kwargs = {}
    if signing_secret is not None:
        state_kwargs["storage_signer"] = SimpleNamespace(signing_secret=signing_secret)

    class Request:
        app = SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env=app_env,
                    supabase_url=supabase_url,
                    supabase_service_role_key=supabase_service_role_key,
                    supabase_jwt_secret=signing_secret,
                    media_work_dir=str(tmp_path),
                    upload_max_file_size_bytes=536_870_912,
                ),
                **state_kwargs,
            ),
        )

        async def stream(self):
            yield body

    return Request()


def test_signed_local_upload_and_download_links_carry_expiry_and_signature() -> None:
    signer = StorageSigner(base_url="http://api.local", signing_secret="link-secret")

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

    assert upload.url.startswith(
        "http://api.local/_local/storage/raw-uploads/workspaces/one/raw/video.mp4?exp="
    )
    assert "&sig=" in upload.url
    assert "exp=" in download.url and "&sig=" in download.url


def test_local_storage_route_requires_a_valid_signature(tmp_path: Path) -> None:
    from app.services.local_storage_signing import sign_local_object_query

    secret = "link-secret"
    query = sign_local_object_query(
        secret=secret,
        method="PUT",
        bucket="raw-uploads",
        path="workspaces/one/raw/video.mp4",
        expires_in_seconds=900,
    )
    exp, sig = (part.split("=", 1)[1] for part in query.split("&"))

    write_request = _local_request(tmp_path, body=b"video-bytes", signing_secret=secret)
    response = asyncio.run(
        put_local_object(
            "raw-uploads", "workspaces/one/raw/video.mp4", write_request, exp=exp, sig=sig
        )
    )
    assert response.status_code == 204

    read_query = sign_local_object_query(
        secret=secret,
        method="GET",
        bucket="raw-uploads",
        path="workspaces/one/raw/video.mp4",
        expires_in_seconds=300,
    )
    read_exp, read_sig = (part.split("=", 1)[1] for part in read_query.split("&"))
    read_request = _local_request(tmp_path, signing_secret=secret)
    file_response = asyncio.run(
        get_local_object(
            "raw-uploads", "workspaces/one/raw/video.mp4", read_request, exp=read_exp, sig=read_sig
        )
    )
    assert Path(file_response.path).read_bytes() == b"video-bytes"


def test_local_storage_route_rejects_unsigned_tampered_and_expired_links(tmp_path: Path) -> None:
    from app.services.local_storage_signing import (
        compute_local_object_signature,
        sign_local_object_query,
    )

    secret = "link-secret"

    # Unsigned.
    unsigned = _local_request(tmp_path, signing_secret=secret)
    with pytest.raises(ApiError) as unsigned_error:
        asyncio.run(get_local_object("raw-uploads", "a/b.mp4", unsigned, exp=None, sig=None))
    assert unsigned_error.value.status_code == 403
    assert unsigned_error.value.code == "local_storage_link_invalid"

    # Tampered signature.
    query = sign_local_object_query(
        secret=secret, method="GET", bucket="raw-uploads", path="a/b.mp4", expires_in_seconds=300
    )
    exp, sig = (part.split("=", 1)[1] for part in query.split("&"))
    # Flip the last character to a different one (replacing it with a fixed
    # character is a no-op whenever the real signature already ends with it).
    tampered_sig = sig[:-1] + ("1" if sig.endswith("0") else "0")
    tampered = _local_request(tmp_path, signing_secret=secret)
    with pytest.raises(ApiError) as tampered_error:
        asyncio.run(
            get_local_object("raw-uploads", "a/b.mp4", tampered, exp=exp, sig=tampered_sig)
        )
    assert tampered_error.value.status_code == 403

    # A signature for a different object path must not open this one.
    other = sign_local_object_query(
        secret=secret,
        method="GET",
        bucket="raw-uploads",
        path="a/other.mp4",
        expires_in_seconds=300,
    )
    other_exp, other_sig = (part.split("=", 1)[1] for part in other.split("&"))
    crossed = _local_request(tmp_path, signing_secret=secret)
    with pytest.raises(ApiError) as crossed_error:
        asyncio.run(
            get_local_object("raw-uploads", "a/b.mp4", crossed, exp=other_exp, sig=other_sig)
        )
    assert crossed_error.value.status_code == 403

    # Expired.
    expired_exp = 1
    expired_sig = compute_local_object_signature(
        secret=secret, method="GET", bucket="raw-uploads", path="a/b.mp4", expires_at=expired_exp
    )
    expired = _local_request(tmp_path, signing_secret=secret)
    with pytest.raises(ApiError) as expired_error:
        asyncio.run(
            get_local_object(
                "raw-uploads", "a/b.mp4", expired, exp=str(expired_exp), sig=expired_sig
            )
        )
    assert expired_error.value.status_code == 403


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
