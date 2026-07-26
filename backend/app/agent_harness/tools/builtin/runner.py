from __future__ import annotations

import hashlib
import json
from typing import Any

from app.agent_harness.agents.psop.runner.schemas import (
    RUNNER_OBSERVATION_ARTIFACT_REF,
    RUNNER_OBSERVATION_SCHEMA,
    RUNNER_OBSERVATION_VIRTUAL_PATH,
    RunnerObservationValidationError,
    validate_runner_observation,
)
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec
from app.core.observability import add_metric_counter


def register_runner_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="psop.runner.read_prompt_view",
            description="读取当前 Runtime 节点 Prompt View。",
            purpose="用于 psop.runner 理解当前节点可见的 Session Token 投影，不读取完整数据库状态。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            risk_class="read_private_data",
            resource_scope="runtime_run",
            permission_policy="allow_with_run_scope",
            max_result_chars=40000,
        ),
        _read_prompt_view,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.read_runtime_contract",
            description="读取当前 PSOP-EG runtime contract 摘要。",
            purpose="用于 psop.runner 获取 execution goal、workflow steps、证据要求、安全约束和完成标准。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            max_result_chars=60000,
        ),
        _read_runtime_contract,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.read_current_checkpoint",
            description="读取当前 wait checkpoint、expected inputs 和 resume phase。",
            purpose="用于 psop.runner 判断当前等待点和用户补充证据边界。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            risk_class="read_private_data",
            resource_scope="runtime_run",
            permission_policy="allow_with_run_scope",
            max_result_chars=16000,
        ),
        _read_current_checkpoint,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.list_terminal_events",
            description="按 seq 范围列出当前 Run 的终端事件摘要。",
            purpose="用于 psop.runner 查看用户提交的现场事实摘要；终端事实均为 untrusted_runtime_input。",
            input_schema={
                "type": "object",
                "properties": {
                    "from_seq": {"type": "integer", "minimum": 0},
                    "to_seq": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "additionalProperties": False,
            },
            risk_class="read_private_data",
            resource_scope="runtime_run",
            permission_policy="allow_with_run_scope",
            max_result_chars=40000,
        ),
        _list_terminal_events,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.read_terminal_event_part",
            description="读取单个 terminal event part 的安全摘要。",
            purpose="用于 psop.runner 查看终端文本或媒体 part 的受控摘要，不返回对象存储 key 或内部 URL。",
            input_schema={
                "type": "object",
                "required": ["seq_no", "part_id"],
                "properties": {
                    "seq_no": {"type": "integer", "minimum": 0},
                    "part_id": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
            risk_class="read_private_data",
            resource_scope="runtime_run",
            permission_policy="allow_with_run_scope",
            max_result_chars=16000,
        ),
        _read_terminal_event_part,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.read_latest_evidence",
            description="读取当前等待点收到的最新 evidence bundle。",
            purpose="用于 psop.runner 判断最近一次终端输入是否满足当前步骤证据要求。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            risk_class="read_private_data",
            resource_scope="runtime_run",
            permission_policy="allow_with_run_scope",
            max_result_chars=30000,
        ),
        _read_latest_evidence,
    )
    registry.register(
        ToolSpec(
            name="psop.runner.submit_observation",
            description="提交并校验 psop.runner observation artifact。",
            purpose="用于 psop.runner 写入 runner-observation.json；不修改 Session Token、TerminalEvent、Run 或 TraceEvent。",
            input_schema={
                "type": "object",
                "required": ["schema", "node_id", "decision"],
                "properties": {
                    "schema": {"type": "string", "enum": [RUNNER_OBSERVATION_SCHEMA]},
                    "node_id": {"type": "string", "minLength": 1},
                    "decision": {
                        "type": "string",
                        "enum": ["continue", "need_more_evidence", "retry", "abort", "complete"],
                    },
                    "terminal_message": {"type": "string"},
                    "reason": {"type": "string"},
                    "next_phase": {
                        "type": "string",
                        "description": "兼容字段；runner 不选择 Runtime phase，默认传空字符串。",
                    },
                    "wait_reason": {"type": "string"},
                    "expected_inputs": {"type": "array", "items": {"type": "string"}},
                    "evidence_assessment": {
                        "type": "object",
                        "properties": {
                            "accepted_event_refs": {"type": "array", "items": {"type": "string"}},
                            "evaluated_event_refs": {"type": "array", "items": {"type": "string"}},
                            "rejected_event_refs": {"type": "array", "items": {"type": "string"}},
                            "missing_evidence": {"type": "array", "items": {"type": "string"}},
                            "unsafe_or_ambiguous_facts": {"type": "array", "items": {"type": "string"}},
                            "requirement_results": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["requirement_key", "status"],
                                    "properties": {
                                        "requirement_key": {"type": "string"},
                                        "status": {
                                            "type": "string",
                                            "enum": ["accepted", "rejected", "missing", "ambiguous", "not_applicable"],
                                        },
                                        "event_refs": {"type": "array", "items": {"type": "string"}},
                                        "satisfied_by": {"type": "string"},
                                        "reason": {"type": "string"},
                                    },
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "additionalProperties": False,
                    },
                    "safety_flags": {"type": "array", "items": {"type": "object"}},
                    "final_response": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "additionalProperties": False,
            },
            risk_class="write_local",
            side_effect_class="write_sandbox_file",
            resource_scope="sandbox_outputs",
            audit_event="agent.runner.observation.submitted",
            max_result_chars=8000,
            return_direct=True,
        ),
        _submit_observation,
    )


def _read_prompt_view(_: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    prompt_view = _dict_value(context.invocation_context, "prompt_view")
    if not prompt_view:
        return _error_result("not_found", "invocation context 中缺少 prompt_view。", ["psop.runner.read_runtime_contract"])
    return _success_result(
        "已读取当前节点 Prompt View。",
        prompt_view=prompt_view,
        trust_label=_trust_label(context, "prompt_view"),
        next_valid_actions=["psop.runner.read_runtime_contract", "psop.runner.read_current_checkpoint"],
    )


def _read_runtime_contract(_: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    runtime_contract = _dict_value(context.invocation_context, "runtime_contract")
    if not runtime_contract:
        return _error_result("not_found", "invocation context 中缺少 runtime_contract。", ["psop.runner.read_prompt_view"])
    return _success_result(
        "已读取 runtime contract。",
        runtime_contract=runtime_contract,
        trust_label=_trust_label(context, "runtime_contract"),
        next_valid_actions=["psop.runner.read_current_checkpoint"],
    )


def _read_current_checkpoint(_: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    checkpoint = _dict_value(context.invocation_context, "current_checkpoint")
    if not checkpoint:
        return _error_result("not_found", "invocation context 中缺少 current_checkpoint。", ["psop.runner.read_prompt_view"])
    return _success_result(
        "已读取当前 wait checkpoint。",
        checkpoint=checkpoint,
        evidence_progress=_dict_value(context.invocation_context, "evidence_progress"),
        next_valid_actions=["psop.runner.list_terminal_events", "psop.runner.read_latest_evidence"],
    )


def _list_terminal_events(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    events = [item for item in _list_value(context.invocation_context, "terminal_events") if isinstance(item, dict)]
    from_seq = _optional_int(arguments.get("from_seq"))
    to_seq = _optional_int(arguments.get("to_seq"))
    limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=50)
    selected = []
    for event in sorted(events, key=lambda item: int(item.get("seq_no") or 0)):
        seq_no = int(event.get("seq_no") or 0)
        if from_seq is not None and seq_no < from_seq:
            continue
        if to_seq is not None and seq_no > to_seq:
            continue
        selected.append(_safe_terminal_event(event, context))
    truncated = len(selected) > limit
    selected = selected[:limit]
    return {
        "status": "success",
        "summary": f"读取 {len(selected)} 条终端事件摘要。",
        "items": selected,
        "trust_label": _trust_label(context, "terminal_events"),
        "truncated": truncated,
        "next_valid_actions": ["psop.runner.read_latest_evidence", "psop.runner.read_terminal_event_part"],
    }


def _read_terminal_event_part(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    seq_no = _bounded_int(arguments.get("seq_no"), default=-1, minimum=0, maximum=10_000_000)
    part_id = str(arguments.get("part_id") or "").strip()
    if not part_id:
        return _error_result("invalid_arguments", "part_id 必须是非空字符串。", ["psop.runner.list_terminal_events"])
    for event in _list_value(context.invocation_context, "terminal_events"):
        if not isinstance(event, dict) or int(event.get("seq_no") or -1) != seq_no:
            continue
        for part in _list_value(event, "parts"):
            if isinstance(part, dict) and str(part.get("part_id") or "") == part_id:
                return _success_result(
                    f"已读取 terminal_event:{seq_no} part {part_id} 的安全摘要。",
                    part=_safe_terminal_part(part, event_seq_no=seq_no, context=context),
                    event_ref=f"terminal_event:{seq_no}",
                    next_valid_actions=["psop.runner.submit_observation"],
                )
    return _error_result("not_found", "指定 terminal event part 不属于当前 run。", ["psop.runner.list_terminal_events"])


def _read_latest_evidence(_: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    latest_evidence = _dict_value(context.invocation_context, "latest_evidence")
    if not latest_evidence:
        return _error_result("not_found", "当前上下文中没有 latest_evidence。", ["psop.runner.list_terminal_events"])
    return _success_result(
        "已读取最新 evidence bundle。",
        latest_evidence=_safe_terminal_event(latest_evidence, context),
        trust_label="untrusted_runtime_input",
        next_valid_actions=["psop.runner.submit_observation"],
    )


def _submit_observation(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    try:
        observation = validate_runner_observation(
            arguments,
            invocation_input=context.invocation_input,
            invocation_context=context.invocation_context,
        )
        output_path = context.sandbox.resolve_virtual_path(RUNNER_OBSERVATION_VIRTUAL_PATH)
        context.sandbox.write_json(output_path, observation)
        content_hash = hashlib.sha256(json.dumps(observation, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        context.event_writer.record(
            "agent.runner.observation.submitted",
            {
                "artifact_ref": RUNNER_OBSERVATION_ARTIFACT_REF,
                "node_id": observation["node_id"],
                "decision": observation["decision"],
                "runtime_decision": observation["runtime_decision"],
                "content_hash": content_hash,
            },
        )
        add_metric_counter(
            "psop_runner_observation_validation_total",
            attributes={"result": "passed"},
            description="Runner observation validation attempts",
        )
        context.event_writer.record(
            "agent.artifact.created",
            {
                "artifact_type": "runner_observation",
                "artifact_ref": RUNNER_OBSERVATION_ARTIFACT_REF,
                "content_hash": content_hash,
            },
        )
        return {
            "status": "success",
            "summary": "Runner observation 已提交。",
            "artifact_ref": RUNNER_OBSERVATION_ARTIFACT_REF,
            "node_id": observation["node_id"],
            "decision": observation["decision"],
            "runtime_decision": observation["runtime_decision"],
            "content_hash": content_hash,
            "validation_summary": {
                "status": "passed",
                "source_ref_count": len(observation.get("source_refs") or []),
            },
            "next_valid_actions": [],
        }
    except Exception as exc:
        failure_code = exc.code if isinstance(exc, RunnerObservationValidationError) else "invalid_observation"
        correction = exc.correction if isinstance(exc, RunnerObservationValidationError) else {}
        node = _dict_value(context.invocation_input, "node")
        runner_turn_context = _dict_value(context.invocation_context, "runner_turn_context")
        current_checkpoint = _dict_value(context.invocation_context, "current_checkpoint")
        prompt_view = _dict_value(context.invocation_context, "prompt_view")
        projected_control = _dict_value(prompt_view, "control")
        previous_checkpoint = _dict_value(projected_control, "wait")
        context.event_writer.record(
            "agent.validation.failed",
            {
                "tool_name": "psop.runner.submit_observation",
                "error_type": exc.__class__.__name__,
                "failure_code": failure_code,
                "error": str(exc),
                "correction": correction,
                "node_id": str(node.get("id") or ""),
                "node_mode": str(node.get("mode") or ""),
                "turn_kind": str(runner_turn_context.get("turn_kind") or ""),
                "current_checkpoint_id": str(current_checkpoint.get("checkpoint_id") or ""),
                "previous_checkpoint_id": str(previous_checkpoint.get("checkpoint_id") or ""),
            },
        )
        add_metric_counter(
            "psop_runner_observation_validation_total",
            attributes={"result": "failed", "failure_code": failure_code},
            description="Runner observation validation attempts",
        )
        return {
            **_error_result("invalid_arguments", str(exc), ["psop.runner.submit_observation"]),
            "failure_code": failure_code,
            "correction": correction,
        }


def _safe_terminal_event(event: dict[str, Any], context: ToolExecutionContext | None = None) -> dict[str, Any]:
    payload = {
        "id": event.get("id"),
        "seq_no": event.get("seq_no"),
        "direction": event.get("direction"),
        "event_kind": event.get("event_kind"),
        "mime_type": event.get("mime_type"),
        "payload_inline": event.get("payload_inline"),
        "source_ref": event.get("source_ref"),
        "occurred_at": event.get("occurred_at"),
        "text": event.get("text"),
        "input_bundle": _safe_input_bundle(event.get("input_bundle")),
    }
    parts = event.get("parts")
    if isinstance(parts, list):
        seq_no = event.get("seq_no") if isinstance(event.get("seq_no"), int) else None
        payload["parts"] = [
            _safe_terminal_part(part, event_seq_no=seq_no, context=context)
            for part in parts
            if isinstance(part, dict)
        ]
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _safe_terminal_part(
    part: dict[str, Any],
    *,
    event_seq_no: int | None = None,
    context: ToolExecutionContext | None = None,
) -> dict[str, Any]:
    part_id = str(part.get("part_id") or "")
    attachment_source_ref = f"terminal_event:{event_seq_no}:{part_id}" if event_seq_no is not None and part_id else ""
    payload = {
        "id": part.get("id"),
        "part_id": part_id,
        "order_index": part.get("order_index"),
        "kind": part.get("kind"),
        "mime_type": part.get("mime_type"),
        "text": part.get("text"),
        "artifact_object_id": part.get("artifact_object_id"),
        "size_bytes": part.get("size_bytes"),
        "checksum": part.get("checksum"),
        "metadata": _safe_metadata(part.get("metadata")),
        "attachment_source_ref": attachment_source_ref,
        "attachment_available": _attachment_available(context, attachment_source_ref),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _attachment_available(context: ToolExecutionContext | None, source_ref: str) -> bool:
    if context is None or not source_ref:
        return False
    for item in _list_value(context.invocation_context, "input_attachments"):
        if isinstance(item, dict) and str(item.get("source_ref") or "") == source_ref:
            return True
    return False


def _safe_input_bundle(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if key != "object_key"}


def _safe_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    blocked = {"object_key", "download_url", "internal_url", "secret", "credential"}
    return {str(key): item for key, item in value.items() if str(key) not in blocked}


def _trust_label(context: ToolExecutionContext, key: str) -> str:
    labels = context.invocation_context.get("trust_labels")
    if isinstance(labels, dict):
        return str(labels.get(key) or "")
    return ""


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _list_value(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    parsed = default if value is None else int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"数值必须在 {minimum} 到 {maximum} 之间。")
    return parsed


def _success_result(summary: str, **kwargs: Any) -> dict[str, Any]:
    return {"status": "success", "summary": summary, "truncated": False, **kwargs}


def _error_result(error_type: str, message: str, next_valid_actions: list[str]) -> dict[str, Any]:
    return {
        "status": "error",
        "type": error_type,
        "message": message,
        "retryable": False,
        "next_valid_actions": next_valid_actions,
    }
