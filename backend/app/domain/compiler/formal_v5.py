from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


FORMAL_REVISION = "psop-eg-formal/v5"
ARTIFACT_VERSION = "psop-eg-formal-v5/llm-compiler-mvp-v1"

SUPPORTED_NODE_KINDS = {"start", "input", "llm", "tool", "terminal"}
KNOWN_NODE_KINDS = SUPPORTED_NODE_KINDS | {"approval", "timer", "skill"}
SUPPORTED_ACTORS = {
    "runtime.start",
    "runtime.input",
    "agent.llm",
    "capability.demo_tool",
    "runtime.terminal",
}
SUPPORTED_TOOLS = {"psop.demo.inspect_input"}
SCAFFOLD_NODE_IDS = {"start", "input", "llm", "tool", "terminal", "final", "finalize", "finish", "end"}
DEFAULT_TOKEN_FIELDS = {
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
}


@dataclass(slots=True)
class FormalDiagnostic:
    severity: str
    code: str
    message: str
    location: dict[str, Any] | None = None
    category: str = "compiler"

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "location": self.location,
            "category": self.category,
        }


@dataclass(slots=True)
class FormalValidationResult:
    artifact: dict[str, Any] | None
    diagnostics: list[FormalDiagnostic] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)


def validate_and_normalize_artifact(candidate: Any) -> FormalValidationResult:
    diagnostics: list[FormalDiagnostic] = []
    if not isinstance(candidate, dict):
        return FormalValidationResult(
            artifact=None,
            diagnostics=[
                FormalDiagnostic(
                    severity="error",
                    code="compile.formal_v5.validation_failed",
                    message="编译智能体输出必须是 JSON object。",
                    location={"path": "$"},
                )
            ],
        )

    artifact = _normalize_candidate(copy.deepcopy(candidate))
    required = [
        "formal_revision",
        "schema",
        "nodes",
        "init",
        "halt",
        "policies",
        "dependency_graph_for_view",
        "runtime_contract",
    ]
    for field_name in required:
        if field_name not in artifact:
            diagnostics.append(
                FormalDiagnostic(
                    severity="error",
                    code="compile.formal_v5.validation_failed",
                    message=f"EG artifact 缺少必需字段 `{field_name}`。",
                    location={"path": field_name},
                )
            )

    if artifact.get("formal_revision") != FORMAL_REVISION:
        diagnostics.append(
            FormalDiagnostic(
                severity="error",
                code="compile.formal_v5.validation_failed",
                message=f"formal_revision 必须是 `{FORMAL_REVISION}`。",
                location={"path": "formal_revision"},
            )
        )

    schema = artifact.get("schema")
    if not isinstance(schema, dict):
        diagnostics.append(_error("schema 必须是对象。", "schema"))
        schema = {}
    token_fields = _token_fields(schema)

    nodes = artifact.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        diagnostics.append(_error("nodes 必须是非空数组。", "nodes"))
        nodes = []

    node_ids: set[str] = set()
    has_start = False
    has_terminal = False
    for index, node in enumerate(nodes):
        path = f"nodes[{index}]"
        if not isinstance(node, dict):
            diagnostics.append(_error("node 必须是对象。", path))
            continue

        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            diagnostics.append(_error("node.id 必须是非空字符串。", f"{path}.id"))
        elif node_id in node_ids:
            diagnostics.append(_error(f"node.id `{node_id}` 重复。", f"{path}.id"))
        else:
            node_ids.add(node_id)

        kind = node.get("kind")
        if kind == "start":
            has_start = True
        if kind == "terminal":
            has_terminal = True
        if kind not in KNOWN_NODE_KINDS:
            diagnostics.append(_error(f"node.kind `{kind}` 不在 formal-v5 节点类型集合中。", f"{path}.kind"))
        elif kind not in SUPPORTED_NODE_KINDS:
            diagnostics.append(
                FormalDiagnostic(
                    severity="error",
                    code="compile.unsupported_actor",
                    message=f"MVP Runtime 暂不支持 `{kind}` 节点。",
                    location={"path": f"{path}.kind"},
                )
            )

        actor_name = _actor_name(node.get("actor"))
        if actor_name not in SUPPORTED_ACTORS:
            diagnostics.append(
                FormalDiagnostic(
                    severity="error",
                    code="compile.unsupported_actor",
                    message=f"MVP Runtime 不支持 actor `{actor_name or '<missing>'}`。",
                    location={"path": f"{path}.actor"},
                )
            )
        if actor_name == "capability.demo_tool":
            tool_name = _tool_name(node.get("actor"))
            if tool_name and tool_name not in SUPPORTED_TOOLS:
                diagnostics.append(
                    FormalDiagnostic(
                        severity="error",
                        code="compile.unsupported_actor",
                        message=f"MVP Runtime 不支持 tool `{tool_name}`。",
                        location={"path": f"{path}.actor.tool_name"},
                    )
                )

        guard = node.get("guard")
        if guard is None:
            diagnostics.append(_error("node.guard 必须存在。", f"{path}.guard"))
        else:
            diagnostics.extend(_validate_guard(guard, token_fields, f"{path}.guard"))

        merge = node.get("merge")
        if not isinstance(merge, list):
            diagnostics.append(_error("node.merge 必须是数组。", f"{path}.merge"))
        else:
            diagnostics.extend(_validate_merge(merge, token_fields, f"{path}.merge"))

    if not has_start:
        diagnostics.append(_error("EG artifact 必须包含 start 节点。", "nodes"))
    if not has_terminal:
        diagnostics.append(_error("EG artifact 必须包含 terminal 节点。", "nodes"))

    init = artifact.get("init")
    if not isinstance(init, dict):
        diagnostics.append(_error("init 必须是对象。", "init"))
        init = {}
    entry_node = init.get("entry_node")
    if entry_node and isinstance(entry_node, str) and node_ids and entry_node not in node_ids:
        diagnostics.append(_error(f"init.entry_node `{entry_node}` 不存在。", "init.entry_node"))

    halt = artifact.get("halt")
    if not isinstance(halt, dict):
        diagnostics.append(_error("halt 必须是对象。", "halt"))
    elif "success" not in halt and "failure" not in halt:
        diagnostics.append(_error("halt 至少需要 success 或 failure 条件。", "halt"))

    runtime_contract = artifact.get("runtime_contract")
    workflow_step_ids: set[str] = set()
    if not isinstance(runtime_contract, dict):
        diagnostics.append(_error("runtime_contract 必须是对象。", "runtime_contract"))
    else:
        workflow_diagnostics, workflow_step_ids = _validate_workflow_contract(runtime_contract, node_ids)
        diagnostics.extend(workflow_diagnostics)
        diagnostics.extend(_validate_workflow_nodes(nodes, workflow_step_ids))

    artifact.setdefault("artifact_version", ARTIFACT_VERSION)
    artifact["graph_summary"] = _build_graph_summary(nodes)
    artifact["capability_summary"] = _build_capability_summary(nodes, artifact.get("runtime_contract"))
    return FormalValidationResult(artifact=artifact if not any(d.severity == "error" for d in diagnostics) else None, diagnostics=diagnostics)


