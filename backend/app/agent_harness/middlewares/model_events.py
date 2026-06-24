from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse

from app.agent_harness.events import AgentEventWriter


class ModelCallEventMiddleware(AgentMiddleware[AgentState]):
    def __init__(self, event_writer: AgentEventWriter) -> None:
        super().__init__()
        self.event_writer = event_writer

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        started_at = time.perf_counter()
        payload = _request_payload(request)
        self.event_writer.record("agent.model.started", payload)
        try:
            result = handler(request)
        except Exception as exc:
            self.event_writer.record(
                "agent.model.failed",
                {**payload, "duration_ms": _elapsed_ms(started_at), "error_type": exc.__class__.__name__, "error": str(exc)},
            )
            raise
        self.event_writer.record("agent.model.completed", {**payload, "duration_ms": _elapsed_ms(started_at)})
        return result

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        started_at = time.perf_counter()
        payload = _request_payload(request)
        self.event_writer.record("agent.model.started", payload)
        try:
            result = await handler(request)
        except Exception as exc:
            self.event_writer.record(
                "agent.model.failed",
                {**payload, "duration_ms": _elapsed_ms(started_at), "error_type": exc.__class__.__name__, "error": str(exc)},
            )
            raise
        self.event_writer.record("agent.model.completed", {**payload, "duration_ms": _elapsed_ms(started_at)})
        return result


def _request_payload(request: ModelRequest) -> dict[str, Any]:
    model = getattr(request, "model", None)
    model_name = getattr(model, "model_name", None) or getattr(model, "model", None) or model.__class__.__name__ if model is not None else "unknown"
    tools = getattr(request, "tools", None) or []
    messages = getattr(request, "messages", None) or []
    return {"model": str(model_name), "message_count": len(messages), "tool_count": len(tools)}


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)
