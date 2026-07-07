from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field


class ScriptedRunnerChatModel(BaseChatModel):
    """Deterministic chat model for psop.runner scripts and CI tests."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str = "scripted-psop-runner"
    bound_tools: list[Any] = Field(default_factory=list)
    observation_overrides_by_node: dict[str, dict[str, Any]] = Field(default_factory=dict)
    use_optional_reads: bool = False

    @property
    def _llm_type(self) -> str:
        return "scripted-psop-runner"

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> "ScriptedRunnerChatModel":
        return self.model_copy(update={"bound_tools": list(tools)})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        tool_names = _tool_names(self.bound_tools or kwargs.get("tools") or [])
        message = _next_runner_message(
            messages,
            tool_names,
            self.observation_overrides_by_node,
            self.use_optional_reads,
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _next_runner_message(
    messages: list[BaseMessage],
    tool_names: set[str],
    observation_overrides_by_node: dict[str, dict[str, Any]],
    use_optional_reads: bool,
) -> AIMessage:
    if use_optional_reads:
        missing_read_calls = []
        for tool_name, call_id, args in (
            ("psop.runner.read_prompt_view", "call_read_prompt_view", {}),
            ("psop.runner.read_runtime_contract", "call_read_runtime_contract", {}),
            ("psop.runner.read_current_checkpoint", "call_read_checkpoint", {}),
            ("psop.runner.list_terminal_events", "call_list_terminal_events", {"limit": 20}),
            ("psop.runner.read_latest_evidence", "call_read_latest_evidence", {}),
            ("psop.runner.list_step_reference_images", "call_list_reference_images", {}),
        ):
            if tool_name in tool_names and not _has_tool_result(messages, tool_name):
                missing_read_calls.append({"id": call_id, "name": tool_name, "args": args})
        if missing_read_calls:
            return _tool_calls(missing_read_calls)
    if "psop.runner.submit_observation" in tool_names and not _has_tool_result(messages, "psop.runner.submit_observation"):
        return _tool_call(
            "call_submit_runner_observation",
            "psop.runner.submit_observation",
            _observation(messages, observation_overrides_by_node),
        )
    return AIMessage(
        content="psop.runner scripted run 已完成，observation 已写入 /mnt/psop/outputs/runner-observation.json。",
        usage_metadata={"input_tokens": 12, "output_tokens": 14, "total_tokens": 26},
    )


def _observation(
    messages: list[BaseMessage],
    observation_overrides_by_node: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    node_id = _node_id_from_messages(messages)
    if node_id.startswith("instruct_"):
        decision = "need_more_evidence"
        next_phase = ""
        terminal_message = "请先说明当前现场情况，并按要求提交清晰证据。"
        reason = "当前节点需要引导终端用户提交现场事实。"
        expected_inputs = ["text", "image"]
        missing_evidence = ["现场说明", "现场证据"]
    elif node_id == "final_verify":
        decision = "complete"
        next_phase = "terminal"
        terminal_message = "测试任务已完成，现场步骤已验证。"
        reason = "当前证据满足完成标准。"
        expected_inputs = []
        missing_evidence = []
    else:
        decision = "continue"
        next_phase = "final_verify"
        terminal_message = "已确认当前证据，可以继续最终核验。"
        reason = "最新终端事件包含当前步骤所需的文字说明。"
        expected_inputs = []
        missing_evidence = []
    source_refs = ["runtime_contract.workflow_steps.collect_context"]
    event_ref = _latest_terminal_event_ref(messages)
    if event_ref:
        source_refs.append(event_ref)
    reference_images = _reference_images(messages)
    observation = {
        "schema": "psop.runner.observation.v1",
        "node_id": node_id,
        "decision": decision,
        "terminal_message": terminal_message,
        "reason": reason,
        "next_phase": next_phase,
        "wait_reason": "等待补充现场证据。" if decision in {"need_more_evidence", "retry"} else "",
        "expected_inputs": expected_inputs,
        "evidence_assessment": {
            "accepted_event_refs": [event_ref] if event_ref and not missing_evidence else [],
            "rejected_event_refs": [],
            "missing_evidence": missing_evidence,
            "unsafe_or_ambiguous_facts": [],
        },
        "reference_images": reference_images,
        "safety_flags": [],
        "final_response": terminal_message if decision == "complete" else "",
        "source_refs": source_refs,
        "confidence": "high",
    }
    override = observation_overrides_by_node.get(node_id)
    if override:
        observation.update(override)
    return observation


def _node_id_from_messages(messages: list[BaseMessage]) -> str:
    content = "\n".join(str(message.content or "") for message in messages if isinstance(message, HumanMessage))
    match = re.search(r"node_id=([A-Za-z0-9_.:-]+)", content)
    if match:
        return match.group(1)
    for candidate in ("final_verify", "evaluate_collect_context", "instruct_collect_context"):
        if candidate in content:
            return candidate
    return "evaluate_collect_context"


def _latest_terminal_event_ref(messages: list[BaseMessage]) -> str:
    context = _runner_turn_context_from_messages(messages)
    latest = context.get("latest_evidence")
    if isinstance(latest, dict) and isinstance(latest.get("seq_no"), int):
        return f"terminal_event:{latest['seq_no']}"
    recent_events = context.get("recent_terminal_events")
    if isinstance(recent_events, list) and recent_events:
        for item in reversed(recent_events):
            if isinstance(item, dict) and isinstance(item.get("seq_no"), int):
                return f"terminal_event:{item['seq_no']}"
    payload = _latest_tool_payload(messages, "psop.runner.list_terminal_events")
    items = payload.get("items")
    if isinstance(items, list) and items:
        seq_no = items[-1].get("seq_no") if isinstance(items[-1], dict) else None
        if isinstance(seq_no, int):
            return f"terminal_event:{seq_no}"
    payload = _latest_tool_payload(messages, "psop.runner.read_latest_evidence")
    latest = payload.get("latest_evidence")
    if isinstance(latest, dict) and isinstance(latest.get("seq_no"), int):
        return f"terminal_event:{latest['seq_no']}"
    return ""


def _reference_images(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    context = _runner_turn_context_from_messages(messages)
    context_items = context.get("reference_image_index")
    if isinstance(context_items, list):
        images = [_safe_reference_image(item, index) for index, item in enumerate(context_items, start=1)]
        if images:
            return images[:1]
    payload = _latest_tool_payload(messages, "psop.runner.list_step_reference_images")
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    results = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not item.get("reference_image_ref"):
            continue
        image = _safe_reference_image(item, index)
        if image:
            results.append(image)
    return results[:1]


def _safe_reference_image(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict) or not item.get("reference_image_ref"):
        return {}
    image = {
        "reference_image_ref": str(item.get("reference_image_ref") or ""),
        "title": str(item.get("title") or ""),
        "caption": str(item.get("caption") or ""),
        "source_ref": str(item.get("source_ref") or ""),
        "display_order": index,
    }
    for key in ("artifact_object_id", "artifact_ref", "mime_type", "workflow_step_id"):
        if item.get(key):
            image[key] = item.get(key)
    return image


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> AIMessage:
    return _tool_calls([{"id": call_id, "name": name, "args": args}])


def _tool_calls(calls: list[dict[str, Any]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=calls,
        usage_metadata={"input_tokens": 12, "output_tokens": 6, "total_tokens": 18},
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


def _has_tool_result(messages: list[BaseMessage], tool_name: str, skill_name: str | None = None) -> bool:
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != tool_name:
            continue
        if skill_name is None:
            return True
        payload = _parse_jsonish(str(message.content or ""))
        if payload.get("name") == skill_name:
            return True
    return False


def _latest_tool_payload(messages: list[BaseMessage], tool_name: str) -> dict[str, Any]:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage) or message.name != tool_name:
            continue
        return _parse_jsonish(str(message.content or ""))
    return {}


def _runner_turn_context_from_messages(messages: list[BaseMessage]) -> dict[str, Any]:
    content = "\n".join(str(message.content or "") for message in messages if isinstance(message, HumanMessage))
    match = re.search(r"<RunnerTurnContext>\s*(\{.*\})\s*</RunnerTurnContext>", content, re.DOTALL)
    if not match:
        return {}
    parsed = _parse_jsonish(match.group(1))
    return parsed if isinstance(parsed, dict) else {}


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