def _error(message: str, path: str) -> FormalDiagnostic:
    return FormalDiagnostic(
        severity="error",
        code="compile.formal_v5.validation_failed",
        message=message,
        location={"path": path},
    )


def _normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    artifact = candidate
    nodes = artifact.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node["guard"] = _normalize_guard_shape(node.get("guard"))
            if isinstance(node.get("merge"), list):
                node["merge"] = _normalize_merge_shape(node["merge"])
            if "actor" not in node and isinstance(node.get("kind"), str):
                actor_name = {
                    "start": "runtime.start",
                    "input": "runtime.input",
                    "llm": "agent.llm",
                    "tool": "capability.demo_tool",
                    "terminal": "runtime.terminal",
                }.get(str(node["kind"]))
                if actor_name:
                    node["actor"] = {"name": actor_name}

    halt = artifact.get("halt")
    if isinstance(halt, dict) and "success" not in halt and "failure" not in halt:
        artifact["halt"] = {"success": _normalize_guard_shape(halt)}
    elif isinstance(halt, dict):
        if "success" in halt:
            halt["success"] = _normalize_guard_shape(halt["success"])
        if "failure" in halt:
            halt["failure"] = _normalize_guard_shape(halt["failure"])
        if "aborted" in halt:
            halt["aborted"] = _normalize_guard_shape(halt["aborted"])
    return artifact


