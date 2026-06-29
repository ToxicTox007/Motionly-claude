import logging
import re
import sys
from typing import Any

from pythonjsonlogger import jsonlogger

# Patterns to redact
_BEARER_RE = re.compile(r'(?i)(bearer\s+)[a-zA-Z0-9_\-\.\~\+\/]+=*')
_URL_PARAM_RE = re.compile(r'(?i)(token|signature|sig|expires|pass|password|key|api_key|access_token|refresh_token|apikey|secret)=[^&"\'\s#]+')
_JSON_KEY_RE = re.compile(r'(?i)(["\'](?:api_key|token|secret|access_token|refresh_token|signature|authorization|password|apikey|service_role_key)["\']\s*:\s*)(["\'])[^\2]*?\2')
_HEADER_AUTH_RE = re.compile(r'(?i)(["\']authorization["\']\s*:\s*)(["\'])[^\2]*?\2')

def redact_string(text: str) -> str:
    if not isinstance(text, str):
        return text

    # 1. Redact Authorization Bearer tokens
    text = _BEARER_RE.sub(r'\1[REDACTED]', text)

    # 2. Redact Signed URL query params
    text = _URL_PARAM_RE.sub(r'\1=[REDACTED]', text)

    # 3. Redact JSON key values
    text = _JSON_KEY_RE.sub(r'\1\2[REDACTED]\2', text)
    text = _HEADER_AUTH_RE.sub(r'\1\2[REDACTED]\2', text)

    # 4. Redact specific prefixes
    # Groq (gsk_...)
    text = re.sub(r'(?i)gsk_[a-zA-Z0-9_\-]+', '[REDACTED_GROQ_KEY]', text)
    # OpenRouter (sk-or-v1-...)
    text = re.sub(r'(?i)sk-or-v1-[a-zA-Z0-9_\-]+', '[REDACTED_OPENROUTER_KEY]', text)
    # Runway or generic sk-
    text = re.sub(r'(?i)sk-[a-zA-Z0-9_\-]{20,}', '[REDACTED_API_KEY]', text)

    return text

def redact_data(data: Any) -> Any:
    """Recursively redact dicts, lists, and strings."""
    if isinstance(data, dict):
        redacted = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ("api_key", "token", "secret", "authorization", "password", "apikey", "signature", "bearer", "credential")):
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

