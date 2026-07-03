from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field

from app.agent_harness.agents.psop.runner.schemas import RUNNER_OBSERVATION_SCHEMA


class ScriptedRunnerChatModel(BaseChatModel):
    """Deterministic chat model for psop.runner scripts and CI tests."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str = "scripted-psop-runner"
    bound_tools: list[Any] = Field(default_factory=list)

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
        message = _next_runner_message(messages, tool_names)
        return ChatResult(generations=[ChatGeneration(message=message)])


def _next_runner_message(messages: list[BaseMessage], tool_names: set[str]) -> AIMessage:
    for skill_name, call_id in (
        ("psop-runner-core", "call_load_runner_core"),
        ("psop-runner-terminal-guidance", "call_load_runner_guidance"),
        ("psop-runner-evidence-evaluation", "call_load_runner_evidence"),
    ):
        if "load_skill" in tool_names and not _has_tool_result(messages, "load_skill", skill_name):
            return _tool_call(call_id, "load_skill", {"skill_name": skill_name})
    for tool_name, call_id, args in (
        ("psop.runner.read_prompt_view", "call_read_prompt_view", {}),
        ("psop.runner.read_runtime_contract", "call_read_runtime_contract", {}),
        ("psop.runner.read_current_checkpoint", "call_read_checkpoint", {}),
        ("psop.runner.list_step_reference_images", "call_list_reference_images", {}),
        ("psop.runner.list_terminal_events", "call_list_terminal_events", {"max_items": 20}),
        ("psop.runner.read_latest_evidence", "call_read_latest_evidence", {}),
    ):
        if tool_name in tool_names and not _has_tool_result(messages, tool_name):
            return _tool_call(call_id, tool_name, args)
    if "psop.runner.submit_observation" in tool_names and not _has_tool_result(messages, "psop.runner.submit_observation"):
        return _tool_call("call_submit_runner_observation", "psop.runner.submit_observation", _observation(messages))
    return AIMessage(
        content="psop.runner scripted run 已完成，RunnerObservation 已提交。",
        usage_metadata={"input_tokens": 16, "output_tokens": 16, "total_tokens": 32},
    )


def _observation(messages: list[BaseMessage]) -> dict[str, Any]:
    prompt_view = _latest_tool_payload(messages, "psop.runner.read_prompt_view").get("prompt_view") or {}
    runtime_payload = _latest_tool_payload(messages, "psop.runner.read_runtime_contract").get("runtime_contract") or {}
    checkpoint = _latest_tool_payload(messages, "psop.runner.read_current_checkpoint").get("current_checkpoint") or {}
    references_payload = _latest_tool_payload(messages, "psop.runner.list_step_reference_images")
    reference_items = references_payload.get("items") if isinstance(references_payload.get("items"), list) else []
    latest_evidence = _latest_tool_payload(messages, "psop.runner.read_latest_evidence").get("latest_evidence") or {}
    node_id = str(prompt_view.get("node_id") or prompt_view.get("phase") or "evaluate_collect_context")
    accepted_refs = []
    seq_no = latest_evidence.get("seq_no") if isinstance(latest_evidence, dict) else None
    if seq_no:
        accepted_refs.append(f"terminal_event:{seq_no}")
    reference_images = []
    if reference_items:
        first = reference_items[0]
        if isinstance(first, dict) and first.get("reference_image_ref"):
            reference_images.append(
                {
                    "reference_image_ref": str(first["reference_image_ref"]),
                    "title": str(first.get("title") or "当前步骤参考图"),
                    "caption": str(first.get("caption") or "请按参考图补充当前步骤证据。"),
                    "source_ref": str(first.get("source_ref") or ""),
                    "display_order": int(first.get("display_order") or 1),
                }
            )
    if node_id == "final_verify":
        return {
            "schema": RUNNER_OBSERVATION_SCHEMA,
            "node_id": node_id,
            "decision": "complete",
            "terminal_message": "测试任务已完成。",
            "reason": "scripted runner 根据当前 workflow observations 完成最终验证。",
            "next_phase": "terminal",
            "wait_reason": "",
            "expected_inputs": [],
            "evidence_assessment": {
                "accepted_event_refs": accepted_refs,
                "rejected_event_refs": [],
                "missing_evidence": [],
                "unsafe_or_ambiguous_facts": [],
            },
            "reference_images": [],
            "safety_flags": [],
            "final_response": "测试任务已完成。",
            "source_refs": [*accepted_refs],
            "confidence": "medium",
        }
    if node_id.startswith("evaluate_") and accepted_refs:
        return {
            "schema": RUNNER_OBSERVATION_SCHEMA,
            "node_id": node_id,
            "decision": "continue",
            "terminal_message": "已收到补充证据，继续进入下一步验证。",
            "reason": "当前 scripted fixture 已收到 terminal evidence，按 runtime contract 推进。",
            "next_phase": _next_phase_for_evaluation(node_id, runtime_payload),
            "wait_reason": "",
            "expected_inputs": [],
            "evidence_assessment": {
                "accepted_event_refs": accepted_refs,
                "rejected_event_refs": [],
                "missing_evidence": [],
                "unsafe_or_ambiguous_facts": [],
            },
            "reference_images": [],
            "safety_flags": [],
            "final_response": "",
            "source_refs": ["runtime_contract.workflow_steps.collect_context", *accepted_refs],
            "confidence": "medium",
        }
    return {
        "schema": RUNNER_OBSERVATION_SCHEMA,
        "node_id": node_id,
        "decision": "need_more_evidence",
        "terminal_message": "请补充当前步骤的清晰现场照片，并用文字确认关键安全条件。",
        "reason": "当前 scripted fixture 用于验证 runner observation 提交流程，默认请求补充证据。",
        "next_phase": "waiting",
        "wait_reason": str(checkpoint.get("reason") or "等待补充现场证据。"),
        "expected_inputs": ["text", "image"],
        "evidence_assessment": {
            "accepted_event_refs": accepted_refs,
            "rejected_event_refs": [],
            "missing_evidence": ["当前步骤清晰照片", "关键安全条件文字确认"],
            "unsafe_or_ambiguous_facts": [],
        },
        "reference_images": reference_images,
        "safety_flags": [],
        "final_response": "",
        "source_refs": ["runtime_contract.workflow_steps.collect_context", *accepted_refs],
        "confidence": "medium",
    }


def _next_phase_for_evaluation(node_id: str, runtime_contract: dict[str, Any]) -> str:
    step_id = node_id.removeprefix("evaluate_")
    steps = runtime_contract.get("workflow_steps") if isinstance(runtime_contract.get("workflow_steps"), list) else []
    step_ids = [str(step.get("id") or "") for step in steps if isinstance(step, dict)]
    if step_id in step_ids:
        index = step_ids.index(step_id)
        if index + 1 < len(step_ids):
            return f"instruct_{step_ids[index + 1]}"
    return "final_verify"


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args}],
    )


def _tool_names(tools: list[Any]) -> set[str]:
    names = set()
    for tool in tools:
        name = getattr(tool, "name", None)
        if isinstance(name, str):
            names.add(name)
    return names


def _has_tool_result(messages: list[BaseMessage], tool_name: str, marker: str | None = None) -> bool:
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if getattr(message, "name", "") != tool_name:
            continue
        if marker is None or marker in str(message.content):
            return True
    return False


def _latest_tool_payload(messages: list[BaseMessage], tool_name: str) -> dict[str, Any]:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage) or getattr(message, "name", "") != tool_name:
            continue
        try:
            payload = json.loads(str(message.content))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}
