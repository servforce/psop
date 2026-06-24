from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.agent_harness.events import AgentEventWriter


class ToolCallMiddleware(AgentMiddleware[AgentState]):
    def __init__(self, event_writer: AgentEventWriter) -> None:
        super().__init__()
        self.event_writer = event_writer

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        started_at = time.perf_counter()
        payload = _tool_payload(request)
        self.event_writer.record("agent.tool.started", payload)
        try:
            result = handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            self.event_writer.record(
                "agent.tool.failed",
                {**payload, "duration_ms": _elapsed_ms(started_at), "error_type": exc.__class__.__name__, "error": str(exc)},
            )
            return _error_tool_message(request, exc)
        self.event_writer.record("agent.tool.completed", {**payload, "duration_ms": _elapsed_ms(started_at)})
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        started_at = time.perf_counter()
        payload = _tool_payload(request)
        self.event_writer.record("agent.tool.started", payload)
        try:
            result = await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            self.event_writer.record(
                "agent.tool.failed",
                {**payload, "duration_ms": _elapsed_ms(started_at), "error_type": exc.__class__.__name__, "error": str(exc)},
            )
            return _error_tool_message(request, exc)
        self.event_writer.record("agent.tool.completed", {**payload, "duration_ms": _elapsed_ms(started_at)})
        return result


def _tool_payload(request: ToolCallRequest) -> dict[str, Any]:
    tool_call = request.tool_call
    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
    return {
        "tool_name": str(tool_call.get("name") or "unknown"),
        "tool_call_id": str(tool_call.get("id") or ""),
        "argument_keys": sorted(args.keys()),
    }


def _error_tool_message(request: ToolCallRequest, exc: Exception) -> ToolMessage:
    tool_call = request.tool_call
    tool_name = str(tool_call.get("name") or "unknown")
    tool_call_id = str(tool_call.get("id") or "missing_tool_call_id")
    detail = str(exc).strip() or exc.__class__.__name__
    if len(detail) > 500:
        detail = detail[:497] + "..."
    return ToolMessage(
        content=f"Error: Tool '{tool_name}' failed with {exc.__class__.__name__}: {detail}",
        tool_call_id=tool_call_id,
        name=tool_name,
        status="error",
    )


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)