def _normalize_guard_shape(guard: Any) -> Any:
    if isinstance(guard, bool) or guard is None:
        return guard
    if not isinstance(guard, dict):
        return guard

    if "op" in guard:
        op = str(guard.get("op") or "").lower()
        if op in {"always", "true"}:
            return {"always": True}
        if op == "phase_is":
            phase = guard.get("value", guard.get("phase"))
            return {"phase_is": str(phase)} if phase is not None else guard
        if op == "field_exists":
            path = guard.get("path", guard.get("value"))
            return {"field_exists": _normalize_token_path(str(path), target=False)} if path is not None else guard
        if op in {"field_equals", "equals", "eq"}:
            path = guard.get("path", guard.get("field"))
            value = guard.get("value")
            if path is not None:
                return {"field_equals": {"path": _normalize_token_path(str(path), target=False), "value": value}}
        if op in {"and", "all"}:
            values = guard.get("conditions", guard.get("items", guard.get("value", [])))
            return {"all": [_normalize_guard_shape(item) for item in values]} if isinstance(values, list) else guard
        if op in {"or", "any"}:
            values = guard.get("conditions", guard.get("items", guard.get("value", [])))
            return {"any": [_normalize_guard_shape(item) for item in values]} if isinstance(values, list) else guard
        if op == "not":
            value = guard.get("condition", guard.get("value"))
            return {"not": _normalize_guard_shape(value)}

    normalized = dict(guard)
    if "field_exists" in normalized and isinstance(normalized["field_exists"], str):
        normalized["field_exists"] = _normalize_token_path(normalized["field_exists"], target=False)
    if isinstance(normalized.get("field_equals"), dict) and isinstance(normalized["field_equals"].get("path"), str):
        normalized["field_equals"] = {
            **normalized["field_equals"],
            "path": _normalize_token_path(normalized["field_equals"]["path"], target=False),
        }
    for key in ("all", "any"):
        if isinstance(normalized.get(key), list):
            normalized[key] = [_normalize_guard_shape(item) for item in normalized[key]]
    if "not" in normalized:
        normalized["not"] = _normalize_guard_shape(normalized["not"])
    return normalized


def _normalize_merge_shape(merge: list[Any]) -> list[Any]:
    normalized_merge: list[Any] = []
    for operation in merge:
        if not isinstance(operation, dict):
            normalized_merge.append(operation)
            continue
        normalized = dict(operation)
        if normalized.get("op") in {"assign", "write"}:
            normalized["op"] = "set"
        if isinstance(normalized.get("path"), str):
            normalized["path"] = _normalize_token_path(normalized["path"], target=True)
        if isinstance(normalized.get("from"), str):
            normalized["from"] = _normalize_merge_source_path(normalized["from"])
        normalized_merge.append(normalized)
    return normalized_merge


def _normalize_merge_source_path(path: str) -> str:
    stripped = path.removeprefix("token.")
    if stripped in {"user_input", "input", "input_text"}:
        return "input.user_input"
    if stripped in {"llm_response", "response", "answer"}:
        return "observation.content"
    if stripped in {"final_response", "final"}:
        return "observation.final_response"
    if path.startswith(("observation.", "input.", "token.")) or path == "observation":
        return path
    return path


def _normalize_token_path(path: str, *, target: bool) -> str:
    stripped = path.removeprefix("token.")
    if stripped in {"phase", "status"}:
        return stripped
    if target:
        if stripped in {"user_input", "input", "input_text"}:
            return "observations.input.user_input"
        if stripped in {"llm_response", "response", "answer"}:
            return "observations.llm.content"
        if stripped in {"final_response", "final"}:
            return "outputs.final_response"
        if stripped in {"tool_result", "tool"}:
            return "observations.tool"
    else:
        if stripped in {"user_input", "input", "input_text"}:
            return "input_envelope.user_input"
        if stripped in {"llm_response", "response", "answer"}:
            return "observations.llm.content"
        if stripped in {"final_response", "final"}:
            return "outputs.final_response"
    return stripped


def _token_fields(schema: dict[str, Any]) -> set[str]:
    fields = set(DEFAULT_TOKEN_FIELDS)
    raw_fields = schema.get("token_fields")
    if isinstance(raw_fields, list):
        fields.update(str(item) for item in raw_fields if isinstance(item, str) and item)
    return fields


def _actor_name(actor: Any) -> str:
    if isinstance(actor, str):
        return actor
    if isinstance(actor, dict):
        if isinstance(actor.get("name"), str):
            return str(actor["name"])
        actor_type = actor.get("type")
        if actor_type == "llm":
            return "agent.llm"
        if actor_type == "tool":
            return "capability.demo_tool"
        if actor_type == "runtime" and isinstance(actor.get("operation"), str):
            return f"runtime.{actor['operation']}"
    return ""


