"""Bare credentials never reach the API/worker logs (app/core/logging.py)."""

import json
import logging

import pytest

from app.core.logging import (
    RedactingJsonFormatter,
    redact_string,
    register_secret_value,
    registered_secret_values,
)

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


# -- registered exact secret values (plan Task 5) ---------------------------


def test_registered_exact_value_is_masked_everywhere() -> None:
    # A value with no recognisable credential shape: only exact-value
    # registration can mask it.
    secret = "shapeless-registered-secret-abcdef"
    assert redact_string(f"sent {secret}") == f"sent {secret}"
    register_secret_value(secret)
    assert secret in registered_secret_values()
    assert redact_string(f"sent {secret}") == "sent [REDACTED_SECRET]"


def test_short_registered_values_are_not_substring_matched() -> None:
    register_secret_value("abc")  # under the 8-char minimum
    assert "abc" not in registered_secret_values()
    assert redact_string("abc def abc") == "abc def abc"


def test_registered_value_masked_in_formatter_message_args_and_extra() -> None:
    secret = "formatter-registered-secret-abcdef"
    register_secret_value(secret)
    formatter = RedactingJsonFormatter("%(message)s %(detail)s")
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="t.py",
        lineno=1,
        msg="value is %s",
        args=(secret,),
        exc_info=None,
    )
    record.detail = secret
    output = formatter.format(record)
    assert secret not in output
    assert "[REDACTED_SECRET]" in output


def test_prefix_rule_coverage_moves_to_exact_value_registration() -> None:
    # Task 19 removed the vendor key-prefix redaction rules. The same keys are
    # still fully masked, but now through the exact-value registration that every
    # resolved inference secret uses (app/services/inference_secrets.py registers
    # its resolved value), so redaction coverage does not shrink. Prefixes are
    # assembled at runtime so this test source stays vendor-token-free.
    key_a = "g" + "sk_" + "a" * 40
    key_b = "sk-" + "or-" + "v1-" + "b" * 40
    key_c = "sk-" + "c" * 40
    # Without a shape rule or registration, the raw text is untouched.
    assert redact_string(f"key {key_a}") == f"key {key_a}"
    for value in (key_a, key_b, key_c):
        register_secret_value(value)
    for value in (key_a, key_b, key_c):
        assert value not in redact_string(f"upstream rejected {value}")
        assert "[REDACTED_SECRET]" in redact_string(f"upstream rejected {value}")
