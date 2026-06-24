from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage


class DanglingToolCallMiddleware(AgentMiddleware[AgentState]):
    def _build_patched_messages(self, messages: list[Any]) -> list[Any] | None:
        tool_messages_by_id: dict[str, deque[ToolMessage]] = defaultdict(deque)
        for message in messages:
            if isinstance(message, ToolMessage) and message.tool_call_id:
                tool_messages_by_id[str(message.tool_call_id)].append(message)

        tool_call_ids: set[str] = set()
        for message in messages:
            if getattr(message, "type", None) != "ai":
                continue
            for tool_call in getattr(message, "tool_calls", None) or []:
                tool_call_id = _tool_call_id(tool_call)
                if tool_call_id:
                    tool_call_ids.add(tool_call_id)

        patched: list[Any] = []
        changed = False
        for message in messages:
            if isinstance(message, ToolMessage) and str(message.tool_call_id) in tool_call_ids:
                continue
            patched.append(message)
            if getattr(message, "type", None) != "ai":
                continue
            for tool_call in getattr(message, "tool_calls", None) or []:
                tool_call_id = _tool_call_id(tool_call)
                if not tool_call_id:
                    continue
                queue = tool_messages_by_id.get(tool_call_id)
                if queue:
                    patched.append(queue.popleft())
                    continue
                patched.append(
                    ToolMessage(
                        content="[Tool call was interrupted and did not return a result.]",
                        tool_call_id=tool_call_id,
                        name=_tool_call_name(tool_call),
                        status="error",
                    )
                )
                changed = True
        return patched if changed else None

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        patched = self._build_patched_messages(list(request.messages))
        if patched is not None:
            request = request.override(messages=patched)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(list(request.messages))
        if patched is not None:
            request = request.override(messages=patched)
        return await handler(request)


def _tool_call_id(tool_call: Any) -> str | None:
    if isinstance(tool_call, dict):
        value = tool_call.get("id")
    else:
        value = getattr(tool_call, "id", None)
    return str(value) if value else None


def _tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "unknown")
    return str(getattr(tool_call, "name", None) or "unknown")
