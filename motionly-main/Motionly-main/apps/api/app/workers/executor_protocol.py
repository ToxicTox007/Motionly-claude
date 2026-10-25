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
