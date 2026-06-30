import json
from typing import Any


class JsonStringBodyMixin:
    @classmethod
    def _coerce_json_string_body(cls, data: Any) -> Any:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        if isinstance(data, str):
            stripped = data.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    return data
        return data
