from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, override
from urllib.parse import urlsplit, urlunsplit

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import BaseMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agent_harness.errors import AgentBudgetExceededError, AgentDeadlineExceededError
from app.agent_harness.events import AgentEventWriter


LOGGER = logging.getLogger(__name__)

_SENSITIVE_KEY_PARTS = ("authorization", "token", "api_key", "apikey", "password", "secret")
_TOKEN_USAGE_KEYS = {
    "cached_tokens",
    "completion_tokens",
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "reasoning_tokens",
    "total_tokens",
}
_HIDDEN_REASONING_KEYS = {"analysis", "reasoning", "reasoning_content", "thinking", "thinking_content"}
_HIDDEN_REASONING_BLOCK_TYPES = {"analysis", "reasoning", "thinking"}


class ModelCallEventMiddleware(AgentMiddleware[AgentState]):
    def __init__(
        self,
        event_writer: AgentEventWriter,
        *,
        max_model_calls: int | None = None,
        deadline_monotonic: float | None = None,
        before_model_call: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.event_writer = event_writer
        self.max_model_calls = max_model_calls
        self.deadline_monotonic = deadline_monotonic
        self.before_model_call = before_model_call
        self._model_call_count = 0

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        self._check_deadline()
        if self.before_model_call is not None:
            self.before_model_call()
        started_at = time.perf_counter()
        payload = self._request_payload(request)
        self._check_model_budget(payload)
        self.event_writer.record("agent.model.started", payload)
        LOGGER.info(
            "Agent LLM API input",
            extra={
                **_request_log_context(request, payload),
                "llm_input": _request_log_snapshot(request),
            },
        )
        try:
            result = handler(request)
        except Exception as exc:
            duration_ms = _elapsed_ms(started_at)
            self.event_writer.record(
                "agent.model.failed",
                {
                    **payload,
                    "duration_ms": duration_ms,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                },
            )
            LOGGER.exception(
                "Agent LLM API output failed",
                extra={
                    **_request_log_context(request, payload),
                    "duration_ms": duration_ms,
                    "llm_output": {"error_type": exc.__class__.__name__, "error": str(exc)},
                },
            )
            raise
        duration_ms = _elapsed_ms(started_at)
        LOGGER.info(
            "Agent LLM API output",
            extra={
                **_request_log_context(request, payload),
                "duration_ms": duration_ms,
                "llm_output": _response_log_snapshot(result),
            },
        )
        self._check_deadline()
        self.event_writer.record("agent.model.completed", {**payload, "duration_ms": duration_ms})
        return result

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        self._check_deadline()
        if self.before_model_call is not None:
            self.before_model_call()
        started_at = time.perf_counter()
        payload = self._request_payload(request)
        self._check_model_budget(payload)
        self.event_writer.record("agent.model.started", payload)
        LOGGER.info(
            "Agent LLM API input",
            extra={
                **_request_log_context(request, payload),
                "llm_input": _request_log_snapshot(request),
            },
        )
        try:
            result = await handler(request)
        except Exception as exc:
            duration_ms = _elapsed_ms(started_at)
            self.event_writer.record(
                "agent.model.failed",
                {
                    **payload,
                    "duration_ms": duration_ms,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                },
            )
            LOGGER.exception(
                "Agent LLM API output failed",
                extra={
                    **_request_log_context(request, payload),
                    "duration_ms": duration_ms,
                    "llm_output": {"error_type": exc.__class__.__name__, "error": str(exc)},
                },
            )
            raise
        duration_ms = _elapsed_ms(started_at)
        LOGGER.info(
            "Agent LLM API output",
            extra={
                **_request_log_context(request, payload),
                "duration_ms": duration_ms,
                "llm_output": _response_log_snapshot(result),
            },
        )
        self._check_deadline()
        self.event_writer.record("agent.model.completed", {**payload, "duration_ms": duration_ms})
        return result

    def _check_deadline(self) -> None:
        if self.deadline_monotonic is None or time.monotonic() < self.deadline_monotonic:
            return
        raise AgentDeadlineExceededError("Agent invocation exceeded its runtime step deadline.")

    def _request_payload(self, request: ModelRequest) -> dict[str, Any]:
        self._model_call_count += 1
        return {**_request_payload(request), "model_call_index": self._model_call_count}

    def _check_model_budget(self, payload: dict[str, Any]) -> None:
        if self.max_model_calls is None or self._model_call_count <= self.max_model_calls:
            return
        self.event_writer.record(
            "agent.budget.exceeded",
            {
                "budget_type": "model_calls",
                "limit": self.max_model_calls,
                "actual": self._model_call_count,
                "message": f"模型调用次数超过限制：{self.max_model_calls}。",
            },
        )
        raise AgentBudgetExceededError(f"模型调用次数超过限制：{self.max_model_calls}。")


def _request_payload(request: ModelRequest) -> dict[str, Any]:
    model = getattr(request, "model", None)
    model_name = getattr(model, "model_name", None) or getattr(model, "model", None) or model.__class__.__name__ if model is not None else "unknown"
    tools = getattr(request, "tools", None) or []
    messages = getattr(request, "messages", None) or []
    return {"model": str(model_name), "message_count": len(messages), "tool_count": len(tools)}


def _request_log_context(request: ModelRequest, payload: dict[str, Any]) -> dict[str, Any]:
    runtime = getattr(request, "runtime", None)
    runtime_context = getattr(runtime, "context", None)
    context = runtime_context if isinstance(runtime_context, Mapping) else {}
    model = getattr(request, "model", None)
    return {
        "provider": model.__class__.__module__.split(".", 1)[0] if model is not None else "unknown",
        "model": payload.get("model"),
        "model_call_index": payload.get("model_call_index"),
        "agent_run_id": context.get("agent_run_id"),
        "sandbox_id": context.get("sandbox_id"),
    }


def _request_log_snapshot(request: ModelRequest) -> dict[str, Any]:
    return {
        "model": _model_log_snapshot(getattr(request, "model", None)),
        "system_message": _message_log_snapshot(getattr(request, "system_message", None)),
        "messages": [
            _message_log_snapshot(message)
            for message in (getattr(request, "messages", None) or [])
        ],
        "tools": [_tool_log_snapshot(tool) for tool in (getattr(request, "tools", None) or [])],
        "tool_choice": _safe_log_value(getattr(request, "tool_choice", None)),
        "response_format": _safe_log_value(getattr(request, "response_format", None)),
        "model_settings": _safe_log_value(getattr(request, "model_settings", None) or {}),
    }


def _model_log_snapshot(model: Any) -> dict[str, Any]:
    if model is None:
        return {}
    return {
        "class": f"{model.__class__.__module__}.{model.__class__.__name__}",
        "model": getattr(model, "model_name", None) or getattr(model, "model", None),
        "base_url": _safe_url(str(getattr(model, "openai_api_base", "") or "")),
        "timeout_seconds": _safe_log_value(getattr(model, "request_timeout", None)),
        "max_retries": _safe_log_value(getattr(model, "max_retries", None)),
        "stream_usage": _safe_log_value(getattr(model, "stream_usage", None)),
        "default_params": _safe_log_value(getattr(model, "_default_params", None) or {}),
    }


def _message_log_snapshot(message: Any) -> Any:
    if message is None:
        return None
    if isinstance(message, BaseMessage):
        return _safe_log_value(message.model_dump(mode="json"))
    return _safe_log_value(message)


def _tool_log_snapshot(tool: Any) -> Any:
    try:
        return _safe_log_value(convert_to_openai_tool(tool))
    except Exception:
        return {
            "name": str(getattr(tool, "name", "") or ""),
            "description": str(getattr(tool, "description", "") or ""),
        }


def _response_log_snapshot(result: Any) -> dict[str, Any]:
    if isinstance(result, ModelResponse):
        return {
            "messages": [_message_log_snapshot(message) for message in result.result],
            "structured_response": _safe_log_value(result.structured_response),
        }
    if isinstance(result, BaseMessage):
        return {"messages": [_message_log_snapshot(result)], "structured_response": None}
    return {"result": _safe_log_value(result)}


def _safe_log_value(value: Any, *, parent_key: str = "") -> Any:
    if isinstance(value, BaseMessage):
        return _message_log_snapshot(value)
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return _safe_log_value(value.model_dump(mode="json"), parent_key=parent_key)
        except Exception:
            return str(value)
    if isinstance(value, Mapping):
        block_type = str(value.get("type") or "").lower()
        if block_type in _HIDDEN_REASONING_BLOCK_TYPES:
            return {"type": block_type, "content": "[OMITTED_HIDDEN_REASONING]"}
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_sensitive_key(key_str):
                sanitized[key_str] = "[REDACTED]"
            elif key_str.lower() in _HIDDEN_REASONING_KEYS and not isinstance(
                item,
                (int, float, bool, type(None)),
            ):
                sanitized[key_str] = "[OMITTED_HIDDEN_REASONING]"
            elif key_str == "data" and parent_key == "input_audio" and isinstance(item, str):
                sanitized[key_str] = {"binary_omitted": True, "base64_chars": len(item)}
            elif key_str == "url" and isinstance(item, str):
                sanitized[key_str] = _safe_url(item)
            else:
                sanitized[key_str] = _safe_log_value(item, parent_key=key_str)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_safe_log_value(item, parent_key=parent_key) for item in value]
    if isinstance(value, str) and value.startswith("data:"):
        return _safe_url(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_url(url: str) -> Any:
    if not url:
        return url
    if url.startswith("data:"):
        header, separator, content = url.partition("base64,")
        media_type = header[5:].split(";", 1)[0] if header.startswith("data:") else ""
        return {
            "binary_omitted": True,
            "media_type": media_type,
            "encoding": "base64" if separator else "unknown",
            "base64_chars": len(content) if separator else 0,
        }
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "[REDACTED_URL]"
    if parsed.scheme in {"http", "https"} and parsed.query:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "[REDACTED]", ""))
    return url


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    if normalized in _TOKEN_USAGE_KEYS:
        return False
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)
