"""Tests for ``aws_s3_objects``: S3 helpers signed with SigV4, no real network.

All HTTP is driven through ``httpx.MockTransport`` so no socket is opened (the
test guard blocks real network). Credentials are a tiny fake exposing
``resolve()`` like the real :class:`IamCredentialChain`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.services import aws_s3_objects
from app.services.aws_s3_objects import (
    S3Location,
    S3ObjectStore,
    parse_s3_uri,
    safe_object_key,
    validate_bucket_name,
)
from app.services.aws_sigv4 import SigV4Credentials
from app.services.provider_resilience import ProviderCallFailure

FIXED_CLOCK = lambda: datetime(2013, 5, 24, 0, 0, 0, tzinfo=UTC)  # noqa: E731

LIST_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    b"<Name>bucket</Name><Prefix>out/</Prefix><KeyCount>2</KeyCount>"
    b"<Contents><Key>out/a.mp4</Key><Size>1</Size></Contents>"
    b"<Contents><Key>out/output.mp4</Key><Size>2</Size></Contents>"
    b"</ListBucketResult>"
)


class _FakeCredentials:
    def __init__(self, creds: SigV4Credentials | None, *, raises: bool = False) -> None:
        self._creds = creds
        self._raises = raises
        self.calls = 0

    def resolve(self) -> SigV4Credentials:
        self.calls += 1
        if self._raises:
            raise RuntimeError("no IAM credentials found")
        assert self._creds is not None
        return self._creds


def _creds() -> _FakeCredentials:
    return _FakeCredentials(
        SigV4Credentials(
            access_key_id="AKIAIOSFODNN7EXAMPLE",
            secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )
    )


def _store(handler, *, credentials=None) -> S3ObjectStore:
    return S3ObjectStore(
        region="ap-south-1",
        bucket="motionly-staging",
        credentials=credentials or _creds(),
        provider="aws_bedrock",
        transport=httpx.MockTransport(handler),
        clock=FIXED_CLOCK,
    )


# --- pure helpers ----------------------------------------------------------
def test_parse_s3_uri_splits_bucket_and_key() -> None:
    loc = parse_s3_uri("s3://my-bucket/path/to/object.json")
    assert loc == S3Location(bucket="my-bucket", key="path/to/object.json")
    assert loc.uri == "s3://my-bucket/path/to/object.json"


def test_parse_s3_uri_allows_empty_key() -> None:
    loc = parse_s3_uri("s3://my-bucket")
    assert loc.bucket == "my-bucket"
    assert loc.key == ""
    assert loc.uri == "s3://my-bucket"


@pytest.mark.parametrize("bad", ["http://x/y", "my-bucket/key", "s3://"])
def test_parse_s3_uri_rejects_bad_input(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_s3_uri(bad)


def test_validate_bucket_name_accepts_valid() -> None:
    assert validate_bucket_name("motionly-staging") == []
    assert validate_bucket_name("abc") == []


@pytest.mark.parametrize(
    "bucket",
    ["ab", "-leading", "trailing-", "has.dot", "UpperCase", "a" * 64],
)
def test_validate_bucket_name_reports_problems(bucket: str) -> None:
    assert validate_bucket_name(bucket) != []


def test_safe_object_key_joins_and_normalises() -> None:
    assert safe_object_key("prefix", "input", "file.wav") == "prefix/input/file.wav"
    assert safe_object_key("", "a", "", "b") == "a/b"
    assert safe_object_key("a/b/", "/c") == "a/b/c"


@pytest.mark.parametrize(
    "parts",
    [("..", "x"), ("a/../b",), ("bad space",), ("semi;colon",)],
)
def test_safe_object_key_rejects_traversal_and_unsafe(parts) -> None:
    with pytest.raises(ValueError):
        safe_object_key(*parts)


# --- signed requests through MockTransport ---------------------------------
def test_put_object_bytes_signs_and_targets_virtual_host() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("authorization")
        seen["ctype"] = request.headers.get("content-type")
        seen["body"] = request.content
        return httpx.Response(200)

    store = _store(handler)
    store.put_object_bytes("prefix/input/file.wav", b"audio", content_type="audio/wav")

    assert seen["method"] == "PUT"
    assert seen["url"] == (
        "https://motionly-staging.s3.ap-south-1.amazonaws.com/prefix/input/file.wav"
    )
    assert seen["ctype"] == "audio/wav"
    assert seen["body"] == b"audio"
    assert str(seen["auth"]).startswith("AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/")


def test_read_object_bytes_returns_body_and_enforces_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"result-json-bytes")

    store = _store(handler)
    assert store.read_object_bytes("out/result.json", max_bytes=1024) == b"result-json-bytes"

    with pytest.raises(ProviderCallFailure) as exc:
        store.read_object_bytes("out/result.json", max_bytes=4)
    assert exc.value.error_info.code == "provider_malformed_response"


def test_read_object_bytes_maps_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"denied")

    store = _store(handler)
    with pytest.raises(ProviderCallFailure) as exc:
        store.read_object_bytes("out/x", max_bytes=1024)
    assert exc.value.error_info.code == "provider_auth_failed"


def test_remove_object_treats_404_as_success() -> None:
    statuses = iter([204, 404])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(next(statuses))

    store = _store(handler)
    store.remove_object("out/a")  # 204
    store.remove_object("out/b")  # 404 -> no raise


def test_remove_object_raises_on_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    store = _store(handler)
    with pytest.raises(ProviderCallFailure):
        store.remove_object("out/a")


def test_list_object_keys_parses_listobjectsv2_xml() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("list-type") == "2"
        assert request.url.params.get("prefix") == "out/"
        return httpx.Response(200, content=LIST_XML)

    store = _store(handler)
    keys = store.list_object_keys("out/", max_keys=10)
    assert keys == ["out/a.mp4", "out/output.mp4"]


def test_list_object_keys_rejects_oversized_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=LIST_XML)

    store = _store(handler)
    with pytest.raises(ProviderCallFailure) as exc:
        store.list_object_keys("out/", max_bytes=8)
    assert exc.value.error_info.code == "provider_malformed_response"


def test_list_object_keys_rejects_bad_xml() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<not-closed")

    store = _store(handler)
    with pytest.raises(ProviderCallFailure) as exc:
        store.list_object_keys("out/")
    assert exc.value.error_info.code == "provider_malformed_response"


def test_presigned_get_url_is_https_and_signed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("presign must not make a network call")

    store = _store(handler)
    url = store.presigned_get_url("out/output.mp4", expires_s=900)
    assert url.startswith(
        "https://motionly-staging.s3.ap-south-1.amazonaws.com/out/output.mp4?"
    )
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    assert "X-Amz-Expires=900" in url
    assert "X-Amz-Signature=" in url


def test_credential_resolution_failure_maps_to_auth_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not send when credentials cannot resolve")

    store = _store(handler, credentials=_FakeCredentials(None, raises=True))
    with pytest.raises(ProviderCallFailure) as exc:
        store.put_object_bytes("k", b"d", content_type="application/json")
    assert exc.value.error_info.code == "provider_auth_failed"
    assert exc.value.error_info.fallback_eligible is True


def test_s3_uri_helper_builds_bucket_uri() -> None:
    store = _store(lambda request: httpx.Response(200))
    assert store.s3_uri("out/result.json") == "s3://motionly-staging/out/result.json"
    assert store.host == "motionly-staging.s3.ap-south-1.amazonaws.com"
    assert store.bucket == "motionly-staging"
    assert store.region == "ap-south-1"


def test_module_exports_service_name() -> None:
    assert aws_s3_objects.S3_SERVICE == "s3"
