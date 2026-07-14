from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse

from app.agent_harness.errors import AgentBudgetExceededError, AgentDeadlineExceededError
from app.agent_harness.events import AgentEventWriter


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
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        self._check_deadline()
        if self.before_model_call is not None:
            self.before_model_call()
        started_at = time.perf_counter()
        payload = self._request_payload(request)
        self._check_model_budget(payload)
        self.event_writer.record("agent.model.started", payload)
        try:
            result = handler(request)
        except Exception as exc:
            self.event_writer.record(
                "agent.model.failed",
                {**payload, "duration_ms": _elapsed_ms(started_at), "error_type": exc.__class__.__name__, "error": str(exc)},
            )
            raise
        self._check_deadline()
        self.event_writer.record("agent.model.completed", {**payload, "duration_ms": _elapsed_ms(started_at)})
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
        try:
            result = await handler(request)
        except Exception as exc:
            self.event_writer.record(
                "agent.model.failed",
                {**payload, "duration_ms": _elapsed_ms(started_at), "error_type": exc.__class__.__name__, "error": str(exc)},
            )
            raise
        self._check_deadline()
        self.event_writer.record("agent.model.completed", {**payload, "duration_ms": _elapsed_ms(started_at)})
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


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)
