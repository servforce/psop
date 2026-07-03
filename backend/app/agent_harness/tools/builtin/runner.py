from __future__ import annotations

import hashlib
import json
from typing import Any

from app.agent_harness.agents.psop.runner.schemas import (
    RUNNER_DECISIONS,
    RUNNER_OBSERVATION_SCHEMA,
    RUNNER_OBSERVATION_VIRTUAL_PATH,
    validate_runner_observation,
)
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec


def register_runner_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="psop.runner.read_prompt_view",
            description="读取 RuntimeService 为当前节点构造的 Prompt View。",
            purpose="用于 psop.runner 理解当前 Session Token 投影，不读取完整数据库状态。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            max_result_chars=24000,
        ),
        _read_prompt_view,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.read_runtime_contract",
            description="读取当前 PSOP-EG runtime contract 摘要。",
            purpose="用于 psop.runner 获取执行目标、workflow steps、证据要求、安全约束和合法 phase。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            max_result_chars=30000,
        ),
        _read_runtime_contract,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.read_current_checkpoint",
            description="读取当前等待点、expected inputs、resume phase 和已有证据摘要。",
            purpose="用于 psop.runner 判断当前节点是否需要等待更多终端输入。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            max_result_chars=12000,
        ),
        _read_current_checkpoint,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.list_step_reference_images",
            description="列出当前步骤允许返回给终端的参考图片。",
            purpose="用于 psop.runner 在当前 workflow step 范围内选择参考图片，不能跨步骤选择。",
            input_schema={
                "type": "object",
                "properties": {"workflow_step_id": {"type": "string"}},
                "additionalProperties": False,
            },
            max_result_chars=12000,
        ),
        _list_step_reference_images,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.list_terminal_events",
            description="按 seq 范围列出当前 Run 的终端事件摘要。",
            purpose="用于 psop.runner 读取终端事实摘要，不返回原始二进制。",
            input_schema={
                "type": "object",
                "properties": {
                    "from_seq": {"type": "integer", "minimum": 1},
                    "to_seq": {"type": "integer", "minimum": 1},
                    "max_items": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "additionalProperties": False,
            },
            max_result_chars=30000,
        ),
        _list_terminal_events,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.read_terminal_event_part",
            description="读取当前 Run 中某个 terminal event part 的安全摘要。",
            purpose="用于 psop.runner 获取已授权 part 的文本、metadata 或 artifact ref，不返回大二进制。",
            input_schema={
                "type": "object",
                "required": ["event_ref", "part_id"],
                "properties": {
                    "event_ref": {"type": "string"},
                    "part_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
            max_result_chars=12000,
        ),
        _read_terminal_event_part,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.read_latest_evidence",
            description="读取 RuntimeService 投影的最新终端 evidence bundle。",
            purpose="用于 psop.runner 判断当前用户输入和附件是否满足当前步骤证据要求。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            max_result_chars=20000,
        ),
        _read_latest_evidence,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.submit_observation",
            description="提交并校验 psop.runner 的结构化 RunnerObservation。",
            purpose="写入 runner-observation.json；不修改 Run、Session Token、terminal events 或数据库状态。",
            input_schema={
                "type": "object",
                "required": ["schema", "node_id", "decision"],
                "properties": {
                    "schema": {"type": "string", "enum": [RUNNER_OBSERVATION_SCHEMA]},
                    "node_id": {"type": "string"},
                    "decision": {"type": "string", "enum": sorted(RUNNER_DECISIONS)},
                    "terminal_message": {"type": "string"},
                    "reason": {"type": "string"},
                    "next_phase": {"type": "string"},
                    "wait_reason": {"type": "string"},
                    "expected_inputs": {"type": "array", "items": {"type": "string"}},
                    "evidence_assessment": {"type": "object"},
                    "reference_images": {"type": "array", "items": {"type": "object"}},
                    "safety_flags": {"type": "array", "items": {"type": "object"}},
                    "final_response": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string"},
                },
                "additionalProperties": False,
            },
            risk_class="write_local",
            side_effect_class="write_sandbox_file",
            resource_scope="sandbox_outputs",
            audit_event="agent.runner.observation.submitted",
            max_result_chars=8000,
        ),
        _submit_observation,
    )