def _tool_name(actor: Any) -> str:
    if isinstance(actor, dict):
        value = actor.get("tool_name") or actor.get("tool")
        if isinstance(value, str):
            return value
    return ""


def _validate_guard(guard: Any, token_fields: set[str], path: str) -> list[FormalDiagnostic]:
    diagnostics: list[FormalDiagnostic] = []
    if isinstance(guard, bool):
        return diagnostics
    if not isinstance(guard, dict):
        return [_error("guard 必须是对象或布尔值。", path)]

    allowed = {"always", "phase_is", "field_exists", "field_equals", "all", "any", "not"}
    unknown = set(guard) - allowed
    for key in sorted(unknown):
        diagnostics.append(_error(f"guard 使用了不支持的操作 `{key}`。", f"{path}.{key}"))

    if "phase_is" in guard and not isinstance(guard["phase_is"], str):
        diagnostics.append(_error("phase_is 必须是字符串。", f"{path}.phase_is"))
    if "field_exists" in guard:
        diagnostics.extend(_validate_path_value(guard["field_exists"], token_fields, f"{path}.field_exists"))
    if "field_equals" in guard:
        field_equals = guard["field_equals"]
        if not isinstance(field_equals, dict) or not isinstance(field_equals.get("path"), str):
            diagnostics.append(_error("field_equals 必须包含字符串 path。", f"{path}.field_equals"))
        else:
            diagnostics.extend(_validate_token_path(field_equals["path"], token_fields, f"{path}.field_equals.path"))
    for key in ("all", "any"):
        if key in guard:
            values = guard[key]
            if not isinstance(values, list):
                diagnostics.append(_error(f"{key} 必须是数组。", f"{path}.{key}"))
            else:
                for index, item in enumerate(values):
                    diagnostics.extend(_validate_guard(item, token_fields, f"{path}.{key}[{index}]"))
    if "not" in guard:
        diagnostics.extend(_validate_guard(guard["not"], token_fields, f"{path}.not"))
    return diagnostics


def _validate_merge(merge: list[Any], token_fields: set[str], path: str) -> list[FormalDiagnostic]:
    diagnostics: list[FormalDiagnostic] = []
    for index, operation in enumerate(merge):
        op_path = f"{path}[{index}]"
        if not isinstance(operation, dict):
            diagnostics.append(_error("merge operation 必须是对象。", op_path))
            continue
        if operation.get("op") != "set":
            diagnostics.append(_error("MVP merge 仅支持 op=set。", f"{op_path}.op"))
        target_path = operation.get("path")
        if not isinstance(target_path, str):
            diagnostics.append(_error("merge operation 必须包含字符串 path。", f"{op_path}.path"))
        else:
            diagnostics.extend(_validate_token_path(target_path, token_fields, f"{op_path}.path"))
        has_value = "value" in operation
        has_from = "from" in operation
        if has_value == has_from:
            diagnostics.append(_error("merge operation 必须且只能包含 value 或 from。", op_path))
    return diagnostics


def _validate_path_value(value: Any, token_fields: set[str], path: str) -> list[FormalDiagnostic]:
    if not isinstance(value, str):
        return [_error("字段路径必须是字符串。", path)]
    return _validate_token_path(value, token_fields, path)


def _validate_token_path(value: str, token_fields: set[str], path: str) -> list[FormalDiagnostic]:
    root = value.split(".", 1)[0]
    if root not in token_fields:
        return [_error(f"字段路径 `{value}` 引用了未知 Token 顶层字段 `{root}`。", path)]
    return []


