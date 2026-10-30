"""The private reply channel for persistent Python executors.

The Node worker talks to ``python -m app.workers.<job> --serve`` over stdin and
stdout, one JSON object per line. Anything else printed to stdout — by the app,
by a model library, or by a native extension writing to file descriptor 1 —
would land in that channel, and a stray line starting with ``{`` or ``[``
makes the worker tear the executor down (python_protocol_invalid).

``open_protocol_channel()`` moves the reply channel out of reach: it duplicates
the real stdout to a private descriptor for replies, then points descriptor 1
(and ``sys.stdout``) at stderr. Library output then goes to the worker's
stderr passthrough as plain log lines, and only ``send`` writes replies.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, TextIO


class ProtocolChannel:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def send(self, message: dict[str, Any]) -> None:
        self._stream.write(json.dumps(message, default=str) + "\n")
        self._stream.flush()


def open_protocol_channel() -> ProtocolChannel:
    """Reserve the process's stdout for protocol replies (call once, first)."""
    sys.stdout.flush()
    reply_fd = os.dup(1)
    # Descriptor-level redirect, so C extensions and child processes that
    # inherit fd 1 write to stderr too, not only Python-level print().
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return ProtocolChannel(os.fdopen(reply_fd, "w", buffering=1, encoding="utf-8"))


# Failures another BullMQ attempt cannot fix (audit WJ-17). The worker discards
# these jobs instead of spending its retries (and GPU/provider time) on them.
NON_RETRYABLE_ERROR_CODES = frozenset(
    {
        "edit_planner_retry_limit_exceeded",
        "inference_registry_mismatch",
        "matte_not_found",
        "matte_transition_conflict",
        # Another attempt owns the render; retrying would take it back (DB-11).
        "render_attempt_superseded",
        "queue_job_payload_invalid",
    }
)
# 4xx statuses that can still succeed later (timeouts, conflicts on state that
# is still settling, throttling).
_RETRYABLE_CLIENT_STATUSES = frozenset({408, 409, 423, 425, 429})


def is_retryable_error(exc: BaseException) -> bool:
    from app.api.errors import ApiError

    if isinstance(exc, ApiError):
        if exc.code in NON_RETRYABLE_ERROR_CODES:
            return False
        status = int(getattr(exc, "status_code", 500) or 500)
        return not (400 <= status < 500 and status not in _RETRYABLE_CLIENT_STATUSES)
    # A malformed job envelope (missing/invalid ids) never becomes valid.
    return not isinstance(exc, (KeyError, ValueError, TypeError))


def error_reply(request_id: object, exc: BaseException) -> dict[str, Any]:
    """The protocol error reply: redacted message, stable code, retry hint."""
    from app.api.errors import ApiError
    from app.core.logging import redact_string

    if isinstance(exc, ApiError):
        message, code = exc.message, exc.code
    else:
        message, code = redact_string(str(exc)), type(exc).__name__
    return {
        "id": request_id,
        "error": message,
        "error_code": code,
        "retryable": is_retryable_error(exc),
    }
