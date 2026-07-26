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
    context = _runner_turn_context_from_messages(messages)
    if node_id.startswith("instruct_"):
        decision = "need_more_evidence"
        next_phase = ""
        terminal_message = "请先说明当前现场情况，并按要求提交清晰证据。"
        reason = "当前节点需要引导终端用户提交现场事实。"
        expected_inputs = ["text", "image"]
        missing_evidence = ["现场说明", "现场证据"]
    elif node_id == "final_verify":
        decision = "complete"
        next_phase = ""
        terminal_message = "测试任务已完成，现场步骤已验证。"
        reason = "当前证据满足完成标准。"
        expected_inputs = []
        missing_evidence = []
    else:
        decision = "continue"
        next_phase = ""
        terminal_message = "已确认当前证据，可以继续最终核验。"
        reason = "最新终端事件包含当前步骤所需的文字说明。"
        expected_inputs = []
        missing_evidence = []
    source_refs = ["runtime_contract.workflow_steps.collect_context"]
    event_ref = _latest_terminal_event_ref(messages)
    if event_ref:
        source_refs.append(event_ref)
    requirement_results = _requirement_results(
        context=context,
        decision=decision,
        event_ref=event_ref,
        reason=reason,
        missing_evidence=missing_evidence,
    )
    for result in requirement_results:
        if isinstance(result, dict):
            source_refs.extend(str(ref) for ref in result.get("event_refs") or [] if str(ref).strip())
    source_refs = list(dict.fromkeys(source_refs))
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
            "evaluated_event_refs": [event_ref] if event_ref else [],
            "accepted_event_refs": [event_ref] if event_ref and not missing_evidence else [],
            "rejected_event_refs": [],
            "missing_evidence": missing_evidence,
            "unsafe_or_ambiguous_facts": [],
            "requirement_results": requirement_results,
        },
        "safety_flags": [],
        "final_response": terminal_message if decision == "complete" else "",
        "source_refs": source_refs,
        "confidence": "high",
    }
    override = observation_overrides_by_node.get(node_id)
    if override:
        override = dict(override)
        assessment_override = override.pop("evidence_assessment", None)
        observation.update(override)
        if isinstance(assessment_override, dict):
            observation["evidence_assessment"] = {
                **observation["evidence_assessment"],
                **assessment_override,
            }
    return observation


def _requirement_results(
    *,
    context: dict[str, Any],
    decision: str,
    event_ref: str,
    reason: str,
    missing_evidence: list[str],
) -> list[dict[str, Any]]:
    progress = context.get("evidence_progress")
    requirements = progress.get("requirements") if isinstance(progress, dict) else None
    if not isinstance(requirements, list):
        return []
    results: list[dict[str, Any]] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        requirement_key = str(requirement.get("requirement_key") or "").strip()
        if not requirement_key:
            continue
        current_status = str(requirement.get("status") or "").strip().lower()
        accepted_refs = requirement.get("accepted_event_refs") if isinstance(requirement.get("accepted_event_refs"), list) else []
        if decision in {"continue", "complete"}:
            matched_event_ref, satisfied_by = _scripted_evidence_match(requirement, context)
            result = {
                    "requirement_key": requirement_key,
                    "status": "accepted",
                    "event_refs": (
                        [matched_event_ref]
                        if matched_event_ref
                        else ([event_ref] if event_ref else [str(ref) for ref in accepted_refs if str(ref).strip()])
                    ),
                    "reason": reason,
                }
            if satisfied_by:
                result["satisfied_by"] = satisfied_by
            results.append(result)
        elif current_status == "accepted":
            result = {
                    "requirement_key": requirement_key,
                    "status": "accepted",
                    "event_refs": [str(ref) for ref in accepted_refs if str(ref).strip()],
                    "reason": str(requirement.get("reason") or "该证据项此前已通过。"),
                }
            if requirement.get("satisfied_by"):
                result["satisfied_by"] = str(requirement["satisfied_by"])
            results.append(result)
        else:
            results.append(
                {
                    "requirement_key": requirement_key,
                    "status": "missing",
                    "event_refs": [],
                    "reason": "当前仍缺少该证据项。" if missing_evidence else reason,
                }
            )
    return results


def _scripted_evidence_match(requirement: dict[str, Any], context: dict[str, Any]) -> tuple[str, str]:
    options = [item for item in requirement.get("evidence_options") or [] if isinstance(item, dict)]
    if not options:
        return "", ""
    candidates = [
        item
        for item in [
            context.get("latest_evidence"),
            *reversed(context.get("recent_terminal_events") or []),
        ]
        if isinstance(item, dict) and isinstance(item.get("seq_no"), int)
    ]
    for option in options:
        expected_kind = str(option.get("kind") or "").lower()
        expected_event_kind = str(option.get("event_kind") or "")
        for event in candidates:
            actual_event_kind = str(event.get("event_kind") or "")
            alias_match = (
                actual_event_kind == "terminal.multimodal.input.v1"
                and expected_event_kind == f"terminal.{expected_kind}.input.v1"
            )
            if expected_event_kind and actual_event_kind != expected_event_kind and not alias_match:
                continue
            part_kinds = {
                str(item.get("kind") or "").lower()
                for item in event.get("parts") or []
                if isinstance(item, dict)
            }
            if expected_kind == "text" and (event.get("text") or event.get("payload_inline")):
                part_kinds.add("text")
            if expected_kind in part_kinds:
                return f"terminal_event:{event['seq_no']}", str(option.get("option_key") or "")
    return "", str(options[0].get("option_key") or "")


def _node_id_from_messages(messages: list[BaseMessage]) -> str:
    content = "\n".join(_human_message_text(message) for message in messages if isinstance(message, HumanMessage))
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
    content = "\n".join(_human_message_text(message) for message in messages if isinstance(message, HumanMessage))
    match = re.search(r"<RunnerTurnContext>\s*(\{.*\})\s*</RunnerTurnContext>", content, re.DOTALL)
    if not match:
        return {}
    parsed = _parse_jsonish(match.group(1))
    return parsed if isinstance(parsed, dict) else {}


def _human_message_text(message: HumanMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    if isinstance(message.content, list):
        return "\n".join(
            str(item.get("text") or "")
            for item in message.content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return str(message.content or "")


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