def _read_prompt_view(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    prompt_view = _context(context).get("prompt_view")
    if not isinstance(prompt_view, dict) or not prompt_view:
        return _error_result("not_found", "本次 invocation context 中缺少 prompt_view。", ["psop.runner.read_prompt_view"])
    return _limited_result(
        {
            "status": "success",
            "summary": "已读取当前节点 Prompt View。",
            "prompt_view": prompt_view,
            "trust_level": _trust_label(context, "prompt_view", "trusted_runtime_projection"),
            "truncated": False,
            "next_valid_actions": ["psop.runner.read_runtime_contract", "psop.runner.read_current_checkpoint"],
        },
        24000,
    )


def _read_runtime_contract(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    runtime_contract = _context(context).get("runtime_contract")
    if not isinstance(runtime_contract, dict) or not runtime_contract:
        return _error_result("not_found", "本次 invocation context 中缺少 runtime_contract。", ["psop.runner.read_runtime_contract"])
    checkpoint = _current_checkpoint(context)
    workflow_step_id = str(checkpoint.get("workflow_step_id") or "")
    current_step = _workflow_step(runtime_contract, workflow_step_id)
    return _limited_result(
        {
            "status": "success",
            "summary": "已读取 runtime contract。",
            "runtime_contract": {
                "execution_goal": runtime_contract.get("execution_goal"),
                "applicability": runtime_contract.get("applicability"),
                "workflow_steps": runtime_contract.get("workflow_steps"),
                "current_step": current_step,
                "expected_evidence": runtime_contract.get("expected_evidence"),
                "safety_constraints": runtime_contract.get("safety_constraints"),
                "wait_checkpoints": runtime_contract.get("wait_checkpoints"),
                "completion_criteria": runtime_contract.get("completion_criteria"),
                "recovery_paths": runtime_contract.get("recovery_paths"),
            },
            "legal_phases": _context(context).get("legal_phases") or [],
            "trust_level": _trust_label(context, "runtime_contract", "trusted"),
            "truncated": False,
            "next_valid_actions": ["psop.runner.list_step_reference_images", "psop.runner.read_latest_evidence"],
        },
        30000,
    )


def _read_current_checkpoint(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    checkpoint = _current_checkpoint(context)
    if not checkpoint:
        return _error_result("not_found", "本次 invocation context 中缺少 current_checkpoint。", ["psop.runner.read_current_checkpoint"])
    return {
        "status": "success",
        "summary": "已读取当前 wait checkpoint。",
        "current_checkpoint": checkpoint,
        "trust_level": "trusted_runtime_projection",
        "truncated": False,
        "next_valid_actions": ["psop.runner.list_terminal_events", "psop.runner.read_latest_evidence"],
    }


def _list_step_reference_images(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    requested_step_id = str(arguments.get("workflow_step_id") or "").strip()
    checkpoint_step_id = str(_current_checkpoint(context).get("workflow_step_id") or "").strip()
    workflow_step_id = requested_step_id or checkpoint_step_id
    raw_images = _context(context).get("step_reference_images")
    images = [item for item in raw_images if isinstance(item, dict)] if isinstance(raw_images, list) else []
    if workflow_step_id:
        images = [
            item
            for item in images
            if not item.get("workflow_step_id") or str(item.get("workflow_step_id")) == workflow_step_id
        ]
    images = sorted(images, key=lambda item: (int(item.get("display_order") or 0), str(item.get("reference_image_ref") or "")))
    return {
        "status": "success",
        "summary": f"当前步骤可用参考图片 {len(images)} 张。",
        "workflow_step_id": workflow_step_id,
        "items": images,
        "truncated": False,
        "next_valid_actions": ["psop.runner.submit_observation", "psop.runner.read_latest_evidence"],
    }


def _list_terminal_events(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    raw_events = _context(context).get("terminal_events")
    events = [item for item in raw_events if isinstance(item, dict)] if isinstance(raw_events, list) else []
    if not events:
        return {
            "status": "success",
            "summary": "当前上下文没有终端事件。",
            "items": [],
            "truncated": False,
            "trust_level": _trust_label(context, "terminal_events", "untrusted_runtime_input"),
            "next_valid_actions": ["psop.runner.submit_observation"],
        }
    from_seq = int(arguments.get("from_seq") or 1)
    to_seq = int(arguments.get("to_seq") or max(int(item.get("seq_no") or 0) for item in events))
    max_items = max(1, min(50, int(arguments.get("max_items") or 20)))
    selected = [
        _terminal_event_summary(item)
        for item in events
        if from_seq <= int(item.get("seq_no") or 0) <= to_seq
    ][:max_items]
    return _limited_result(
        {
            "status": "success",
            "summary": f"读取终端事件 {len(selected)} 条。",
            "items": selected,
            "truncated": len(selected) < len(events),
            "trust_level": _trust_label(context, "terminal_events", "untrusted_runtime_input"),
            "next_valid_actions": ["psop.runner.read_terminal_event_part", "psop.runner.submit_observation"],
        },
        30000,
    )


def _read_terminal_event_part(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    event_ref = str(arguments.get("event_ref") or "").strip()
    part_id = str(arguments.get("part_id") or "").strip()
    if not event_ref or not part_id:
        return _error_result("invalid_arguments", "event_ref 和 part_id 必须是非空字符串。", ["psop.runner.list_terminal_events"])
    event = _find_terminal_event(context, event_ref)
    if not event:
        return _error_result("not_found", f"找不到 terminal event：{event_ref}", ["psop.runner.list_terminal_events"])
    parts = event.get("parts") if isinstance(event.get("parts"), list) else []
    part = next((item for item in parts if isinstance(item, dict) and str(item.get("part_id") or "") == part_id), None)
    if not part:
        return _error_result("not_found", f"找不到 terminal event part：{part_id}", ["psop.runner.list_terminal_events"])
    return {
        "status": "success",
        "summary": f"已读取 part 摘要：{part_id}。",
        "event_ref": _event_ref(event),
        "part": _terminal_part_summary(part),
        "trust_level": "untrusted_runtime_input",
        "truncated": False,
        "next_valid_actions": ["psop.runner.submit_observation"],
    }


def _read_latest_evidence(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    evidence = _context(context).get("latest_evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    return _limited_result(
        {
            "status": "success",
            "summary": "已读取最新 evidence bundle。" if evidence else "当前没有最新 evidence bundle。",
            "latest_evidence": evidence,
            "trust_level": "untrusted_runtime_input",
            "truncated": False,
            "next_valid_actions": ["psop.runner.submit_observation", "psop.runner.list_terminal_events"],
        },
        20000,
    )


def _submit_observation(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    try:
        observation = validate_runner_observation(
            arguments,
            node_id=_current_node_id(context),
            output_contract=_output_contract(context),
            step_reference_images=_context(context).get("step_reference_images") if isinstance(_context(context).get("step_reference_images"), list) else [],
            terminal_cursor=_terminal_cursor(context),
        )
    except ValueError as exc:
        return _error_result("validation_failed", str(exc), ["psop.runner.submit_observation"])

    payload = observation.model_dump(mode="json")
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    context.sandbox.write_text(RUNNER_OBSERVATION_VIRTUAL_PATH, content)
    event_payload = {
        "artifact_type": "runner_observation",
        "artifact_ref": "sandbox://outputs/runner-observation.json",
        "content_hash": content_hash,
        "node_id": observation.node_id,
        "decision": observation.decision,
        "reference_image_count": len(observation.reference_images),
    }
    context.event_writer.record("agent.runner.observation.submitted", event_payload)
    context.event_writer.record("agent.artifact.created", event_payload)
    return {
        "status": "success",
        "summary": f"已提交 runner observation：{observation.decision}。",
        "artifact_ref": "sandbox://outputs/runner-observation.json",
        "content_hash": content_hash,
        "node_id": observation.node_id,
        "decision": observation.decision,
        "reference_image_count": len(observation.reference_images),
        "truncated": False,
        "next_valid_actions": [],
    }


def _context(context: ToolExecutionContext) -> dict[str, Any]:
    return context.invocation_context or {}


def _input(context: ToolExecutionContext) -> dict[str, Any]:
    return context.invocation_input or {}


def _current_node_id(context: ToolExecutionContext) -> str:
    node = _context(context).get("node")
    if isinstance(node, dict) and node.get("id"):
        return str(node["id"])
    node_input = _input(context).get("node")
    if isinstance(node_input, dict) and node_input.get("id"):
        return str(node_input["id"])
    return ""


def _output_contract(context: ToolExecutionContext) -> dict[str, Any]:
    contract = _context(context).get("output_contract")
    if isinstance(contract, dict):
        return contract
    contract = _input(context).get("output_contract")
    return contract if isinstance(contract, dict) else {}


def _current_checkpoint(context: ToolExecutionContext) -> dict[str, Any]:
    checkpoint = _context(context).get("current_checkpoint")
    return checkpoint if isinstance(checkpoint, dict) else {}


def _terminal_cursor(context: ToolExecutionContext) -> int:
    value = _context(context).get("terminal_cursor")
    if value is None:
        prompt_view = _context(context).get("prompt_view")
        if isinstance(prompt_view, dict):
            value = prompt_view.get("terminal_cursor")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _trust_label(context: ToolExecutionContext, key: str, default: str) -> str:
    labels = _context(context).get("trust_labels")
    if isinstance(labels, dict) and labels.get(key):
        return str(labels[key])
    return default


def _workflow_step(runtime_contract: dict[str, Any], step_id: str) -> dict[str, Any]:
    steps = runtime_contract.get("workflow_steps")
    if not isinstance(steps, list) or not step_id:
        return {}
    for step in steps:
        if isinstance(step, dict) and str(step.get("id") or "") == step_id:
            return step
    return {}


def _find_terminal_event(context: ToolExecutionContext, event_ref: str) -> dict[str, Any] | None:
    events = _context(context).get("terminal_events")
    if not isinstance(events, list):
        return None
    normalized_ref = event_ref.removeprefix("terminal_event:")
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("seq_no") or "") == normalized_ref or str(event.get("id") or "") == event_ref:
            return event
    return None


def _event_ref(event: dict[str, Any]) -> str:
    seq_no = event.get("seq_no")
    return f"terminal_event:{seq_no}" if seq_no else str(event.get("id") or "")


def _terminal_event_summary(event: dict[str, Any]) -> dict[str, Any]:
    parts = event.get("parts") if isinstance(event.get("parts"), list) else []
    return {
        "id": event.get("id"),
        "ref": _event_ref(event),
        "seq_no": event.get("seq_no"),
        "direction": event.get("direction"),
        "event_kind": event.get("event_kind"),
        "mime_type": event.get("mime_type"),
        "payload_inline": event.get("payload_inline"),
        "part_count": len(parts),
        "parts": [_terminal_part_summary(part) for part in parts if isinstance(part, dict)],
        "source_ref": event.get("source_ref"),
    }


def _terminal_part_summary(part: dict[str, Any]) -> dict[str, Any]:
    kind = str(part.get("kind") or "")
    summary = {
        "part_id": part.get("part_id"),
        "kind": kind,
        "mime_type": part.get("mime_type"),
        "artifact_ref": f"artifact://{part.get('artifact_object_id')}" if part.get("artifact_object_id") else "",
        "size_bytes": part.get("size_bytes"),
        "checksum": part.get("checksum"),
        "metadata": part.get("metadata") if isinstance(part.get("metadata"), dict) else {},
    }
    if kind == "text":
        summary["text"] = part.get("text") or ""
    return summary


def _limited_result(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(content) <= max_chars:
        return payload
    limited = dict(payload)
    limited["truncated"] = True
    for key in ("prompt_view", "runtime_contract", "latest_evidence", "items"):
        if key in limited:
            limited[key] = {"omitted": True, "reason": "result_too_large"}
    return limited


def _error_result(error_type: str, message: str, next_valid_actions: list[str]) -> dict[str, Any]:
    return {
        "status": "error",
        "type": error_type,
        "message": message,
        "retryable": error_type in {"timeout", "rate_limited", "service_unavailable", "internal_error"},
        "truncated": False,
        "next_valid_actions": next_valid_actions,
    }
