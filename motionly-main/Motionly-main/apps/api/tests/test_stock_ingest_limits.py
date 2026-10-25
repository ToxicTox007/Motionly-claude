"""Stock-video ingest is capped: an oversized provider file is refused, not stored."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.errors import ApiError
from app.schemas.broll import StockProviderKey
from app.services import stock_video
from app.services.stock_video import ensure_owned_stock_asset
from app.services.storage import StorageSigner

CHUNK = 1024 * 1024


class _Source:
    def __init__(self, *, chunks: int, content_length: int | None) -> None:
        self.chunks = chunks
        self.headers = {"content-type": "video/mp4"}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)
        self.yielded = 0

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, chunk_size: int):
        for _ in range(self.chunks):
            self.yielded += 1
            yield b"\0" * chunk_size


def _fake_stream(source: _Source):
    @contextmanager
    def stream(*_args, **_kwargs):
        yield source

    return stream


def _asset():
    return SimpleNamespace(
        provider=StockProviderKey.PEXELS,
        provider_asset_id="12345",
        raw_metadata={},
        download_url="https://videos.pexels.com/video-files/12345/clip.mp4",
        preview_video_url=None,
        workspace_id=uuid4(),
        project_id=uuid4(),
    )


class _Repository:
    def __init__(self) -> None:
        self.owned: list[dict] = []

    def mark_stock_asset_owned(self, **values):
        self.owned.append(values)
        return values


def _local_signer() -> StorageSigner:
    return StorageSigner(supabase_url=None, service_role_key=None)


def _ingest(tmp_path, signer=None):
    repository = _Repository()
    settings = SimpleNamespace(upload_storage_bucket="raw-uploads", media_work_dir=str(tmp_path))
    result = ensure_owned_stock_asset(
        asset=_asset(),
        repository=repository,
        settings=settings,
        storage_signer=signer or _local_signer(),
    )
    return result, repository


@pytest.fixture
def cap_two_mib(monkeypatch):
    monkeypatch.setenv("STOCK_ASSET_MAX_BYTES", str(2 * CHUNK))


def _stored_files(tmp_path):
    return [path for path in tmp_path.rglob("*") if path.is_file()]


def test_a_clip_within_the_cap_is_copied(monkeypatch, tmp_path, cap_two_mib) -> None:
    source = _Source(chunks=2, content_length=2 * CHUNK)
    monkeypatch.setattr(stock_video, "safe_http_stream", _fake_stream(source))

    _result, repository = _ingest(tmp_path)

    files = _stored_files(tmp_path)
    assert len(files) == 1 and files[0].stat().st_size == 2 * CHUNK
    assert len(repository.owned) == 1


def test_a_declared_oversize_clip_is_refused_before_any_bytes_are_read(
    monkeypatch, tmp_path, cap_two_mib
) -> None:
    source = _Source(chunks=5, content_length=5 * CHUNK)
    monkeypatch.setattr(stock_video, "safe_http_stream", _fake_stream(source))

    with pytest.raises(ApiError) as exc:
        _ingest(tmp_path)

    assert exc.value.status_code == 422
    assert exc.value.code == "stock_asset_too_large"
    assert source.yielded == 0
    assert _stored_files(tmp_path) == []


def test_an_undeclared_oversize_clip_is_cut_off_and_the_partial_copy_removed(
    monkeypatch, tmp_path, cap_two_mib
) -> None:
    source = _Source(chunks=50, content_length=None)
    monkeypatch.setattr(stock_video, "safe_http_stream", _fake_stream(source))

    with pytest.raises(ApiError) as exc:
        _ingest(tmp_path)

    assert exc.value.code == "stock_asset_too_large"
    assert source.yielded == 3  # stopped at the first chunk past the cap
    assert _stored_files(tmp_path) == []


def test_a_lying_content_length_does_not_bypass_the_cap(monkeypatch, tmp_path, cap_two_mib) -> None:
    source = _Source(chunks=10, content_length=1024)
    monkeypatch.setattr(stock_video, "safe_http_stream", _fake_stream(source))

    with pytest.raises(ApiError) as exc:
        _ingest(tmp_path)
    assert exc.value.code == "stock_asset_too_large"
    assert _stored_files(tmp_path) == []


def test_supabase_upload_is_cut_off_and_the_partial_object_deleted(
    monkeypatch, tmp_path, cap_two_mib
) -> None:
    source = _Source(chunks=50, content_length=None)
    monkeypatch.setattr(stock_video, "safe_http_stream", _fake_stream(source))
    uploaded_bytes = []
    deleted = []

    def fake_post(url, *, headers, content, timeout):
        for chunk in content:  # httpx consumes the iterator while sending
            uploaded_bytes.append(len(chunk))
        return httpx.Response(200)

    def fake_delete(url, *, headers, timeout):
        deleted.append(url)
        return httpx.Response(200)

    monkeypatch.setattr(stock_video.httpx, "post", fake_post)
    monkeypatch.setattr(stock_video.httpx, "delete", fake_delete)
    signer = StorageSigner(supabase_url="https://supabase.example.test", service_role_key="srk")

    with pytest.raises(ApiError) as exc:
        _ingest(tmp_path, signer=signer)

    assert exc.value.code == "stock_asset_too_large"
    assert sum(uploaded_bytes) <= 2 * CHUNK
    assert len(deleted) == 1 and "/storage/v1/object/raw-uploads/" in deleted[0]


def test_cap_setting_cannot_exceed_the_render_download_limit(monkeypatch) -> None:
    from app.services.worker_media import MAX_SOURCE_BYTES

    assert stock_video.DEFAULT_STOCK_ASSET_MAX_BYTES == MAX_SOURCE_BYTES
    for raw, expected in (
        ("1000", 1000),
        (str(10 * MAX_SOURCE_BYTES), MAX_SOURCE_BYTES),
        ("0", 1),
        ("nope", MAX_SOURCE_BYTES),
    ):
        monkeypatch.setenv("STOCK_ASSET_MAX_BYTES", raw)
        assert stock_video.stock_asset_max_bytes() == expected
