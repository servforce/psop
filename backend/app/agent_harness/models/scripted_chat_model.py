from __future__ import annotations

from app.gateway.inference import LlmChatCompletion, LlmChatMessage, LlmToolCall


class ScriptedToolCallingChatModel:
    """Deterministic tool-calling model for local demo and CI."""

    provider = "scripted"
    model = "scripted-tool-calling"

    def __init__(self) -> None:
        self.calls = 0

    def complete_chat(
        self,
        *,
        messages: list[LlmChatMessage],
        tools: list[dict] | None = None,
        route_key: str = "text",
    ) -> LlmChatCompletion:
        self.calls += 1
        tool_names = _tool_names(tools or [])
        if self.calls == 1 and "demo_extract_check_items" in tool_names:
            user_text = _latest_user_text(messages)
            return _completion(
                LlmChatMessage(
                    role="assistant",
                    tool_calls=[
                        LlmToolCall(
                            id="call_extract",
                            name="demo_extract_check_items",
                            arguments={"text": user_text},
                        )
                    ],
                )
            )
        if self.calls == 2 and "demo_score_checklist" in tool_names:
            items = _latest_tool_result_items(messages)
            return _completion(
                LlmChatMessage(
                    role="assistant",
                    tool_calls=[
                        LlmToolCall(
                            id="call_score",
                            name="demo_score_checklist",
                            arguments={"items": items},
                        )
                    ],
                )
            )
        if self.calls == 3 and "memory_put" in tool_names:
            return _completion(
                LlmChatMessage(
                    role="assistant",
                    tool_calls=[
                        LlmToolCall(
                            id="call_memory",
                            name="memory_put",
                            arguments={"key": "last_demo_status", "value": "checklist_report_generated"},
                        )
                    ],
                )
            )
        if self.calls == 4 and "write_demo_report" in tool_names:
            item_count, risk_level = _latest_score(messages)
            content = (
                "# PSOP Harness Demo Report\n\n"
                f"- 检查项数量：{item_count}\n"
                f"- 风险等级：{risk_level}\n"
                "- 状态：已完成 demo agent harness 验收。\n"
            )
            return _completion(
                LlmChatMessage(
                    role="assistant",
                    tool_calls=[
                        LlmToolCall(
                            id="call_report",
                            name="write_demo_report",
                            arguments={"filename": "result.md", "content": content},
                        )
                    ],
                )
            )
        item_count, risk_level = _latest_score(messages)
        return _completion(
            LlmChatMessage(
                role="assistant",
                content=(
                    f"已完成检查清单生成，共识别 {item_count} 个检查项，"
                    f"风险等级 {risk_level}，报告已写入 workspace/result.md。"
                ),
            )
        )


def _completion(message: LlmChatMessage) -> LlmChatCompletion:
    return LlmChatCompletion(
        message=message,
        provider=ScriptedToolCallingChatModel.provider,
        model=ScriptedToolCallingChatModel.model,
        raw_response={"scripted": True},
        usage={},
    )


def _tool_names(tools: list[dict]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        if function.get("name"):
            names.add(str(function["name"]))
    return names


def _latest_user_text(messages: list[LlmChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user" and message.content:
            return message.content
    return ""


def _latest_tool_result_items(messages: list[LlmChatMessage]) -> list[str]:
    import json

    for message in reversed(messages):
        if message.role == "tool" and message.name == "demo_extract_check_items" and message.content:
            payload = json.loads(message.content)
            items = payload.get("items")
            return [str(item) for item in items] if isinstance(items, list) else []
    return []


def _latest_score(messages: list[LlmChatMessage]) -> tuple[int, str]:
    import json

    for message in reversed(messages):
        if message.role == "tool" and message.name == "demo_score_checklist" and message.content:
            payload = json.loads(message.content)
            return int(payload.get("item_count") or 0), str(payload.get("risk_level") or "low")
    return 0, "low"
