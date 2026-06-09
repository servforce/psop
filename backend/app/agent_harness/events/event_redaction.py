from __future__ import annotations

from typing import Any


SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)


def redact_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _redact_value(payload) if isinstance(payload, dict) else {}


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                redacted[key_text] = "[redacted]"
            else:
                redacted[key_text] = _redact_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)