def _validate_workflow_contract(runtime_contract: dict[str, Any], node_ids: set[str]) -> tuple[list[FormalDiagnostic], set[str]]:
    diagnostics: list[FormalDiagnostic] = []
    required_fields = [
        "execution_goal",
        "applicability",
        "workflow_steps",
        "expected_evidence",
        "safety_constraints",
        "wait_checkpoints",
        "completion_criteria",
        "recovery_paths",
    ]
    for field_name in required_fields:
        value = runtime_contract.get(field_name)
        if value in (None, "", [], {}):
            diagnostics.append(_workflow_error(f"runtime_contract.{field_name} 是现实世界协作执行必填字段。", f"runtime_contract.{field_name}"))

    workflow_steps = runtime_contract.get("workflow_steps")
    if not isinstance(workflow_steps, list) or not workflow_steps:
        diagnostics.append(
            FormalDiagnostic(
                severity="error",
                code="compile.workflow.not_extracted",
                message="runtime_contract.workflow_steps 必须包含从 SKILL.md/README.md 提取出的业务工作流步骤。",
                location={"path": "runtime_contract.workflow_steps"},
            )
        )
        return diagnostics, set()

    step_ids: set[str] = set()
    for index, step in enumerate(workflow_steps):
        path = f"runtime_contract.workflow_steps[{index}]"
        if not isinstance(step, dict):
            diagnostics.append(_workflow_error("workflow step 必须是对象。", path))
            continue
        step_id = step.get("id")
        if not isinstance(step_id, str) or not step_id:
            diagnostics.append(_workflow_error("workflow step.id 必须是非空字符串。", f"{path}.id"))
            continue
        if step_id in step_ids:
            diagnostics.append(_workflow_error(f"workflow step.id `{step_id}` 重复。", f"{path}.id"))
        if step_id in SCAFFOLD_NODE_IDS:
            diagnostics.append(
                _workflow_error(
                    f"workflow step.id `{step_id}` 是通用模板节点名，必须改为来自 Skill 工作流的语义化步骤 ID。",
                    f"{path}.id",
                )
            )
        instruct_id = f"instruct_{step_id}"
        evaluate_id = f"evaluate_{step_id}"
        if node_ids and instruct_id not in node_ids:
            diagnostics.append(_workflow_error(f"workflow step `{step_id}` 缺少指令节点 `{instruct_id}`。", f"{path}.id"))
        if node_ids and evaluate_id not in node_ids:
            diagnostics.append(_workflow_error(f"workflow step `{step_id}` 缺少证据评估节点 `{evaluate_id}`。", f"{path}.id"))
        title = step.get("title")
        goal = step.get("goal")
        evidence = step.get("source_evidence")
        if not isinstance(title, str) or not title.strip():
            diagnostics.append(_workflow_error("workflow step.title 必须描述该业务步骤。", f"{path}.title"))
        if not isinstance(goal, str) or not goal.strip():
            diagnostics.append(_workflow_error("workflow step.goal 必须说明该步骤在 Skill 工作流中的目标。", f"{path}.goal"))
        if not isinstance(evidence, str) or not evidence.strip():
            diagnostics.append(
                _workflow_error(
                    "workflow step.source_evidence 必须引用 SKILL.md 或 README.md 中支撑该步骤的内容。",
                    f"{path}.source_evidence",
                )
            )
        step_ids.add(step_id)

    wait_checkpoints = runtime_contract.get("wait_checkpoints")
    if isinstance(wait_checkpoints, list):
        checkpoint_step_ids = {
            item.get("workflow_step_id")
            for item in wait_checkpoints
            if isinstance(item, dict) and isinstance(item.get("workflow_step_id"), str)
        }
        missing_wait_steps = sorted(step_ids - checkpoint_step_ids)
        for step_id in missing_wait_steps:
            diagnostics.append(
                _workflow_error(
                    f"workflow step `{step_id}` 缺少 runtime_contract.wait_checkpoints 声明。",
                    "runtime_contract.wait_checkpoints",
                )
            )
    else:
        diagnostics.append(_workflow_error("runtime_contract.wait_checkpoints 必须是数组。", "runtime_contract.wait_checkpoints"))
    return diagnostics, step_ids


