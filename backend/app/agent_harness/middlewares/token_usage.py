from __future__ import annotations

from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent_harness.events import AgentEventWriter


class TokenUsageMiddleware(AgentMiddleware[AgentState]):
    def __init__(self, event_writer: AgentEventWriter) -> None:
        super().__init__()
        self.event_writer = event_writer
        self._seen: set[str] = set()
        self._total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._record_usage(state)
        return None

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._record_usage(state)
        return None

    def _record_usage(self, state: AgentState) -> None:
        messages = state.get("messages", [])
        if not messages:
            return
        message = messages[-1]
        if not isinstance(message, AIMessage):
            return
        usage = _normalize_usage(getattr(message, "usage_metadata", None))
        if not usage:
            return
        message_key = str(getattr(message, "id", "") or f"object:{id(message)}")
        if message_key in self._seen:
            return
        self._seen.add(message_key)
        for key in self._total:
            self._total[key] += int(usage.get(key) or 0)
        self.event_writer.record("agent.token.usage", {"usage": usage, "total": dict(self._total)})


def _normalize_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    result = {
        "input_tokens": int(raw.get("input_tokens") or 0),
        "output_tokens": int(raw.get("output_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }
    return result if any(result.values()) else {}
