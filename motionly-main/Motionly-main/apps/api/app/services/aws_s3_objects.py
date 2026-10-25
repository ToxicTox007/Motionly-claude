"""Amazon S3 object helpers for the Bedrock CPU-only deployment.

Bedrock Data Automation (audio transcription / diarization) and the Nova Reel
``async-invoke`` video flow stage their inputs and read their outputs through S3.
This module is the only place that speaks the S3 REST API, keeping the Bedrock
runners free of HTTP plumbing.

It is additive and dependency-free: it uses the standard library plus ``httpx``
and the existing :mod:`app.services.aws_sigv4` SigV4 signer. Every request is
signed with SigV4 (service ``s3``) against the virtual-hosted endpoint
``{bucket}.s3.{region}.amazonaws.com``. Credentials are resolved lazily through
an injected credential chain (anything exposing ``resolve() -> SigV4Credentials``)
and are never logged; a resolution failure maps to ``provider_auth_failed`` the
same way :meth:`AwsBedrockAdapter._send_model` does.

Responses are read into memory with an explicit byte cap so a hostile or
oversized object cannot exhaust the process. ``ListObjectsV2`` output is parsed
with :mod:`xml.etree.ElementTree` on a bounded body.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from xml.etree import ElementTree

import httpx

from app.services.aws_sigv4 import SigV4Credentials, presign_url, sign_request
from app.services.inference_http import (
    build_client,
    exception_failure,
    status_error_info,
)
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo

# S3 virtual-hosted endpoints use the ``s3`` SigV4 service name.
S3_SERVICE = "s3"

# A generous cap for a ListObjectsV2 XML body (keys only, no object bodies).
DEFAULT_LIST_MAX_BYTES = 4 * 1024 * 1024

# The ListObjectsV2 response namespace.
_S3_NS = "http://s3.amazonaws.com/doc/2006-03-01/"

# A bucket name allows lowercase letters, digits and hyphens (no dots, so the
# virtual-hosted TLS certificate always matches), 3..63 characters, and may not
# start or end with a hyphen.
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")

# Object keys are restricted to a safe, path-like alphabet so a caller-supplied
# segment can never escape its prefix or inject control characters.
_KEY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9/_.=-]+$")


@dataclass(frozen=True)
class S3Location:
    """A parsed ``s3://bucket/key`` location."""

    bucket: str
    key: str

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}" if self.key else f"s3://{self.bucket}"


def parse_s3_uri(uri: str) -> S3Location:
    """Parse ``s3://bucket/key`` into an :class:`S3Location`.

    The key may be empty (a bucket-or-prefix URI). Raises ``ValueError`` for a
    non-``s3://`` scheme or a missing bucket.
    """
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// URI: {uri!r}")
    remainder = uri[len("s3://") :]
    bucket, _, key = remainder.partition("/")
    if not bucket:
        raise ValueError(f"s3:// URI has no bucket: {uri!r}")
    return S3Location(bucket=bucket, key=key)


def validate_bucket_name(bucket: str) -> list[str]:
    """Return a list of human-readable problems with ``bucket`` (empty = valid).

    Enforces the subset of the S3 naming rules this deployment relies on: 3..63
    characters, only ``[a-z0-9-]`` (no dots), and no leading/trailing hyphen.
    """
    errors: list[str] = []
    if not (3 <= len(bucket) <= 63):
        errors.append("bucket name must be 3-63 characters")
    if re.search(r"[^a-z0-9-]", bucket):
        errors.append("bucket name may contain only lowercase letters, digits and '-'")
    if bucket.startswith("-") or bucket.endswith("-"):
        errors.append("bucket name may not start or end with '-'")
    return errors


def safe_object_key(*parts: str) -> str:
    """Join ``parts`` into an S3 key, rejecting traversal and unsafe characters.

    Each part is restricted to ``[A-Za-z0-9/_.=-]``; no part may contain ``..``;
    the resulting key may not begin with ``/``. Empty parts are skipped so a
    caller can pass an optional prefix uniformly.
    """
    cleaned: list[str] = []
    for part in parts:
        if part == "":
            continue
        if ".." in part:
            raise ValueError(f"object key part may not contain '..': {part!r}")
        if not _KEY_SEGMENT_RE.match(part):
            raise ValueError(f"object key part has unsafe characters: {part!r}")
        cleaned.append(part.strip("/"))
    key = "/".join(segment for segment in cleaned if segment)
    if key.startswith("/"):
        raise ValueError("object key may not start with '/'")
    return key


def _auth_failure(provider: str, exc: BaseException) -> ProviderCallFailure:
    """Credential resolution failure -> ``provider_auth_failed`` (parity with _send_model)."""
    return ProviderCallFailure(
        provider=provider,
        error_info=ProviderErrorInfo(
            "provider_auth_failed",
            False,
            fallback_eligible=True,
            circuit_worthy=True,
        ),
        message=str(exc) or None,
    )


