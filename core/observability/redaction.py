"""Redaction boundary for telemetry values supplied by application code."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_SENSITIVE_KEY_PARTS = (
    "prompt",
    "completion",
    "content",
    "message",
    "body",
    "authorization",
    "cookie",
    "password",
    "secret",
    "api_key",
    "access_token",
    "refresh_token",
    "request_headers",
    "response_headers",
    "input_text",
    "output_text",
    "raw_request",
    "raw_response",
)
_MAX_STRING_LENGTH = 256
_MAX_MAPPING_ITEMS = 50
_MAX_LIST_ITEMS = 20
_MAX_DEPTH = 4


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).casefold().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact(value: Any, *, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return "[redacted:maximum depth]"
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]" if _is_sensitive_key(key) else _redact(item, depth=depth + 1)
            for key, item in list(value.items())[:_MAX_MAPPING_ITEMS]
        }
    if isinstance(value, (list, tuple, set)):
        return [_redact(item, depth=depth + 1) for item in list(value)[:_MAX_LIST_ITEMS]]
    if isinstance(value, str):
        if len(value) <= _MAX_STRING_LENGTH:
            return value
        return f"{value[:_MAX_STRING_LENGTH - 3]}..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_STRING_LENGTH]


def redact_telemetry_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a bounded copy safe for telemetry persistence and live display."""
    result = _redact(value or {}, depth=0)
    return result if isinstance(result, dict) else {}
