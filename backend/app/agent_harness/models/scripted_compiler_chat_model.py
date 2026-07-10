from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field


class ScriptedCompilerChatModel(BaseChatModel):
    """Deterministic chat model for psop.compiler scripts and CI tests."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str = "scripted-psop-compiler"
    bound_tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-psop-compiler"

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> "ScriptedCompilerChatModel":
        return self.model_copy(update={"bound_tools": list(tools)})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        tool_names = _tool_names(self.bound_tools or kwargs.get("tools") or [])
        message = _next_compiler_message(messages, tool_names)
        return ChatResult(generations=[ChatGeneration(message=message)])


def _next_compiler_message(messages: list[BaseMessage], tool_names: set[str]) -> AIMessage:
    if "load_skill" in tool_names and not _has_tool_result(messages, "load_skill", "psop-compiler"):
        return _tool_call("call_load_compiler_skill", "load_skill", {"skill_name": "psop-compiler"})
    for resource_path, call_id in (
        ("core/SKILL.md", "call_load_core_resource"),
        ("contract/SKILL.md", "call_load_contract_resource"),
        ("mapping/SKILL.md", "call_load_mapping_resource"),
        ("review/SKILL.md", "call_load_review_resource"),
    ):
        if "load_skill_resource" in tool_names and not _has_resource_result(messages, "psop-compiler", resource_path):
            return _tool_call(
                call_id,
                "load_skill_resource",
                {"skill_name": "psop-compiler", "resource_path": resource_path, "max_chars": 60000},
            )
    if "psop.compiler.read_skill_source" in tool_names and not _has_tool_result(messages, "psop.compiler.read_skill_source"):
        return _tool_call("call_read_source", "psop.compiler.read_skill_source", {"paths": ["README.md", "SKILL.md"], "max_chars": 20000})
    if "psop.compiler.read_manifest_snapshot" in tool_names and not _has_tool_result(messages, "psop.compiler.read_manifest_snapshot"):
        return _tool_call("call_read_manifest", "psop.compiler.read_manifest_snapshot", {"include_runtime_policy": True})
    if "psop.compiler.read_allowed_runtime" in tool_names and not _has_tool_result(messages, "psop.compiler.read_allowed_runtime"):
        return _tool_call("call_read_runtime", "psop.compiler.read_allowed_runtime", {"formal_revision": "psop-eg-formal/v5"})
    if "psop.compiler.read_domain_pack" in tool_names and not _has_tool_result(messages, "psop.compiler.read_domain_pack"):
        return _tool_call("call_read_domain", "psop.compiler.read_domain_pack", {"detail_level": "summary", "max_chars": 8000})
    if "psop.compiler.build_formal_v5_scaffold" in tool_names and not _has_tool_result(
        messages,
        "psop.compiler.build_formal_v5_scaffold",
    ):
        return _tool_call("call_build_scaffold", "psop.compiler.build_formal_v5_scaffold", _scaffold_arguments(messages))
    artifact_ref = _artifact_ref_from_scaffold(messages)
    candidate_ref = _candidate_ref_from_scaffold(messages)
    artifact = _artifact_from_scaffold(messages) or _artifact()
    if "psop.compiler.validate_formal_v5" in tool_names and not _has_tool_result(messages, "psop.compiler.validate_formal_v5"):
        validate_args = {"include_normalized_summary": True}
        if artifact_ref:
            validate_args["artifact_ref"] = artifact_ref
        else:
            validate_args["artifact"] = artifact
        return _tool_call("call_validate", "psop.compiler.validate_formal_v5", validate_args)
    if "psop.compiler.submit_candidate" in tool_names and not _has_tool_result(messages, "psop.compiler.submit_candidate"):
        if candidate_ref:
            return _tool_call("call_submit", "psop.compiler.submit_candidate", {"candidate_ref": candidate_ref})
        scaffold_candidate = _candidate_from_scaffold(messages)
        return _tool_call("call_submit", "psop.compiler.submit_candidate", scaffold_candidate or _candidate(messages, artifact))
    return AIMessage(
        content="psop.compiler scripted run 已完成，候选产物已写入 /mnt/psop/outputs/compiler-result.json。",
        usage_metadata={"input_tokens": 18, "output_tokens": 20, "total_tokens": 38},
    )


def _candidate(messages: list[BaseMessage], artifact: dict[str, Any]) -> dict[str, Any]:
    validator_payload = _latest_tool_payload(messages, "psop.compiler.validate_formal_v5")
    normalized_summary = validator_payload.get("normalized_summary") if isinstance(validator_payload, dict) else {}
    error_count = int(normalized_summary.get("error_count") or 0) if isinstance(normalized_summary, dict) else 0
    warning_count = int(normalized_summary.get("warning_count") or 0) if isinstance(normalized_summary, dict) else 0
    status = "failed" if error_count else "passed"
    return {
        "artifact": artifact,
        "compile_reason": "根据冻结 Skill source 中的收集上下文 workflow，生成一个 formal-v5 最小可运行 EG candidate。",
        "source_map": [
            {
                "target": "runtime_contract.execution_goal",
                "source_file": "SKILL.md",
                "source_summary": "Skill 要求运行时帮助用户完成现实任务并收集现场证据。",
            },
            {
                "target": "runtime_contract.workflow_steps[*]",
                "source_file": "SKILL.md",
                "source_summary": "Skill 定义了收集上下文、等待证据和最终验证的协作流程。",
            },
            {
                "target": "nodes[*]",
                "source_file": "SKILL.md",
                "source_summary": "每个 workflow step 编译为 instruct/evaluate 节点，并保留 final_verify。",
            },
        ],
        "diagnostics": [],
        "repair_history": [],
        "validator_summary": {
            "status": status,
            "error_count": error_count,
            "warning_count": warning_count,
        },
    }


def _scaffold_arguments(messages: list[BaseMessage]) -> dict[str, Any]:
    reference_images = _reference_images_from_source_assets(messages)
    workflow_step = {
        "id": "collect_context",
        "title": "收集上下文",
        "goal": "识别用户任务、约束和期望输出。",
        "source_evidence": "SKILL.md 要求先理解用户任务，并等待用户提交真实场景证据。",
        "expected_evidence": [
            {"kind": "text", "event_kind": "terminal.text.input.v1", "description": "用户任务和现场约束说明"},
            {"kind": "image", "event_kind": "terminal.image.input.v1", "description": "现场图片或状态截图"},
        ],
        "source_file": "SKILL.md",
    }
    if reference_images:
        workflow_step["reference_images"] = reference_images
    return {
        "execution_goal": "帮助用户在现实世界完成当前 Skill 目标。",
        "workflow_steps": [workflow_step],
        "safety_constraints": ["如果用户证据显示存在安全风险，应暂停并要求补充证据。"],
        "completion_criteria": ["用户任务上下文和必要证据已经收集完成。"],
        "recovery_paths": [{"when": "evidence_insufficient", "action": "request_more_evidence"}],
        "compile_reason": "根据冻结 Skill source 中的收集上下文 workflow，使用 scaffold tool 生成 formal-v5 最小可运行 EG candidate。",
    }


def _reference_images_from_source_assets(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    payload = _latest_tool_payload(messages, "psop.compiler.read_skill_source")
    raw_assets = payload.get("reference_assets") if isinstance(payload, dict) else []
    if not isinstance(raw_assets, list):
        return []
    reference_images: list[dict[str, Any]] = []
    for index, asset in enumerate(raw_assets, start=1):
        if not isinstance(asset, dict):
            continue
        reference_path = str(asset.get("reference_path") or "")
        artifact_object_id = str(asset.get("artifact_object_id") or "")
        mime_type = str(asset.get("mime_type") or "")
        if not reference_path or not artifact_object_id or not mime_type:
            continue
        reference_images.append(
            {
                "reference_image_ref": f"skill-reference://steps/collect_context/{_reference_image_slug(reference_path, index)}",
                "title": str(asset.get("title") or _reference_image_title(reference_path, index)),
                "caption": "",
                "artifact_object_id": artifact_object_id,
                "mime_type": mime_type,
                "source_ref": str(asset.get("source_ref") or ""),
                "display_order": int(asset.get("display_order") or index),
            }
        )
    return reference_images


def _reference_image_title(reference_path: str, index: int) -> str:
    stem = reference_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("-", " ").replace("_", " ").strip()
    return stem or f"参考图 {index}"


def _reference_image_slug(reference_path: str, index: int) -> str:
    stem = _reference_image_title(reference_path, index)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", stem.lower()).strip("-")
    return slug or f"image-{index}"


def _artifact_from_scaffold(messages: list[BaseMessage]) -> dict[str, Any] | None:
    payload = _latest_tool_payload(messages, "psop.compiler.build_formal_v5_scaffold")
    artifact = payload.get("artifact")
    return artifact if isinstance(artifact, dict) else None


def _artifact_ref_from_scaffold(messages: list[BaseMessage]) -> str:
    payload = _latest_tool_payload(messages, "psop.compiler.build_formal_v5_scaffold")
    artifact_ref = payload.get("artifact_ref")
    return artifact_ref if isinstance(artifact_ref, str) else ""


def _candidate_from_scaffold(messages: list[BaseMessage]) -> dict[str, Any] | None:
    payload = _latest_tool_payload(messages, "psop.compiler.build_formal_v5_scaffold")
    candidate = payload.get("candidate")
    return candidate if isinstance(candidate, dict) else None


def _candidate_ref_from_scaffold(messages: list[BaseMessage]) -> str:
    payload = _latest_tool_payload(messages, "psop.compiler.build_formal_v5_scaffold")
    candidate_ref = payload.get("candidate_ref")
    return candidate_ref if isinstance(candidate_ref, str) else ""


def _artifact() -> dict[str, Any]:
    return {
        "artifact_version": "psop-eg-formal-v5/agent-compiler-v1",
        "formal_revision": "psop-eg-formal/v5",
        "skill": {},
        "schema": {
            "token_fields": [
                "phase",
                "input_envelope",
                "observations",
                "budgets",
                "outputs",
                "control",
                "metadata",
                "facts",
                "registers",
                "memory",
                "trace",
                "status",
                "terminal",
            ],
            "input_name": "user_input",
            "output_name": "final_response",
        },
        "nodes": [
            {
                "id": "start",
                "kind": "start",
                "guard": {"phase_is": "start"},
                "actor": {"name": "runtime.start"},
                "merge": [
                    {"op": "set", "path": "observations.start", "from": "observation"},
                    {"op": "set", "path": "phase", "value": "instruct_collect_context"},
                ],
                "policy": {"priority": 10},
            },
            {
                "id": "instruct_collect_context",
                "kind": "llm",
                "guard": {"phase_is": "instruct_collect_context"},
                "actor": {"name": "agent.llm"},
                "interaction": {
                    "output_to_terminal": True,
                    "wait_after_output": True,
                    "checkpoint_id": "collect_context_evidence",
                    "workflow_step_id": "collect_context",
                    "wait_reason": "等待用户提交当前真实场景的说明或多模态证据。",
                    "expected_inputs": [
                        {"kind": "text", "event_kind": "terminal.text.input.v1"},
                        {"kind": "image", "event_kind": "terminal.image.input.v1"},
                        {"kind": "file", "event_kind": "terminal.file.input.v1"},
                    ],
                    "resume_phase": "evaluate_collect_context",
                },
                "projection": {
                    "system_template": "输出当前现实步骤指令。collect_context",
                    "user_template": (
                        "步骤目标：识别用户任务、约束和期望输出。\n"
                        "依据：SKILL.md 要求先理解用户任务。\n"
                        "当前 Token：{{token}}"
                    ),
                },
                "merge": [{"op": "set", "path": "observations.instruct_collect_context", "from": "observation"}],
                "policy": {"priority": 20},
            },
            {
                "id": "evaluate_collect_context",
                "kind": "llm",
                "guard": {"phase_is": "evaluate_collect_context"},
                "actor": {"name": "agent.llm"},
                "interaction": {
                    "evaluation": True,
                    "transitions": {
                        "proceed": "final_verify",
                        "complete": "terminal",
                        "abort": "terminal",
                    },
                },
                "projection": {
                    "system_template": "只输出 JSON decision。evaluate_collect_context",
                    "user_template": (
                        "根据 token.control.wait.evidence 判断 collect_context 是否完成。\n"
                        "必须输出 JSON decision。当前 Token：{{token}}"
                    ),
                },
                "merge": [{"op": "set", "path": "observations.evaluate_collect_context", "from": "observation"}],
                "policy": {"priority": 30},
            },
            {
                "id": "final_verify",
                "kind": "llm",
                "guard": {"phase_is": "final_verify"},
                "actor": {"name": "agent.llm"},
                "interaction": {
                    "evaluation": True,
                    "transitions": {
                        "proceed": "terminal",
                        "complete": "terminal",
                        "abort": "terminal",
                    },
                },
                "projection": {
                    "system_template": "只输出 JSON decision。final_verify",
                    "user_template": "根据 completion_criteria 与当前 Token 做最终验证。当前 Token：{{token}}",
                },
                "merge": [
                    {"op": "set", "path": "observations.final_verify", "from": "observation"},
                    {"op": "set", "path": "outputs.final_response", "from": "observation.terminal_message"},
                ],
                "policy": {"priority": 40},
            },
            {
                "id": "terminal",
                "kind": "terminal",
                "guard": {"phase_is": "terminal"},
                "actor": {"name": "runtime.terminal"},
                "merge": [
                    {"op": "set", "path": "outputs.final_response", "from": "observation.final_response"},
                    {"op": "set", "path": "status", "value": "success"},
                    {"op": "set", "path": "phase", "value": "completed"},
                ],
                "policy": {"priority": 50},
            },
        ],
        "init": {"entry_node": "start"},
        "halt": {"success": {"field_equals": {"path": "status", "value": "success"}}},
        "policies": {"selection": "priority_then_order", "max_steps": 10},
        "dependency_graph_for_view": [
            {"from": "start", "to": "instruct_collect_context"},
            {"from": "instruct_collect_context", "to": "evaluate_collect_context"},
            {"from": "evaluate_collect_context", "to": "final_verify"},
            {"from": "final_verify", "to": "terminal"},
        ],
        "runtime_contract": {
            "llm_route_key": "text",
            "skill_instruction": "遵循 SKILL.md 完成任务。",
            "execution_goal": "帮助用户在现实世界完成当前 Skill 目标。",
            "applicability": {
                "applies_when": ["用户处在真实任务现场并可提交证据。"],
                "does_not_apply_when": ["任务存在不可控安全风险或用户无法提供现场反馈。"],
            },
            "workflow_steps": [
                {
                    "id": "collect_context",
                    "title": "收集上下文",
                    "goal": "识别用户任务、约束和期望输出。",
                    "source_evidence": "SKILL.md 要求先理解用户任务。",
                }
            ],
            "expected_evidence": {
                "collect_context": [
                    {"kind": "text", "event_kind": "terminal.text.input.v1"},
                    {"kind": "image", "event_kind": "terminal.image.input.v1"},
                    {"kind": "file", "event_kind": "terminal.file.input.v1"},
                ]
            },
            "safety_constraints": ["如果用户证据显示存在安全风险，应中止或要求人工介入。"],
            "wait_checkpoints": [
                {
                    "checkpoint_id": "collect_context_evidence",
                    "workflow_step_id": "collect_context",
                    "expected_inputs": [{"kind": "text"}, {"kind": "image"}, {"kind": "file"}],
                }
            ],
            "completion_criteria": ["所有必须的现实步骤已经由证据验证完成。"],
            "recovery_paths": [{"when": "evidence_insufficient", "action": "request_more_evidence"}],
        },
    }


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args}],
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


def _has_resource_result(messages: list[BaseMessage], skill_name: str, resource_path: str) -> bool:
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "load_skill_resource":
            continue
        payload = _parse_jsonish(str(message.content or ""))
        if payload.get("skill_name") == skill_name and payload.get("resource_path") == resource_path:
            return True
    return False


def _latest_tool_payload(messages: list[BaseMessage], tool_name: str) -> dict[str, Any]:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage) or message.name != tool_name:
            continue
        return _parse_jsonish(str(message.content or ""))
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
