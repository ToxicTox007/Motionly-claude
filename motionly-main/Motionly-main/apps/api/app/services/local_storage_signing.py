"""Signed, expiring links for the development/CPU-stage local storage backend.

Local file storage serves bytes straight off disk through the ``/_local/storage``
routes. Without a signature anyone who can reach the API could read or overwrite
any object by guessing its path (B38). Every local link therefore carries an
expiry (``exp``) and an HMAC signature (``sig``) issued by the storage signer; the
route handlers refuse unsigned, tampered, or expired links with 403.

The signature is an HMAC-SHA256 over a canonical JSON array containing the
method, bucket, path and expiry. A download link can never be replayed as an
upload link and vice versa. Comparison is constant time
(``hmac.compare_digest`` via ``constant_time_token_matches``).
"""

from __future__ import annotations

import hmac
import json
import time
from hashlib import sha256

from app.core.security import constant_time_token_matches


class LocalStorageSignatureError(Exception):
    """Raised when a local-storage link lacks a valid, unexpired signature."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _canonical_message(*, method: str, bucket: str, path: str, expires_at: int) -> bytes:
    # JSON gives each signed field an unambiguous boundary. A colon-delimited
    # message allowed values such as bucket/path to produce the same signature.
    return json.dumps(
        [method.upper(), bucket, path, expires_at],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_local_object_signature(
    *,
    secret: str,
    method: str,
    bucket: str,
    path: str,
    expires_at: int,
) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        _canonical_message(method=method, bucket=bucket, path=path, expires_at=expires_at),
        sha256,
    ).hexdigest()


def sign_local_object_query(
    *,
    secret: str,
    method: str,
    bucket: str,
    path: str,
    expires_in_seconds: int,
    now: int | None = None,
) -> str:
    """Return the ``exp=…&sig=…`` query string for a local-storage link."""
    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + max(1, expires_in_seconds)
    signature = compute_local_object_signature(
        secret=secret, method=method, bucket=bucket, path=path, expires_at=expires_at
    )
    return f"exp={expires_at}&sig={signature}"


def verify_local_object_signature(
    *,
    secret: str,
    method: str,
    bucket: str,
    path: str,
    expires_at: str | int | None,
    signature: str | None,
    now: int | None = None,
) -> None:
    """Raise ``LocalStorageSignatureError`` unless the link is signed and unexpired."""
    if not signature or expires_at is None:
        raise LocalStorageSignatureError("missing_signature")
    try:
        expiry = int(expires_at)
    except (TypeError, ValueError) as exc:
        raise LocalStorageSignatureError("invalid_expiry") from exc

    expected = compute_local_object_signature(
        secret=secret, method=method, bucket=bucket, path=path, expires_at=expiry
    )
    if not constant_time_token_matches(signature, expected):
        raise LocalStorageSignatureError("invalid_signature")

    current = int(time.time()) if now is None else now
    if expiry < current:
        raise LocalStorageSignatureError("expired")
