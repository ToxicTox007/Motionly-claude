"""AWS Signature Version 4 signing, standard library only (plan Task 25).

Revision 5 forbids a new dependency for Bedrock, so this module implements the
SigV4 "Authorization header" flow with ``hashlib`` and ``hmac`` alone — no
``botocore``. It produces the four SigV4 artifacts the AWS test suite pins:

1. the **canonical request**,
2. the **string to sign**,
3. the derived **signing key**, and
4. the hex **signature** and the ``Authorization`` header value.

The implementation is tested against AWS's published SigV4 test-suite vectors
(the ``get-vanilla`` worked example: credentials ``AKIDEXAMPLE`` /
``wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY``, region ``us-east-1``, service
``service``, date ``20150830T123600Z``). See
``apps/api/tests/test_aws_sigv4.py`` for the cited vectors.

The module is pure: it reads no environment, resolves no credential and makes no
network call. Credentials are passed in explicitly by the caller.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

SIGV4_ALGORITHM = "AWS4-HMAC-SHA256"
# The SHA-256 of an empty payload, used for bodyless requests.
EMPTY_PAYLOAD_HASH = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class SigV4Credentials:
    """IAM credentials used to sign one request.

    ``session_token`` is present for temporary (STS/role) credentials and adds
    the ``x-amz-security-token`` header, which is part of the signed headers.
    """

    access_key_id: str
    secret_access_key: str
    session_token: str | None = None


@dataclass(frozen=True)
class CanonicalRequest:
    """The intermediate SigV4 artifacts, exposed so each stage is testable."""

    canonical_request: str
    signed_headers: str
    hashed_canonical_request: str


def _uri_encode(value: str, *, is_path: bool) -> str:
    # AWS keeps '/' unencoded in the path, encodes it elsewhere; '~' stays.
    safe = "/~" if is_path else "~"
    return quote(value, safe=safe)


def canonical_uri(path: str) -> str:
    """Canonicalise a request path (RFC 3986), keeping ``/`` segments."""
    if not path:
        return "/"
    return _uri_encode(path, is_path=True)


def canonical_query_string(query: Mapping[str, str] | None) -> str:
    """Build the canonical query string: sorted, each key and value encoded."""
    if not query:
        return ""
    pairs = sorted(
        (_uri_encode(key, is_path=False), _uri_encode(value, is_path=False))
        for key, value in query.items()
    )
    return "&".join(f"{key}={value}" for key, value in pairs)


def _canonical_headers(headers: Mapping[str, str]) -> tuple[str, str]:
    """Return the canonical header block and the signed-header list."""
    normalized: dict[str, str] = {}
    for name, value in headers.items():
        key = name.strip().lower()
        # Collapse internal whitespace runs in the value (AWS rule).
        normalized[key] = " ".join(str(value).strip().split())
    ordered = sorted(normalized.items())
    canonical = "".join(f"{name}:{value}\n" for name, value in ordered)
    signed = ";".join(name for name, _ in ordered)
    return canonical, signed


def build_canonical_request(
    *,
    method: str,
    path: str,
    query: Mapping[str, str] | None,
    headers: Mapping[str, str],
    payload_hash: str,
) -> CanonicalRequest:
    """Build the SigV4 canonical request (step 1) and its SHA-256 (step 2 input)."""
    canonical_headers, signed_headers = _canonical_headers(headers)
    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri(path),
            canonical_query_string(query),
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    hashed = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    return CanonicalRequest(
        canonical_request=canonical_request,
        signed_headers=signed_headers,
        hashed_canonical_request=hashed,
    )


def credential_scope(*, date_stamp: str, region: str, service: str) -> str:
    """The SigV4 credential scope ``<date>/<region>/<service>/aws4_request``."""
    return f"{date_stamp}/{region}/{service}/aws4_request"


def build_string_to_sign(
    *,
    amz_date: str,
    scope: str,
    hashed_canonical_request: str,
) -> str:
    """Build the SigV4 string to sign (step 3)."""
    return "\n".join([SIGV4_ALGORITHM, amz_date, scope, hashed_canonical_request])


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def derive_signing_key(
    *,
    secret_access_key: str,
    date_stamp: str,
    region: str,
    service: str,
) -> bytes:
    """Derive the SigV4 signing key (step 4)."""
    k_date = _hmac(f"AWS4{secret_access_key}".encode(), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def calculate_signature(*, signing_key: bytes, string_to_sign: str) -> str:
    """Calculate the hex SigV4 signature (step 5)."""
    return hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True)
class SignedRequest:
    """The headers to send, plus the intermediate artifacts for diagnostics."""

    headers: Mapping[str, str]
    authorization: str
    signature: str
    canonical_request: str
    string_to_sign: str


def sign_request(
    *,
    credentials: SigV4Credentials,
    method: str,
    host: str,
    path: str,
    region: str,
    service: str,
    amz_date: str,
    query: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    payload: bytes = b"",
) -> SignedRequest:
    """Sign a request and return the headers (including ``Authorization``).

    ``amz_date`` is the ISO basic timestamp ``YYYYMMDDThhmmssZ``; the date stamp
    is derived from its first eight characters. The caller supplies the clock, so
    this stays pure and deterministic. ``host`` and ``x-amz-date`` are always
    signed; ``x-amz-security-token`` is added and signed for temporary creds.
    """
    date_stamp = amz_date[:8]
    payload_hash = hashlib.sha256(payload).hexdigest() if payload else EMPTY_PAYLOAD_HASH

    sign_headers: dict[str, str] = {}
    for name, value in (headers or {}).items():
        sign_headers[name.strip().lower()] = value
    sign_headers["host"] = host
    sign_headers["x-amz-date"] = amz_date
    sign_headers["x-amz-content-sha256"] = payload_hash
    if credentials.session_token:
        sign_headers["x-amz-security-token"] = credentials.session_token

    canonical = build_canonical_request(
        method=method,
        path=path,
        query=query,
        headers=sign_headers,
        payload_hash=payload_hash,
    )
    scope = credential_scope(date_stamp=date_stamp, region=region, service=service)
    string_to_sign = build_string_to_sign(
        amz_date=amz_date,
        scope=scope,
        hashed_canonical_request=canonical.hashed_canonical_request,
    )
    signing_key = derive_signing_key(
        secret_access_key=credentials.secret_access_key,
        date_stamp=date_stamp,
        region=region,
        service=service,
    )
    signature = calculate_signature(signing_key=signing_key, string_to_sign=string_to_sign)
    authorization = (
        f"{SIGV4_ALGORITHM} "
        f"Credential={credentials.access_key_id}/{scope}, "
        f"SignedHeaders={canonical.signed_headers}, "
        f"Signature={signature}"
    )
    out_headers = dict(sign_headers)
    out_headers["authorization"] = authorization
    return SignedRequest(
        headers=out_headers,
        authorization=authorization,
        signature=signature,
        canonical_request=canonical.canonical_request,
        string_to_sign=string_to_sign,
    )


# ---------------------------------------------------------------------------
# SigV4 query-string (presigned URL) authentication.
# ---------------------------------------------------------------------------
# The constant payload string used by presigned URLs: the body content is not
# known when the URL is minted, so the hash is replaced by this literal.
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"


def presign_url(
    *,
    credentials: SigV4Credentials,
    method: str,
    host: str,
    path: str,
    region: str,
    service: str,
    amz_date: str,
    expires_s: int,
    query: Mapping[str, str] | None = None,
) -> str:
    """Build a SigV4 query-string (presigned) URL and return it as ``https://``.

    This is the "Authenticating Requests: Using Query Parameters" flow. Unlike
    :func:`sign_request` (which signs ``Authorization`` as a header), the auth
    information rides in the query string so the URL is self-contained:

    * ``X-Amz-Algorithm``   = ``AWS4-HMAC-SHA256``
    * ``X-Amz-Credential``  = ``<access-key>/<scope>``
    * ``X-Amz-Date``        = ``amz_date``
    * ``X-Amz-Expires``     = ``expires_s``
    * ``X-Amz-SignedHeaders`` = ``host`` (only the host header is signed)
    * ``X-Amz-Security-Token`` = session token, when temporary credentials are
      used (signed, per the S3 presign spec)

    The canonical request uses the constant ``UNSIGNED-PAYLOAD`` in place of a
    body hash, and the canonical query string contains every ``X-Amz-*``
    parameter except ``X-Amz-Signature``. The result is verified against AWS's
    published S3 presign worked example in ``tests/test_aws_sigv4.py``.

    This function is pure: it reads no environment and makes no network call.
    ``amz_date`` is the ISO basic timestamp ``YYYYMMDDThhmmssZ``; the date stamp
    is its first eight characters. Any caller-supplied ``query`` entries are
    merged into the signed query string (``X-Amz-Signature`` is always excluded).
    """
    date_stamp = amz_date[:8]
    scope = credential_scope(date_stamp=date_stamp, region=region, service=service)

    # Only the host header participates in a presigned URL signature.
    signed_headers = "host"

    presign_query: dict[str, str] = dict(query or {})
    presign_query["X-Amz-Algorithm"] = SIGV4_ALGORITHM
    presign_query["X-Amz-Credential"] = f"{credentials.access_key_id}/{scope}"
    presign_query["X-Amz-Date"] = amz_date
    presign_query["X-Amz-Expires"] = str(int(expires_s))
    presign_query["X-Amz-SignedHeaders"] = signed_headers
    if credentials.session_token:
        presign_query["X-Amz-Security-Token"] = credentials.session_token
    # X-Amz-Signature is never part of the canonical query string.
    presign_query.pop("X-Amz-Signature", None)

    canonical_query = canonical_query_string(presign_query)
    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri(path),
            canonical_query,
            f"host:{host}\n",
            signed_headers,
            UNSIGNED_PAYLOAD,
        ]
    )
    hashed_canonical_request = hashlib.sha256(
        canonical_request.encode("utf-8")
    ).hexdigest()
    string_to_sign = build_string_to_sign(
        amz_date=amz_date,
        scope=scope,
        hashed_canonical_request=hashed_canonical_request,
    )
    signing_key = derive_signing_key(
        secret_access_key=credentials.secret_access_key,
        date_stamp=date_stamp,
        region=region,
        service=service,
    )
    signature = calculate_signature(
        signing_key=signing_key, string_to_sign=string_to_sign
    )
    # The signature is appended last; it is not part of the signed query string.
    final_query = f"{canonical_query}&X-Amz-Signature={signature}"
    return f"https://{host}{canonical_uri(path)}?{final_query}"
