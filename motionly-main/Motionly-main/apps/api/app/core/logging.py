import logging
import re
import sys
import threading
from typing import Any

from pythonjsonlogger import jsonlogger

# ---------------------------------------------------------------------------
# Process-wide registry of exact secret values (plan Task 5, Section 2.4
# "Resolved values are registered with the generic redactor before use" and
# invariant 5 "Secret values ... never logged").
#
# Resolved inference secrets (file:/bundle: refs, OAuth access tokens) register
# their exact value here. The redaction filter then replaces any occurrence of
# a registered value — in a message, args, extra field or exception text — with
# ``SECRET_VALUE_MARKER``. This complements the shape-based rules below: those
# catch known credential *shapes*; this catches the exact strings we resolved,
# whatever their shape.
#
# Values shorter than ``_MIN_REGISTERED_SECRET_LEN`` are ignored for redaction
# to avoid over-redacting common short substrings (a 3-character token would
# otherwise blank out unrelated log text). Such a short value is still never
# logged deliberately — callers wrap secrets in a redacted container and never
# place a raw secret in a log record — but we do not substring-match it.
# ---------------------------------------------------------------------------

SECRET_VALUE_MARKER = "[REDACTED_SECRET]"
_MIN_REGISTERED_SECRET_LEN = 8

_registered_secret_values: set[str] = set()
_registered_secret_lock = threading.Lock()


def register_secret_value(value: str) -> None:
    """Register an exact secret value so the redactor masks it everywhere.

    Idempotent and thread-safe. Values shorter than
    ``_MIN_REGISTERED_SECRET_LEN`` are not substring-matched (see module note),
    but passing one is harmless. A non-string or empty value is ignored.
    """
    if not isinstance(value, str) or len(value) < _MIN_REGISTERED_SECRET_LEN:
        return
    with _registered_secret_lock:
        _registered_secret_values.add(value)


def registered_secret_values() -> frozenset[str]:
    """A snapshot of the registered secret values (for tests/diagnostics only)."""
    with _registered_secret_lock:
        return frozenset(_registered_secret_values)


def _redact_registered_values(text: str) -> str:
    """Replace every registered exact secret value with the fixed marker.

    Longest values first so a secret that is a superstring of another is masked
    whole rather than leaving a tail.
    """
    if not _registered_secret_values:
        return text
    with _registered_secret_lock:
        values = sorted(_registered_secret_values, key=len, reverse=True)
    for value in values:
        if value in text:
            text = text.replace(value, SECRET_VALUE_MARKER)
    return text


# Patterns to redact
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.\~\+\/]+=*")
_URL_PARAM_RE = re.compile(
    r'(?i)(token|signature|sig|expires|pass|password|key|api_key|access_token|refresh_token|apikey|secret)=[^&"\'\s#]+'
)
_JSON_KEY_RE = re.compile(
    r'(?i)(["\'](?:api_key|token|secret|access_token|refresh_token|signature|authorization|password|apikey|service_role_key)["\']\s*:\s*)(["\'])[^\2]*?\2'
)
_HEADER_AUTH_RE = re.compile(r'(?i)(["\']authorization["\']\s*:\s*)(["\'])[^\2]*?\2')


# Credentials that appear bare in free text (exception messages, response
# bodies, URLs). Applied before the generic rules below so they cannot be
# half-redacted. Mirrored in the worker (apps/worker/src/log-redaction.mjs).
# A JWT: Supabase access tokens, the anon/service-role/worker keys.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
# Motionly API keys (mtly_ + 43 chars) and Supabase secret keys.
_MOTIONLY_KEY_RE = re.compile(r"\bmtly_[A-Za-z0-9_-]{16,}")
_SUPABASE_SECRET_RE = re.compile(r"\bsb_secret_[A-Za-z0-9_-]{8,}")
# Passwords in connection URLs: redis://:pw@host, postgres://user:pw@host.
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]*):[^\s@/]+@")
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
    re.DOTALL,
)


def redact_string(text: str) -> str:
    if not isinstance(text, str):
        return text

    # 0a. Registered exact secret values (resolved inference secrets, OAuth
    # access tokens). Applied first so an exact value is masked whole before any
    # shape rule can half-redact it.
    text = _redact_registered_values(text)

    # 0. Bare credential shapes, wherever they appear.
    text = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", text)
    text = _JWT_RE.sub("[REDACTED_JWT]", text)
    text = _MOTIONLY_KEY_RE.sub("[REDACTED_API_KEY]", text)
    text = _SUPABASE_SECRET_RE.sub("[REDACTED_SUPABASE_KEY]", text)
    text = _URL_USERINFO_RE.sub(r"\1:[REDACTED]@", text)
    text = _AWS_ACCESS_KEY_RE.sub("[REDACTED_AWS_KEY]", text)

    # 1. Redact Authorization Bearer tokens
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)

    # 2. Redact Signed URL query params
    text = _URL_PARAM_RE.sub(r"\1=[REDACTED]", text)

    # 3. Redact JSON key values
    text = _JSON_KEY_RE.sub(r"\1\2[REDACTED]\2", text)
    text = _HEADER_AUTH_RE.sub(r"\1\2[REDACTED]\2", text)

    return text


def redact_data(data: Any) -> Any:
    """Recursively redact dicts, lists, and strings."""
    if isinstance(data, dict):
        redacted = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(
                s in k_lower
                for s in (
                    "api_key",
                    "token",
                    "secret",
                    "authorization",
                    "password",
                    "apikey",
                    "signature",
                    "bearer",
                    "credential",
                )
            ):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_data(v)
        return redacted
    elif isinstance(data, list):
        return [redact_data(item) for item in data]
    elif isinstance(data, str):
        return redact_string(data)
    else:
        return data


class RedactingJsonFormatter(jsonlogger.JsonFormatter):
    def format(self, record: logging.LogRecord) -> str:
        # First format the record normally using the parent method
        formatted = super().format(record)
        # Then redact the final serialized JSON string
        return redact_string(formatted)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingJsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
