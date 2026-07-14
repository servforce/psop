from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field


class ScriptedToolCallingChatModel(BaseChatModel):
    """Deterministic LangChain chat model for Agent Harness CI/demo tests."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str = "scripted-tool-calling"
    bound_tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-calling"

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> "ScriptedToolCallingChatModel":
        return self.model_copy(update={"bound_tools": list(tools)})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        tool_names = _tool_names(self.bound_tools or kwargs.get("tools") or [])
        message = _next_message(messages, tool_names)
        return ChatResult(generations=[ChatGeneration(message=message)])


def _next_message(messages: list[BaseMessage], tool_names: set[str]) -> AIMessage:
    if "load_skill" in tool_names and not _has_tool_result(messages, "load_skill"):
        return _tool_call("call_load_skill", "load_skill", {"skill_name": "demo_psop_checklist"})
    if "demo_extract_check_items" in tool_names and not _has_tool_result(messages, "demo_extract_check_items"):
        return _tool_call("call_extract", "demo_extract_check_items", {"text": _latest_user_text(messages)})
    if "demo_score_checklist" in tool_names and not _has_tool_result(messages, "demo_score_checklist"):
        return _tool_call("call_score", "demo_score_checklist", {"items": _latest_tool_result_items(messages)})
    if "memory_put" in tool_names and not _has_tool_result(messages, "memory_put"):
        return _tool_call("call_memory", "memory_put", {"key": "last_demo_status", "value": "checklist_report_generated"})
    if "write_demo_report" in tool_names and not _has_tool_result(messages, "write_demo_report"):
        item_count, risk_level = _latest_score(messages)
        content = (
            "# PSOP Harness Demo Report\n\n"
            f"- 检查项数量：{item_count}\n"
            f"- 风险等级：{risk_level}\n"
            "- 状态：已完成 demo agent harness 验收。\n"
        )
        return _tool_call("call_report", "write_demo_report", {"filename": "result.md", "content": content})
    item_count, risk_level = _latest_score(messages)
    return AIMessage(
        content=(
            f"已完成检查清单生成，共识别 {item_count} 个检查项，"
            f"风险等级 {risk_level}，报告已写入 /mnt/psop/workspace/result.md。"
        ),
        usage_metadata={"input_tokens": 12, "output_tokens": 24, "total_tokens": 36},
    )


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args}],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


def _tool_names(tools: list[Any]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        name = getattr(tool, "name", None)
        if not name and isinstance(tool, dict):
            name = tool.get("name") or (tool.get("function") or {}).get("name")
        if name:
            names.add(str(name))
    return names


def _has_tool_result(messages: list[BaseMessage], tool_name: str) -> bool:
    return any(isinstance(message, ToolMessage) and message.name == tool_name for message in messages)


def _latest_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content or "")
    return ""


def _latest_tool_result_items(messages: list[BaseMessage]) -> list[str]:
    payload = _latest_tool_payload(messages, "demo_extract_check_items")
    items = payload.get("items") if isinstance(payload, dict) else None
    return [str(item) for item in items] if isinstance(items, list) else []


def _latest_score(messages: list[BaseMessage]) -> tuple[int, str]:
    payload = _latest_tool_payload(messages, "demo_score_checklist")
    if not isinstance(payload, dict):
        return 0, "low"
    return int(payload.get("item_count") or 0), str(payload.get("risk_level") or "low")


def _latest_tool_payload(messages: list[BaseMessage], tool_name: str) -> dict[str, Any]:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage) or message.name != tool_name:
            continue
        content = message.content
        if isinstance(content, str):
            return _parse_jsonish(content)
    return {}


def _parse_jsonish(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}