def _validate_workflow_nodes(nodes: list[Any], workflow_step_ids: set[str]) -> list[FormalDiagnostic]:
    if not workflow_step_ids:
        return []

    diagnostics: list[FormalDiagnostic] = []
    node_map = {str(node.get("id")): node for node in nodes if isinstance(node, dict) and node.get("id")}
    if any(step_id in node_map for step_id in workflow_step_ids):
        diagnostics.append(
            _workflow_error(
                "新编译产物不允许把 workflow step 直接编译为同名线性业务节点；必须使用 instruct_<step_id> / evaluate_<step_id> 结构。",
                "nodes",
            )
        )

    if "final_verify" not in node_map:
        diagnostics.append(_workflow_error("terminal(success) 前必须存在 final_verify 或等价最终验证节点。", "nodes.final_verify"))

    for step_id in sorted(workflow_step_ids):
        instruct_id = f"instruct_{step_id}"
        evaluate_id = f"evaluate_{step_id}"
        instruct = node_map.get(instruct_id)
        evaluate = node_map.get(evaluate_id)
        if not instruct or not evaluate:
            continue

        if instruct.get("kind") != "llm":
            diagnostics.append(_workflow_error(f"`{instruct_id}` 必须是 llm 指令节点。", f"nodes[{instruct_id}].kind"))
        instruct_interaction = instruct.get("interaction")
        if not isinstance(instruct_interaction, dict):
            diagnostics.append(_workflow_error(f"`{instruct_id}` 必须声明 interaction。", f"nodes[{instruct_id}].interaction"))
            instruct_interaction = {}
        if instruct_interaction.get("output_to_terminal") is not True:
            diagnostics.append(
                _workflow_error(f"`{instruct_id}` 必须 output_to_terminal=true。", f"nodes[{instruct_id}].interaction.output_to_terminal")
            )
        if instruct_interaction.get("wait_after_output") is not True:
            diagnostics.append(
                _workflow_error(f"`{instruct_id}` 必须 wait_after_output=true。", f"nodes[{instruct_id}].interaction.wait_after_output")
            )
        if instruct_interaction.get("resume_phase") != evaluate_id:
            diagnostics.append(
                _workflow_error(
                    f"`{instruct_id}` 的 resume_phase 必须指向 `{evaluate_id}`。",
                    f"nodes[{instruct_id}].interaction.resume_phase",
                )
            )
        expected_inputs = instruct_interaction.get("expected_inputs")
        if not isinstance(expected_inputs, list) or not expected_inputs:
            diagnostics.append(
                _workflow_error(f"`{instruct_id}` 必须声明 expected_inputs。", f"nodes[{instruct_id}].interaction.expected_inputs")
            )

        if evaluate.get("kind") != "llm":
            diagnostics.append(_workflow_error(f"`{evaluate_id}` 必须是 llm 评估节点。", f"nodes[{evaluate_id}].kind"))
        evaluate_interaction = evaluate.get("interaction")
        if not isinstance(evaluate_interaction, dict) or evaluate_interaction.get("evaluation") is not True:
            diagnostics.append(_workflow_error(f"`{evaluate_id}` 必须声明 interaction.evaluation=true。", f"nodes[{evaluate_id}].interaction"))
        projection = evaluate.get("projection")
        if not isinstance(projection, dict) or not projection.get("user_template"):
            diagnostics.append(_workflow_error(f"`{evaluate_id}` 必须包含 projection.user_template。", f"nodes[{evaluate_id}].projection"))

        for node_id, node in ((instruct_id, instruct), (evaluate_id, evaluate)):
            merge = node.get("merge")
            writes_own_observation = (
                isinstance(merge, list)
                and any(
                    isinstance(operation, dict)
                    and operation.get("op") == "set"
                    and operation.get("path") == f"observations.{node_id}"
                    and operation.get("from") == "observation"
                    for operation in merge
                )
            )
            if not writes_own_observation:
                diagnostics.append(
                    _workflow_error(
                        f"`{node_id}` 必须把 observation 写入 observations.{node_id}。",
                        f"nodes[{node_id}].merge",
                    )
                )
    return diagnostics


def _workflow_error(message: str, path: str) -> FormalDiagnostic:
    return FormalDiagnostic(
        severity="error",
        code="compile.workflow.not_extracted",
        message=message,
        location={"path": path},
    )


def _build_graph_summary(nodes: list[Any]) -> dict[str, Any]:
    normalized_nodes = [node for node in nodes if isinstance(node, dict)]
    workflow_nodes = [
        str(node.get("id"))
        for node in normalized_nodes
        if node.get("id") and str(node.get("id")) not in SCAFFOLD_NODE_IDS
    ]
    return {
        "node_count": len(normalized_nodes),
        "template": "formal-v5 skill workflow graph",
        "nodes": [str(node.get("id")) for node in normalized_nodes if node.get("id")],
        "workflow_nodes": workflow_nodes,
    }


def _build_capability_summary(nodes: list[Any], runtime_contract: Any) -> dict[str, Any]:
    normalized_nodes = [node for node in nodes if isinstance(node, dict)]
    llm_nodes = [node.get("id") for node in normalized_nodes if node.get("kind") == "llm"]
    tool_nodes = [node for node in normalized_nodes if node.get("kind") == "tool"]
    route_key = "default"
    if isinstance(runtime_contract, dict):
        route_key = str(runtime_contract.get("llm_route_key") or "default")
    return {
        "llm_route_key": route_key,
        "llm_nodes": llm_nodes,
        "tools": [_tool_name(node.get("actor")) or "psop.demo.inspect_input" for node in tool_nodes],
        "terminal_enabled": any(node.get("kind") == "terminal" for node in normalized_nodes),
    }
