from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field


class ScriptedBuilderChatModel(BaseChatModel):
    """Deterministic chat model for psop.builder script and CI tests."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str = "scripted-psop-builder"
    bound_tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-psop-builder"

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> "ScriptedBuilderChatModel":
        return self.model_copy(update={"bound_tools": list(tools)})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        tool_names = _tool_names(self.bound_tools or kwargs.get("tools") or [])
        message = _next_builder_message(messages, tool_names)
        return ChatResult(generations=[ChatGeneration(message=message)])


def _next_builder_message(messages: list[BaseMessage], tool_names: set[str]) -> AIMessage:
    if "load_skill" in tool_names and not _has_tool_result(messages, "load_skill", "psop-builder"):
        return _tool_call("call_load_builder_skill", "load_skill", {"skill_name": "psop-builder"})
    for resource_path, call_id in (
        ("core/SKILL.md", "call_load_builder_core"),
        ("evidence-mapping/SKILL.md", "call_load_builder_evidence"),
        ("quality-review/SKILL.md", "call_load_builder_quality"),
    ):
        if "load_skill_resource" in tool_names and not _has_resource_result(messages, "psop-builder", resource_path):
            return _tool_call(
                call_id,
                "load_skill_resource",
                {"skill_name": "psop-builder", "resource_path": resource_path, "max_chars": 60000},
            )
    if "psop.builder.read_current_source" in tool_names and not _has_tool_result(messages, "psop.builder.read_current_source"):
        return _tool_call("call_read_source", "psop.builder.read_current_source", {"paths": ["README.md", "SKILL.md"]})
    if "psop.builder.list_materials" in tool_names and not _has_tool_result(messages, "psop.builder.list_materials"):
        return _tool_call("call_list_materials", "psop.builder.list_materials", {"max_items": 20})
    if "psop.builder.read_material_analysis" in tool_names and not _has_tool_result(messages, "psop.builder.read_material_analysis"):
        material_id = _first_material_id(messages)
        return _tool_call("call_read_analysis", "psop.builder.read_material_analysis", {"material_id": material_id, "detail_level": "evidence", "max_chars": 8000})
    if "psop.builder.list_reference_assets" in tool_names and not _has_tool_result(messages, "psop.builder.list_reference_assets"):
        material_id = _first_material_id(messages)
        return _tool_call("call_list_assets", "psop.builder.list_reference_assets", {"material_id": material_id, "max_items": 10})
    if "psop.standard.search" in tool_names and not _has_tool_result(messages, "psop.standard.search"):
        return _tool_call(
            "call_standard_search",
            "psop.standard.search",
            {
                "query": "泵房 阀门 压力表 PPE 安全检查 操作规范",
                "task_summary": "进入泵房前检查 PPE、确认阀门关闭并记录压力表读数。",
                "standard_scope": "industry",
                "hazard_types": ["机械伤害", "压力风险"],
                "equipment_keywords": ["泵房", "阀门", "压力表"],
                "max_results": 3,
            },
        )
    if "workspace.write_text" in tool_names and not _has_tool_result(messages, "workspace.write_text"):
        return _tool_call(
            "call_workspace_note",
            "workspace.write_text",
            {
                "path": "evidence-map-draft.md",
                "content": "# Evidence Map Draft\n\n- 已读取素材分析、参考资产和标准检索状态。\n",
                "mode": "overwrite",
            },
        )
    if "psop.builder.submit_candidate" in tool_names and not _has_tool_result(messages, "psop.builder.submit_candidate"):
        return _tool_call("call_submit_candidate", "psop.builder.submit_candidate", _candidate(messages))
    return AIMessage(
        content="psop.builder scripted run 已完成，候选产物已写入 /mnt/psop/outputs/builder-result.json。",
        usage_metadata={"input_tokens": 16, "output_tokens": 20, "total_tokens": 36},
    )


def _candidate(messages: list[BaseMessage]) -> dict[str, Any]:
    material_id = _first_material_id(messages)
    asset = _first_reference_asset(messages)
    reference_path = str(asset.get("reference_path") or "references/keyframes/pump-room-pressure.jpg")
    asset_id = str(asset.get("asset_id") or "asset-1")
    return {
        "directory_tree": "README.md\nSKILL.md\nprompts/system.md\nreferences/README.md\nexamples/input.md\nexamples/expected-output.md\ntests/checklist.md",
        "files": {
            "README.md": "# 泵房进入前安全检查\n\n用于指导现场人员在进入泵房前完成 PPE、阀门状态和压力表读数检查。\n",
            "SKILL.md": (
                "# 泵房进入前安全检查\n\n"
                "## 目标\n确保进入泵房前具备基础安全条件。\n\n"
                "## 适用边界\n适用于常规泵房巡检前的安全确认。\n\n"
                "## 输入\n- 操作员文本确认\n- PPE 照片或明确确认\n- 阀门状态观察\n- 压力表读数\n\n"
                "## 输出\n- 是否允许进入泵房\n- 已记录的关键证据\n- 异常停止原因\n\n"
                "## Workflow\n"
                "### 阶段 1：PPE 与进入条件确认\n要求操作员确认 PPE 穿戴完整，并参考 "
                f"{reference_path} 判断现场入口状态。缺少 PPE 时停止进入。\n\n"
                "### 阶段 2：阀门与压力表确认\n确认目标阀门处于关闭状态，记录压力表读数。读数异常或阀门状态不清时停止并请求复核。\n\n"
                "## Wait Checkpoints\n- 阶段 1 等待 PPE 或入口状态证据。\n- 阶段 2 等待阀门状态和压力表读数。\n\n"
                "## Expected Evidence\n- PPE 确认\n- 阀门关闭证据\n- 压力表读数\n\n"
                "## Safety Constraints\n- PPE 不完整不得进入泵房。\n- 阀门状态不清不得继续。\n- 压力读数异常必须停止并升级复核。\n\n"
                "## Recovery Paths\n- 缺少 PPE：补齐 PPE 后重新提交证据。\n- 阀门状态不清：请求现场负责人复核。\n- 压力异常：停止进入并记录异常。\n\n"
                "## Completion Criteria\nPPE、阀门状态和压力读数均已确认且无异常。\n"
            ),
            "prompts/system.md": "你是泵房进入前安全检查运行时助手。必须按 SKILL.md 的阶段、证据门和安全停止条件推进。\n",
            "references/README.md": f"# 参考资料\n\n- `{reference_path}`：用于辅助判断泵房入口和设备状态。\n",
            "examples/input.md": "# 示例输入\n\n进入泵房前检查 PPE，确认阀门关闭，记录压力表读数。\n",
            "examples/expected-output.md": "# 期望输出\n\n阶段 1 要求 PPE 证据；阶段 2 要求阀门关闭和压力表读数；异常时停止。\n",
            "tests/checklist.md": "# 测试清单\n\n- happy path：阶段 1 和阶段 2 均有证据。\n- 缺失证据：缺少 PPE 时等待。\n- 风险停止：压力读数异常时停止。\n- 人工确认：阀门状态不清时请求复核。\n",
        },
        "generation_reason": "根据用户目标、素材分析和候选参考资产构建了泵房进入前检查 Skill draft。",
        "review_notes": ["标准检索不可用，未引用行业标准。", "发布前需要人工确认适用标准条款。"],
        "material_usage": [{"material_id": material_id, "usage": "用于识别 PPE、阀门关闭和压力表读数三个关键检查点。"}],
        "industry_standard_usage": [],
        "selected_reference_assets": [
            {
                "asset_id": asset_id,
                "material_id": material_id,
                "reference_path": reference_path,
                "used_in": ["SKILL.md", "references/README.md"],
                "reason": "该参考资产用于辅助判断泵房入口或设备状态。",
            }
        ],
        "evidence_map": [
            {
                "claim": "作业需要在进入泵房前确认 PPE。",
                "support_level": "observed_fact",
                "source_refs": [{"source_type": "user_description", "ref": "input.user_description"}],
                "used_in": ["阶段 1"],
            },
            {
                "claim": "阀门状态和压力表读数是关键完成证据。",
                "support_level": "observed_fact",
                "source_refs": [{"source_type": "material_analysis", "material_id": material_id}],
                "used_in": ["阶段 2"],
            },
            {
                "claim": "行业标准适用性需要人工确认。",
                "support_level": "human_confirmation_required",
                "source_refs": [{"source_type": "human_confirmation_required", "ref": "standard_scope"}],
                "used_in": ["review_notes"],
            },
        ],
        "missing_questions": [
            {
                "question": "该泵房适用的企业或行业标准编号是什么？",
                "reason": "当前检索结果未形成可直接采纳的标准条款约束。",
                "blocking_level": "non_blocking",
            }
        ],
        "safety_constraints": [
            {
                "constraint": "PPE 不完整不得进入泵房。",
                "applies_to": "阶段 1",
                "risk_type": "personal_safety",
                "required_action": "停止进入并要求补齐 PPE 证据。",
            },
            {
                "constraint": "压力读数异常时不得继续。",
                "applies_to": "阶段 2",
                "risk_type": "equipment_pressure",
                "required_action": "停止并请求现场负责人复核。",
            },
        ],
        "workflow_step_candidates": [
            {"step_id": "阶段 1", "title": "PPE 与进入条件确认"},
            {"step_id": "阶段 2", "title": "阀门与压力表确认"},
        ],
        "expected_evidence_requirements": [
            {"stage_id": "阶段 1", "evidence_type": "ppe_confirmation", "completion_criteria": "PPE 穿戴完整且入口状态可接受。"},
            {"stage_id": "阶段 2", "evidence_type": "valve_and_pressure", "completion_criteria": "阀门关闭且压力表读数已记录。"},
        ],
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


def _first_material_id(messages: list[BaseMessage]) -> str:
    payload = _latest_tool_payload(messages, "psop.builder.list_materials")
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return str(items[0].get("material_id") or "material-1")
    return "material-1"


def _first_reference_asset(messages: list[BaseMessage]) -> dict[str, Any]:
    payload = _latest_tool_payload(messages, "psop.builder.list_reference_assets")
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {
        "asset_id": "asset-1",
        "reference_path": "references/keyframes/pump-room-pressure.jpg",
    }


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
