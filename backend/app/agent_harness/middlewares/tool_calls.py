from __future__ import annotations

import ast
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.agent_harness.errors import AgentBudgetExceededError, AgentDeadlineExceededError
from app.agent_harness.events import AgentEventWriter


class ToolCallMiddleware(AgentMiddleware[AgentState]):
    def __init__(
        self,
        event_writer: AgentEventWriter,
        *,
        max_error_counts: dict[str, int] | None = None,
        deadline_monotonic: float | None = None,
    ) -> None:
        super().__init__()
        self.event_writer = event_writer
        self.max_error_counts = max_error_counts or {}
        self.deadline_monotonic = deadline_monotonic
        self._error_counts: dict[str, int] = {}
        self._validation_signatures: dict[str, tuple[str, int]] = {}

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        self._check_deadline()
        started_at = time.perf_counter()
        payload = _tool_payload(request)
        self.event_writer.record("agent.tool.started", payload)
        try:
            result = handler(request)
        except (GraphBubbleUp, AgentDeadlineExceededError):
            raise
        except Exception as exc:
            failed_payload = {
                **payload,
                "duration_ms": _elapsed_ms(started_at),
                "error_type": exc.__class__.__name__,
                "error": str(exc),
            }
            self.event_writer.record("agent.tool.failed", failed_payload)
            self._check_error_budget(
                {
                    **failed_payload,
                    "result_status": "error",
                    "result_type": exc.__class__.__name__,
                    "result_message": str(exc),
                }
            )
            return _error_tool_message(request, exc)
        self._check_deadline()
        completed_payload = {**payload, **_tool_result_payload(result), "duration_ms": _elapsed_ms(started_at)}
        self.event_writer.record("agent.tool.completed", completed_payload)
        self._check_error_budget(completed_payload)
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        self._check_deadline()
        started_at = time.perf_counter()
        payload = _tool_payload(request)
        self.event_writer.record("agent.tool.started", payload)
        try:
            result = await handler(request)
        except (GraphBubbleUp, AgentDeadlineExceededError):
            raise
        except Exception as exc:
            failed_payload = {
                **payload,
                "duration_ms": _elapsed_ms(started_at),
                "error_type": exc.__class__.__name__,
                "error": str(exc),
            }
            self.event_writer.record("agent.tool.failed", failed_payload)
            self._check_error_budget(
                {
                    **failed_payload,
                    "result_status": "error",
                    "result_type": exc.__class__.__name__,
                    "result_message": str(exc),
                }
            )
            return _error_tool_message(request, exc)
        self._check_deadline()
        completed_payload = {**payload, **_tool_result_payload(result), "duration_ms": _elapsed_ms(started_at)}
        self.event_writer.record("agent.tool.completed", completed_payload)
        self._check_error_budget(completed_payload)
        return result

    def _check_deadline(self) -> None:
        if self.deadline_monotonic is None or time.monotonic() < self.deadline_monotonic:
            return
        raise AgentDeadlineExceededError("Agent invocation exceeded its runtime step deadline.")

    def _check_error_budget(self, payload: dict[str, Any]) -> None:
        tool_name = str(payload.get("tool_name") or "")
        self._check_repeated_builder_validation(payload, tool_name)
        limit = self.max_error_counts.get(tool_name)
        if not limit or payload.get("result_status") != "error":
            if tool_name:
                self._error_counts[tool_name] = 0
            return
        current = self._error_counts.get(tool_name, 0) + 1
        self._error_counts[tool_name] = current
        if current < limit:
            return
        self.event_writer.record(
            "agent.budget.exceeded",
            {
                "budget_type": "tool_errors",
                "tool_name": tool_name,
                "limit": limit,
                "actual": current,
                "last_error_type": payload.get("result_type") or "",
                "last_error_message": payload.get("result_message") or "",
                "message": f"{tool_name} 连续返回错误次数达到限制：{limit}。",
            },
        )
        raise AgentBudgetExceededError(f"{tool_name} 连续返回错误次数达到限制：{limit}。")

    def _check_repeated_builder_validation(self, payload: dict[str, Any], tool_name: str) -> None:
        if tool_name != "psop.builder.submit_candidate" or payload.get("result_status") != "error":
            return
        diagnostics = payload.get("validation_diagnostics")
        if not isinstance(diagnostics, list) or not diagnostics:
            return
        signature = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        previous, count = self._validation_signatures.get(tool_name, ("", 0))
        count = count + 1 if signature == previous else 1
        self._validation_signatures[tool_name] = (signature, count)
        if count < 2:
            return
        self.event_writer.record(
            "agent.validation.terminal",
            {
                "tool_name": tool_name,
                "failure_kind": "validation_failed",
                "repeated_diagnostic_count": count,
                "diagnostics": diagnostics[:8],
                "message": "同一候选校验诊断重复出现，停止内部纠错。",
            },
        )
        raise AgentBudgetExceededError("psop.builder.submit_candidate 重复出现同一候选校验错误，停止内部纠错。")


def _tool_payload(request: ToolCallRequest) -> dict[str, Any]:
    tool_call = request.tool_call
    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
    return {
        "tool_name": str(tool_call.get("name") or "unknown"),
        "tool_call_id": str(tool_call.get("id") or ""),
        "argument_keys": sorted(args.keys()),
    }


def _tool_result_payload(result: ToolMessage | Command) -> dict[str, Any]:
    if not isinstance(result, ToolMessage):
        return {"result_kind": result.__class__.__name__}
    payload = _parse_tool_content(result.content)
    if not isinstance(payload, dict):
        content = str(result.content or "")
        return {
            "result_status": str(getattr(result, "status", None) or "success"),
            "result_summary": _truncate(content, 240),
        }
    summary: dict[str, Any] = {
        "result_status": str(payload.get("status") or getattr(result, "status", None) or "success"),
        "result_keys": sorted(str(key) for key in payload.keys())[:20],
    }
    for source_key, target_key in (
        ("type", "result_type"),
        ("message", "result_message"),
        ("summary", "result_summary"),
        ("artifact_ref", "result_artifact_ref"),
        ("files_root_ref", "result_files_root_ref"),
    ):
        value = payload.get(source_key)
        if isinstance(value, (str, int, float, bool)) and value != "":
            summary[target_key] = _truncate(str(value), 500)
    if isinstance(payload.get("items"), list):
        summary["result_item_count"] = len(payload["items"])
    if isinstance(payload.get("materialized_files"), list):
        summary["result_materialized_file_count"] = len(payload["materialized_files"])
    validation_summary = payload.get("validation_summary")
    if isinstance(validation_summary, dict):
        summary["validation_summary"] = {
            str(key): value
            for key, value in validation_summary.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, list):
        summary["validation_diagnostics"] = [item for item in diagnostics if isinstance(item, dict)][:8]
    return summary


def _parse_tool_content(content: Any) -> Any:
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        return {"content_parts": len(content)}
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None


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


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."
