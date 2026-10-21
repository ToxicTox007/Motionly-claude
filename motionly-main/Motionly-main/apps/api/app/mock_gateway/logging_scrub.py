"""Log scrubbing for the mock payment gateway.

Full card numbers and OTPs must never be written to logs. This filter redacts
any 12-19 digit run (card PANs, with optional spaces/dashes) and obvious
``card_number``/``otp`` field values from every log record the gateway emits,
including request logs.
"""

from __future__ import annotations

import logging
import re

# A 12-19 digit run, optionally grouped by spaces or dashes (card PAN).
_PAN = re.compile(r"\b(?:\d[ -]?){11,18}\d\b")
# Explicit sensitive JSON/field fragments like "card_number":"4111..." or otp=123456.
_FIELD = re.compile(
    r'("?(?:card_number|pan|cvv|cvc|otp)"?\s*[:=]\s*"?)([^",}\s]+)',
    re.IGNORECASE,
)

REDACTION = "[redacted]"


def scrub(text: str) -> str:
    text = _FIELD.sub(lambda m: m.group(1) + REDACTION, text)
    return _PAN.sub(REDACTION, text)


class CardScrubbingFilter(logging.Filter):
    """Redacts card PANs/OTPs from log messages and string args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: scrub(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    scrub(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        return True


def install_scrubbing(logger: logging.Logger | None = None) -> None:
    """Attach the scrubbing filter to a logger and the root handlers."""
    target = logger or logging.getLogger()
    scrubber = CardScrubbingFilter()
    target.addFilter(scrubber)
    for handler in target.handlers:
        handler.addFilter(scrubber)
