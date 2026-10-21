"""Webhook signing for the mock payment gateway.

The signature scheme is documented in the package docstring. It is isolated
here (no FastAPI import) so the Task 23 receiver can import and reuse it.
"""

from __future__ import annotations

import hashlib
import hmac
import time

SIGNATURE_PREFIX = "sha256="


def signing_input(timestamp: int, raw_body: bytes) -> bytes:
    """The bytes that are HMAC'd: ``f"{timestamp}.{raw_body}"``."""
    return str(timestamp).encode("ascii") + b"." + raw_body


def compute_signature(secret: str, timestamp: int, raw_body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), signing_input(timestamp, raw_body), hashlib.sha256
    ).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_signature(
    secret: str,
    timestamp: int,
    raw_body: bytes,
    signature: str,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> bool:
    """Constant-time verify a signature, rejecting stale timestamps.

    The Task 23 receiver should call this (or re-implement the same scheme).
    """
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        return False
    expected = compute_signature(secret, timestamp, raw_body)
    return hmac.compare_digest(expected, signature)
