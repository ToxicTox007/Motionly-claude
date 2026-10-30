from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.orchestration.contracts import (
    WORKFLOW_PATCH_IDS,
    HistoryPayloadRejected,
    assert_identifier_only_history,
)

_SENSITIVE_KEY_RE = re.compile(
    r"(?:secret|password|credential|authorization|cookie|token|api[_-]?key|"
    r"access[_-]?key|private[_-]?key|signed[_-]?(?:url|uri)|"
    r"media[_-]?(?:bytes|data|payload)?|blob|base64)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(?:https?|s3|gs|az|file|data):(?:/{0,2})|//[A-Za-z0-9.-]+|"
    r"-----BEGIN [A-Z ]+-----|(?:bearer|authorization|token)\s*[:= ]",
    re.IGNORECASE,
)
_MAX_HISTORY_BYTES = 32 * 1024 * 1024
_MAX_PAYLOAD_BYTES = 64 * 1024
_MAX_METADATA_VALUE_BYTES = 256
_ALLOWED_ENCODINGS = frozenset({"json/plain", "binary/null"})


def validate_history_file(path: str | Path) -> dict[str, Any]:
    history_path = Path(path)
    size = history_path.stat().st_size
    if size > _MAX_HISTORY_BYTES:
        raise HistoryPayloadRejected("history file exceeds the validation bound")
    document = json.loads(history_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise HistoryPayloadRejected("history document must be an object")
    validate_history_document(document)
    return document


def validate_history_document(document: Mapping[str, Any]) -> None:
    _scan_history_value(document, path="history")


# ``workflow.patched`` makes the SDK upsert the TemporalChangeVersion search
# attribute: a KeywordList of the patch ids. It is the only payload allowed to
# carry metadata besides ``encoding``, and only with known patch ids.
_CHANGE_VERSION_SUFFIX = ".indexedFields.TemporalChangeVersion"


def _validate_change_version(value: Mapping[object, object], *, path: str) -> None:
    metadata = value.get("metadata")
    if set(value) != {"metadata", "data"} or not isinstance(metadata, Mapping):
        raise HistoryPayloadRejected(f"{path} has invalid payload fields")
    try:
        decoded = {
            str(key): base64.b64decode(str(item), validate=True).decode("ascii")
            for key, item in metadata.items()
        }
        data = json.loads(base64.b64decode(str(value.get("data")), validate=True))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HistoryPayloadRejected(f"{path} has invalid change-version data") from exc
    if decoded != {"encoding": "json/plain", "type": "KeywordList"}:
        raise HistoryPayloadRejected(f"{path} has forbidden payload metadata")
    if not isinstance(data, list) or any(item not in WORKFLOW_PATCH_IDS for item in data):
        raise HistoryPayloadRejected(f"{path} lists an unknown workflow patch")


def _scan_history_value(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        if path.endswith(_CHANGE_VERSION_SUFFIX) and _looks_like_payload(value):
            _validate_change_version(value, path=path)
            return
        if _looks_like_payload(value):
            decoded = _decode_payload(value, path=path)
            if decoded is not None:
                assert_identifier_only_history(decoded, path=f"{path}.data")
            return
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY_RE.search(key_text):
                raise HistoryPayloadRejected(f"{path} contains a forbidden history field")
            _scan_history_value(item, path=f"{path}.{key_text}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _scan_history_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _SENSITIVE_VALUE_RE.search(value):
        raise HistoryPayloadRejected(f"{path} contains forbidden external or secret data")


def _looks_like_payload(value: Mapping[object, object]) -> bool:
    metadata = value.get("metadata")
    return isinstance(metadata, Mapping) and "encoding" in metadata


def _decode_payload(value: Mapping[object, object], *, path: str) -> object | None:
    keys = set(value)
    if keys not in ({"metadata"}, {"metadata", "data"}):
        raise HistoryPayloadRejected(f"{path} has invalid payload fields")
    metadata = value.get("metadata")
    if not isinstance(metadata, Mapping) or set(metadata) != {"encoding"}:
        raise HistoryPayloadRejected(f"{path} has forbidden payload metadata")

    decoded_metadata: dict[str, str] = {}
    for key, encoded_value in metadata.items():
        if not isinstance(key, str) or _SENSITIVE_KEY_RE.search(key):
            raise HistoryPayloadRejected(f"{path} has forbidden payload metadata")
        if not isinstance(encoded_value, str):
            raise HistoryPayloadRejected(f"{path} has invalid payload metadata")
        try:
            raw_value = base64.b64decode(encoded_value, validate=True)
        except ValueError as exc:
            raise HistoryPayloadRejected(f"{path} has invalid payload metadata") from exc
        if len(raw_value) > _MAX_METADATA_VALUE_BYTES:
            raise HistoryPayloadRejected(f"{path} payload metadata exceeds the bound")
        try:
            text_value = raw_value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise HistoryPayloadRejected(f"{path} has non-text payload metadata") from exc
        if _SENSITIVE_VALUE_RE.search(text_value):
            raise HistoryPayloadRejected(f"{path} has forbidden payload metadata")
        decoded_metadata[key] = text_value

    encoding = decoded_metadata["encoding"]
    if encoding not in _ALLOWED_ENCODINGS:
        raise HistoryPayloadRejected(f"{path} uses a non-identifier payload encoding")

    encoded_data = value.get("data", "")
    if not isinstance(encoded_data, str):
        raise HistoryPayloadRejected(f"{path} has invalid payload data")
    if len(encoded_data) > (_MAX_PAYLOAD_BYTES * 4 // 3) + 8:
        raise HistoryPayloadRejected(f"{path} payload exceeds the history bound")
    try:
        raw = base64.b64decode(encoded_data, validate=True)
    except ValueError as exc:
        raise HistoryPayloadRejected(f"{path} has invalid payload data") from exc
    if len(raw) > _MAX_PAYLOAD_BYTES:
        raise HistoryPayloadRejected(f"{path} payload exceeds the history bound")

    if encoding == "binary/null":
        if raw:
            raise HistoryPayloadRejected(f"{path} binary/null payload must not contain data")
        return None
    if not raw:
        raise HistoryPayloadRejected(f"{path} json/plain payload is empty")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoryPayloadRejected(f"{path} has invalid JSON payload data") from exc