class S3ObjectStore:
    """A minimal, SigV4-signed S3 client for one bucket in one region.

    ``credentials`` is any object exposing ``resolve() -> SigV4Credentials`` (the
    shared :class:`~app.services.aws_bedrock_adapter.IamCredentialChain`).
    ``transport`` is an injection point for ``httpx.MockTransport`` in tests;
    ``clock`` supplies the signing timestamp so signing stays deterministic.
    """

    def __init__(
        self,
        *,
        region: str,
        bucket: str,
        credentials: object,
        provider: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout_s: float = 60.0,
    ) -> None:
        self._region = region
        self._bucket = bucket
        self._credentials = credentials
        self._provider = provider
        self._transport = transport
        self._clock = clock
        self._timeout_s = timeout_s
        self._host = f"{bucket}.s3.{region}.amazonaws.com"

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def region(self) -> str:
        return self._region

    @property
    def host(self) -> str:
        return self._host

    def s3_uri(self, key: str) -> str:
        """The ``s3://bucket/key`` URI for ``key`` in this store's bucket."""
        return f"s3://{self._bucket}/{key}"

    # -- credential resolution ---------------------------------------------
    def _resolve_s3_credentials(self) -> SigV4Credentials:
        try:
            return self._credentials.resolve()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - mapped to a provider failure
            raise _auth_failure(self._provider, exc) from exc

    def _amz_date(self) -> str:
        return self._clock().strftime("%Y%m%dT%H%M%SZ")

    def _signed_headers(
        self,
        *,
        method: str,
        path: str,
        payload: bytes,
        query: Mapping[str, str] | None,
        extra_headers: Mapping[str, str] | None,
    ) -> Mapping[str, str]:
        credentials = self._resolve_s3_credentials()
        signed = sign_request(
            credentials=credentials,
            method=method,
            host=self._host,
            path=path,
            region=self._region,
            service=S3_SERVICE,
            amz_date=self._amz_date(),
            query=query,
            headers=dict(extra_headers or {}),
            payload=payload,
        )
        return dict(signed.headers)

    def _request(
        self,
        *,
        method: str,
        path: str,
        payload: bytes = b"",
        query: Mapping[str, str] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        headers = self._signed_headers(
            method=method,
            path=path,
            payload=payload,
            query=query,
            extra_headers=extra_headers,
        )
        url = f"https://{self._host}{path}"
        try:
            with build_client(
                timeout_s=self._timeout_s, transport=self._transport
            ) as client:
                return client.request(
                    method,
                    url,
                    headers=dict(headers),
                    params=dict(query or {}),
                    content=payload,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise exception_failure(self._provider, exc) from None

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=status_error_info(response.status_code, None),
            )

    # -- object operations --------------------------------------------------
    def put_object_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        """Upload ``data`` to ``key`` with the given ``Content-Type``."""
        response = self._request(
            method="PUT",
            path=f"/{key}",
            payload=data,
            extra_headers={"content-type": content_type},
        )
        self._raise_for_status(response)

    def read_object_bytes(self, key: str, *, max_bytes: int) -> bytes:
        """Download ``key``, failing (malformed) if the body exceeds ``max_bytes``."""
        response = self._request(method="GET", path=f"/{key}")
        self._raise_for_status(response)
        content = response.content
        if len(content) > max_bytes:
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=status_error_info(500, {"500": "malformed"}),
            )
        return content

    def remove_object(self, key: str) -> None:
        """Best-effort delete; a 2xx or a 404 (already gone) both count as success."""
        response = self._request(method="DELETE", path=f"/{key}")
        if response.status_code == 404:
            return
        self._raise_for_status(response)

    def list_object_keys(
        self, prefix: str, *, max_keys: int = 100, max_bytes: int = DEFAULT_LIST_MAX_BYTES
    ) -> list[str]:
        """List object keys under ``prefix`` via ListObjectsV2 (bounded XML)."""
        query = {
            "list-type": "2",
            "prefix": prefix,
            "max-keys": str(int(max_keys)),
        }
        response = self._request(method="GET", path="/", query=query)
        self._raise_for_status(response)
        content = response.content
        if len(content) > max_bytes:
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=status_error_info(500, {"500": "malformed"}),
            )
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=status_error_info(500, {"500": "malformed"}),
            ) from exc
        keys: list[str] = []
        for contents in root.findall(f"{{{_S3_NS}}}Contents"):
            key_el = contents.find(f"{{{_S3_NS}}}Key")
            if key_el is not None and key_el.text is not None:
                keys.append(key_el.text)
        return keys

    def presigned_get_url(self, key: str, *, expires_s: int) -> str:
        """A SigV4 presigned HTTPS GET URL for ``key`` (no network call)."""
        credentials = self._resolve_s3_credentials()
        return presign_url(
            credentials=credentials,
            method="GET",
            host=self._host,
            path=f"/{key}",
            region=self._region,
            service=S3_SERVICE,
            amz_date=self._amz_date(),
            expires_s=expires_s,
        )
