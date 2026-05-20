from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

_LOG_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("psop_log_context", default={})

_RESERVED_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__)
_CONTEXT_KEYS = (
    "skill_id",
    "skill_key",
    "skill_version_id",
    "compile_request_id",
    "publish_record_id",
    "run_id",
    "invocation_id",
    "job_id",
    "worker_id",
    "node_id",
    "node_kind",
    "route_key",
    "provider",
    "model",
)
_SENSITIVE_KEY_PARTS = ("authorization", "token", "api_key", "apikey", "password", "secret")
_TOKEN_USAGE_KEYS = {
    "cached_tokens",
    "completion_tokens",
    "input_tokens",
    "llm_input_tokens",
    "llm_output_tokens",
    "llm_total_tokens",
    "output_tokens",
    "prompt_tokens",
    "reasoning_tokens",
    "total_tokens",
}


def configure_logging(level: str, *, log_format: str = "plain") -> None:
    """Configure backend logging with optional JSON records and trace correlation."""

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    formatter: logging.Formatter
    if log_format.lower() == "json":
        formatter = JsonLogFormatter()
    else:
        formatter = TraceAwarePlainFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    if root_logger.handlers:
        root_logger.setLevel(numeric_level)
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)
        return

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    """Temporarily attach domain identifiers to all logs emitted in this context."""

    current = dict(_LOG_CONTEXT.get())
    current.update({key: value for key, value in values.items() if value not in (None, "")})
    token = _LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def get_log_context() -> dict[str, Any]:
    return dict(_LOG_CONTEXT.get())


class TraceAwarePlainFormatter(logging.Formatter):
    """Plain formatter that appends trace and domain correlation fields."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        fields = _record_context(record)
        if not fields:
            return message
        suffix = " ".join(f"{key}={value}" for key, value in fields.items())
        return f"{message} {suffix}"


class JsonLogFormatter(logging.Formatter):
    """Small dependency-free JSON formatter for local and OTLP log correlation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        payload.update(_record_context(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(_sanitize(payload), ensure_ascii=False, default=str)


def _record_context(record: logging.LogRecord) -> dict[str, Any]:
    context = get_log_context()
    context.update(_trace_context())
    for key in _CONTEXT_KEYS:
        if hasattr(record, key):
            context[key] = getattr(record, key)
    for key, value in record.__dict__.items():
        if key in _RESERVED_RECORD_KEYS or key in context:
            continue
        if key.startswith("_") or key in {"message", "asctime"}:
            continue
        context[key] = value
    return {key: _sanitize(value) for key, value in context.items() if value not in (None, "")}


def _trace_context() -> dict[str, str]:
    try:
        from opentelemetry import trace

        span_context = trace.get_current_span().get_span_context()
    except Exception:
        return {}
    if not getattr(span_context, "is_valid", False):
        return {}
    return {
        "trace_id": f"{span_context.trace_id:032x}",
        "span_id": f"{span_context.span_id:016x}",
    }


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_sensitive_key(key_str):
                sanitized[key_str] = "[REDACTED]"
            else:
                sanitized[key_str] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item) for item in value)
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    if normalized in _TOKEN_USAGE_KEYS:
        return False
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)
