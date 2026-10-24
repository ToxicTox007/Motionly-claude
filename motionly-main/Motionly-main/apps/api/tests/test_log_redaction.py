"""Bare credentials never reach the API/worker logs (app/core/logging.py)."""

import json
import logging

import pytest

from app.core.logging import RedactingJsonFormatter, redact_string

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJlc2lnbmF0dXJl"
)
PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"upstream rejected key {JWT}", "upstream rejected key [REDACTED_JWT]"),
        (f"auth failed for {JWT}, retrying", "auth failed for [REDACTED_JWT], retrying"),
        ("redis://:hunter2pass@redis:6379/0", "redis://:[REDACTED]@redis:6379/0"),
        ("postgres://app:s3cret@db:5432/motionly", "postgres://app:[REDACTED]@db:5432/motionly"),
        ("key mtly_" + "a" * 43, "key [REDACTED_API_KEY]"),
        ("sb_secret_abcdefgh12345678", "[REDACTED_SUPABASE_KEY]"),
        ("AKIAABCDEFGHIJKLMNOP leaked", "[REDACTED_AWS_KEY] leaked"),
        (f"pem {PRIVATE_KEY} end", "pem [REDACTED_PRIVATE_KEY] end"),
    ],
)
def test_bare_credentials_are_redacted(text: str, expected: str) -> None:
    assert redact_string(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "render 3fa85f64-5717-4562-b3fc-2c963f66afa6 completed",
        "GET https://api.example.com/projects?limit=20",
        "eyJhbGciOiJIUzI1NiJ9 is only a header",
        "user@example.com signed in",
    ],
)
def test_ordinary_text_is_untouched(text: str) -> None:
    assert redact_string(text) == text


def test_formatter_redacts_exception_text_and_keeps_valid_json() -> None:
    formatter = RedactingJsonFormatter("%(message)s")
    try:
        raise RuntimeError(f"Supabase rejected {JWT} for redis://:pw999999@redis:6379")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="provider call failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    output = formatter.format(record)
    assert JWT not in output
    assert "pw999999" not in output
    parsed = json.loads(output)
    assert "[REDACTED_JWT]" in parsed["exc_info"]
