from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from app.agent_harness.agents.psop.compiler.schemas import (
    COMPILER_EG_ARTIFACT_VIRTUAL_PATH,
    COMPILER_RESULT_VIRTUAL_PATH,
    validate_compiler_candidate,
    validator_status_from_counts,
)
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec
from app.domain.compiler.formal_v5 import (
    ARTIFACT_VERSION,
    DEFAULT_TOKEN_FIELDS,
    FORMAL_REVISION,
    SUPPORTED_ACTORS,
    SUPPORTED_NODE_KINDS,
    SUPPORTED_TOOLS,
    validate_and_normalize_artifact,
)


GUARD_OPS = ["always", "phase_is", "field_exists", "field_equals", "all", "any", "not"]
MERGE_OPS = ["set"]
SCAFFOLD_ARTIFACT_VIRTUAL_PATH = "/mnt/psop/workspace/compiler-scaffold-artifact.json"
SCAFFOLD_CANDIDATE_VIRTUAL_PATH = "/mnt/psop/workspace/compiler-scaffold-candidate.json"


def register_compiler_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="psop.compiler.read_skill_source",
            description="读取本次编译请求中的冻结 Skill source 和已镜像参考资产索引。",
            purpose="用于 psop.compiler 从 invocation context 获取 README.md、SKILL.md、source 摘要和 source.reference_assets，不直接访问 GitLab。",
            input_schema={
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 80000},
                },
                "additionalProperties": False,
            },
            max_result_chars=80000,
        ),
        _read_skill_source,
    )
    registry.register(
        ToolSpec(
            name="psop.compiler.read_manifest_snapshot",
            description="读取本次编译请求中的 manifest snapshot 和 runtime policy snapshot 摘要。",
            purpose="用于 psop.compiler 建立平台发布版本事实边界，不返回数据库对象内部字段。",
            input_schema={
                "type": "object",
                "properties": {"include_runtime_policy": {"type": "boolean", "default": True}},
                "additionalProperties": False,
            },
            max_result_chars=40000,
        ),
        _read_manifest_snapshot,
    )
    registry.register(
        ToolSpec(
            name="psop.compiler.read_allowed_runtime",
            description="读取当前 compiler 可使用的 formal-v5 Runtime 支持白名单。",
            purpose="用于 psop.compiler 限制 node kind、actor、tool、guard、merge 和 token 字段。",
            input_schema={
                "type": "object",
                "properties": {"formal_revision": {"type": "string", "enum": [FORMAL_REVISION]}},
                "additionalProperties": False,
            },
            max_result_chars=24000,
        ),
        _read_allowed_runtime,
    )
    registry.register(
        ToolSpec(
            name="psop.compiler.read_domain_pack",
            description="读取本次编译请求可用的 domain pack 语义参考。",
            purpose="用于 psop.compiler 理解领域术语和质量参考；domain pack 不能改变 formal-v5 或 Runtime 白名单。",
            input_schema={
                "type": "object",
                "properties": {
                    "detail_level": {"type": "string", "enum": ["metadata", "summary", "full"], "default": "summary"},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 24000},
                },
                "additionalProperties": False,
            },
            max_result_chars=24000,
        ),
        _read_domain_pack,
    )
    registry.register(
        ToolSpec(
            name="psop.compiler.validate_formal_v5",
            description="使用确定性 validator 校验 formal-v5 PSOP-EG candidate。",
            purpose="用于 psop.compiler 在提交前获取权威 formal-v5 diagnostics 和 normalized summary。",
            input_schema={
                "type": "object",
                "properties": {
                    "artifact": {"type": "object"},
                    "artifact_ref": {"type": "string"},
                    "candidate_ref": {"type": "string"},
                    "validation_profile": {
                        "type": "string",
                        "enum": ["mvp_runtime", "strict_formal_v5"],
                        "default": "mvp_runtime",
                    },
                    "include_normalized_summary": {"type": "boolean", "default": True},
                },
                "additionalProperties": False,
            },
            risk_class="compute_only",
            max_result_chars=30000,
        ),
        _validate_formal_v5,
    )
    registry.register(
        ToolSpec(
            name="psop.compiler.build_formal_v5_scaffold",
            description="根据抽取出的 workflow steps 机械生成合法 formal-v5 PSOP-EG scaffold。",
            purpose=(
                "用于 psop.compiler 避免手写完整 EG JSON；模型只提供业务 workflow 语义，"
                "工具生成 nodes、guards、merges、wait checkpoints、dependency view、reference_images 和 compiler candidate。"
            ),
            input_schema={
                "type": "object",
                "required": ["execution_goal", "workflow_steps"],
                "properties": {
                    "execution_goal": {"type": "string", "minLength": 1},
                    "applicability": {"type": "object"},
                    "workflow_steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "reference_images": {"type": "array", "items": {"type": "object"}},
                            },
                        },
                        "minItems": 1,
                        "maxItems": 50,
                    },
                    "safety_constraints": {"type": "array", "items": {"type": "string"}},
                    "completion_criteria": {"type": "array", "items": {"type": "string"}},
                    "recovery_paths": {"type": "array", "items": {"type": "object"}},
                    "compile_reason": {"type": "string"},
                    "diagnostics": {"type": "array", "items": {"type": "object"}},
                    "source_map": {"type": "array", "items": {"type": "object"}},
                    "llm_route_key": {"type": "string"},
                },
                "additionalProperties": False,
            },
            risk_class="compute_only",
            max_result_chars=12000,
        ),
        _build_formal_v5_scaffold,
    )
    registry.register(
        ToolSpec(
            name="psop.compiler.submit_candidate",
            description="提交并校验 PSOP-EG compiler candidate。",
            purpose="用于 psop.compiler 写入 compiler-result.json 和 eg.compile.artifact.json；不提交 GitLab，不写数据库，不发布 ready artifact。",
            input_schema={
                "type": "object",
                "properties": {
                    "candidate_ref": {"type": "string"},
                    "artifact": {"type": "object"},
                    "compile_reason": {"type": "string", "minLength": 1},
                    "source_map": {"type": "array", "items": {"type": "object"}},
                    "diagnostics": {"type": "array", "items": {"type": "object"}},
                    "repair_history": {"type": "array", "items": {"type": "object"}},
                    "validator_summary": {"type": "object"},
                },
                "additionalProperties": False,
            },
            risk_class="write_local",
            side_effect_class="write_sandbox_file",
            resource_scope="sandbox_outputs",
            audit_event="agent.artifact.created",
            max_result_chars=8000,
        ),
        _submit_candidate,
    )


def allowed_runtime_snapshot() -> dict[str, Any]:
    return {
        "formal_revision": FORMAL_REVISION,
        "artifact_version": ARTIFACT_VERSION,
        "node_kinds": sorted(SUPPORTED_NODE_KINDS),
        "actors": sorted(SUPPORTED_ACTORS),
        "tools": sorted(SUPPORTED_TOOLS),
        "guard_ops": GUARD_OPS,
        "merge_ops": MERGE_OPS,
        "token_fields": sorted(DEFAULT_TOKEN_FIELDS),
        "policy_limits": {
            "selection": "priority_then_order",
            "minimum_llm_calls": "2 * workflow_steps.length + 1",
        },
        "formal_v5_contract": {
            "recommended_builder_tool": "psop.compiler.build_formal_v5_scaffold",
            "workflow_step_node_pattern": ["instruct_<step_id>", "evaluate_<step_id>"],
            "workflow_step_optional_fields": ["reference_images"],
            "required_runtime_contract_fields": [
                "execution_goal",
                "applicability",
                "workflow_steps",
                "expected_evidence",
                "safety_constraints",
                "wait_checkpoints",
                "completion_criteria",
                "recovery_paths",
            ],
            "required_node_invariants": [
                "每个 node 必须包含 id、kind、actor、guard、merge。",
                "每个 instruct_<step_id> 必须是 llm 节点，output_to_terminal=true，wait_after_output=true。",
                "每个 instruct_<step_id> 的 resume_phase 必须指向 evaluate_<step_id>。",
                "每个 instruct/evaluate 节点必须把 observation 写入 observations.<node_id>。",
                "每个 evaluate_<step_id> 必须是 llm 节点，interaction.evaluation=true，并包含 projection.user_template。",
                "terminal(success) 前必须存在 final_verify。",
            ],
        },
        "unsupported_features": ["approval", "timer", "skill"],
    }


def _read_skill_source(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    source = _source_payload(context)
    files = _source_files(source)
    if not files:
        return _error_result("not_found", "本次 invocation context 中缺少 frozen source。", ["psop.compiler.read_skill_source"])
    requested = arguments.get("paths")
    paths = [str(path) for path in requested] if isinstance(requested, list) and requested else sorted(files)
    max_chars = _bounded_int(arguments.get("max_chars"), default=80000, minimum=1000, maximum=80000)
    selected: dict[str, dict[str, Any]] = {}
    for path in paths:
        if path not in files:
            return _error_result("not_found", f"frozen source 中不存在文件：{path}", ["psop.compiler.read_skill_source"])
        content = files[path]
        selected[path] = {"content": _truncate(content, max_chars), "truncated": len(content) > max_chars}
    reference_assets = _source_reference_assets(source)
    source_summary = _source_summary(files)
    source_summary["reference_asset_count"] = len(reference_assets)
    return {
        "status": "success",
        "summary": f"读取 frozen Skill source：{', '.join(selected)}。",
        "source_commit_sha": _source_commit_sha(context, source),
        "files": selected,
        "source_summary": source_summary,
        "reference_assets": reference_assets,
        "trust_level": "frozen_source",
        "truncated": any(item["truncated"] for item in selected.values()),
        "next_valid_actions": ["psop.compiler.read_manifest_snapshot", "psop.compiler.read_allowed_runtime"],
    }


def _read_manifest_snapshot(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    manifest = _context(context).get("manifest_snapshot")
    if not isinstance(manifest, dict):
        manifest = _input(context).get("manifest_snapshot")
    if not isinstance(manifest, dict):
        return _error_result("not_found", "本次 invocation context 中缺少 manifest_snapshot。", ["psop.compiler.read_manifest_snapshot"])
    include_runtime_policy = bool(arguments.get("include_runtime_policy", True))
    skill = _dict_value(_context(context), "skill") or _dict_value(_input(context), "skill")
    runtime_policy = _context(context).get("runtime_policy_snapshot") if include_runtime_policy else {}
    if not isinstance(runtime_policy, dict):
        runtime_policy = {}
    payload = {
        "status": "success",
        "summary": "已读取 manifest snapshot。",
        "skill_identity": skill or manifest.get("identity") or {},
        "compile_config": manifest.get("compile_config") or {},
        "runtime_policy_snapshot": runtime_policy,
        "capability_summary": manifest.get("capability_summary") or manifest.get("capabilities") or {},
        "manifest_hash": hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "truncated": False,
        "trust_level": "platform_snapshot",
        "next_valid_actions": ["psop.compiler.read_allowed_runtime", "psop.compiler.read_domain_pack"],
    }
    return _limit_json_payload(payload, 40000)


def _read_allowed_runtime(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    requested_revision = str(arguments.get("formal_revision") or FORMAL_REVISION)
    if requested_revision != FORMAL_REVISION:
        return _error_result("invalid_arguments", f"不支持的 formal_revision：{requested_revision}", ["psop.compiler.read_allowed_runtime"])
    configured = _context(context).get("allowed_runtime")
    snapshot = configured if isinstance(configured, dict) and configured else allowed_runtime_snapshot()
    return {
        "status": "success",
        "summary": "已读取 formal-v5 Runtime 支持白名单。",
        **snapshot,
        "truncated": False,
        "next_valid_actions": ["psop.compiler.build_formal_v5_scaffold", "psop.compiler.validate_formal_v5", "workspace.write_text"],
    }


def _read_domain_pack(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    domain_pack = _context(context).get("domain_pack")
    detail_level = str(arguments.get("detail_level") or "summary")
    max_chars = _bounded_int(arguments.get("max_chars"), default=24000, minimum=1000, maximum=24000)
    if not isinstance(domain_pack, dict) or not domain_pack:
        return {
            "status": "success",
            "summary": "本次编译未配置 domain pack，继续使用通用规则。",
            "domain_pack_ref": "",
            "metadata": {},
            "guidance_summary": "",
            "guidance": "",
            "truncated": False,
            "trust_level": "semi_trusted_reference",
            "next_valid_actions": ["psop.compiler.validate_formal_v5", "workspace.write_text"],
        }
    guidance = str(domain_pack.get("guidance") or "")
    guidance_summary = str(domain_pack.get("guidance_summary") or domain_pack.get("summary") or "")
    metadata = domain_pack.get("metadata") if isinstance(domain_pack.get("metadata"), dict) else {}
    if detail_level == "metadata":
        guidance = ""
    elif detail_level == "summary" and not guidance_summary:
        guidance_summary = _truncate(guidance, min(max_chars, 4000))
        guidance = ""
    else:
        guidance = _truncate(guidance, max_chars)
    return {
        "status": "success",
        "summary": "已读取 domain pack 语义参考。",
        "domain_pack_ref": str(domain_pack.get("domain_pack_ref") or metadata.get("domain_pack_key") or ""),
        "metadata": metadata,
        "guidance_summary": guidance_summary,
        "guidance": guidance,
        "truncated": bool(domain_pack.get("guidance") and len(str(domain_pack.get("guidance"))) > max_chars),
        "trust_level": "semi_trusted_reference",
        "next_valid_actions": ["psop.compiler.validate_formal_v5", "workspace.write_text"],
    }


def _validate_formal_v5(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    context.event_writer.record("agent.validation.started", {"tool_name": "psop.compiler.validate_formal_v5"})
    started = time.perf_counter()
    try:
        artifact = _artifact_from_arguments(arguments, context)
        validation = validate_and_normalize_artifact(artifact)
    except Exception as exc:
        context.event_writer.record(
            "agent.validation.failed",
            {"tool_name": "psop.compiler.validate_formal_v5", "error_type": exc.__class__.__name__, "error": str(exc)},
        )
        return _error_result("internal_error", str(exc), ["psop.compiler.validate_formal_v5"])
    diagnostics = [item.as_dict() for item in validation.diagnostics]
    error_count = sum(1 for item in validation.diagnostics if item.severity == "error")
    warning_count = sum(1 for item in validation.diagnostics if item.severity == "warning")
    duration_ms = int((time.perf_counter() - started) * 1000)
    summary = _normalized_summary(validation.artifact or artifact, error_count, warning_count)
    context.event_writer.record(
        "agent.validation.completed",
        {
            "tool_name": "psop.compiler.validate_formal_v5",
            "valid": validation.artifact is not None and not validation.has_errors,
            "error_count": error_count,
            "warning_count": warning_count,
            "duration_ms": duration_ms,
        },
    )
    return {
        "status": "success",
        "valid": validation.artifact is not None and not validation.has_errors,
        "diagnostics": diagnostics,
        "normalized_summary": summary if bool(arguments.get("include_normalized_summary", True)) else {},
        "truncated": False,
        "next_valid_actions": ["psop.compiler.submit_candidate", "psop.compiler.build_formal_v5_scaffold", "workspace.write_text"],
    }


def _build_formal_v5_scaffold(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    raw_steps = arguments.get("workflow_steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return _error_result(
            "invalid_arguments",
            "workflow_steps 必须是非空数组。",
            ["psop.compiler.build_formal_v5_scaffold"],
        )
    try:
        workflow_steps = _normalize_scaffold_steps(raw_steps)
    except ValueError as exc:
        return _error_result(
            "invalid_arguments",
            str(exc),
            ["psop.compiler.build_formal_v5_scaffold"],
        )

    execution_goal = _non_empty_text(arguments.get("execution_goal"), "帮助用户按 Skill source 完成现实世界任务。")
    safety_constraints = _string_list(
        arguments.get("safety_constraints"),
        ["如果用户证据显示存在安全风险，应暂停并要求补充证据或人工介入。"],
    )
    completion_criteria = _string_list(
        arguments.get("completion_criteria"),
        ["所有 workflow steps 均完成，现场证据满足完成标准，final_verify 输出成功结论。"],
    )
    recovery_paths = arguments.get("recovery_paths")
    if not isinstance(recovery_paths, list) or not recovery_paths:
        recovery_paths = [{"when": "evidence_insufficient", "action": "request_more_evidence"}]
    applicability = arguments.get("applicability")
    if not isinstance(applicability, dict) or not applicability:
        applicability = {
            "applies_when": ["用户处在真实任务现场，并能按阶段提交文字、图片或文件证据。"],
            "does_not_apply_when": ["任务超出 Skill source 定义边界，或存在无法通过远程协作控制的安全风险。"],
        }

    artifact = _scaffold_artifact(
        context=context,
        execution_goal=execution_goal,
        applicability=applicability,
        workflow_steps=workflow_steps,
        safety_constraints=safety_constraints,
        completion_criteria=completion_criteria,
        recovery_paths=recovery_paths,
        llm_route_key=_non_empty_text(arguments.get("llm_route_key"), "text"),
    )
    validation = validate_and_normalize_artifact(artifact)
    diagnostics = [item.as_dict() for item in validation.diagnostics]
    error_count = sum(1 for item in validation.diagnostics if item.severity == "error")
    warning_count = sum(1 for item in validation.diagnostics if item.severity == "warning")
    if validation.artifact is None or validation.has_errors:
        context.event_writer.record(
            "agent.scaffold.failed",
            {
                "tool_name": "psop.compiler.build_formal_v5_scaffold",
                "error_count": error_count,
                "warning_count": warning_count,
            },
        )
        return {
            "status": "error",
            "type": "validation_failed",
            "message": "formal-v5 scaffold 生成后未通过 validator。",
            "diagnostics": diagnostics,
            "truncated": False,
            "next_valid_actions": ["psop.compiler.build_formal_v5_scaffold"],
        }

    artifact_payload = validation.artifact
    source_map = _scaffold_source_map(workflow_steps, arguments.get("source_map"))
    candidate = {
        "artifact": artifact_payload,
        "compile_reason": _non_empty_text(
            arguments.get("compile_reason"),
            "根据 frozen Skill source 抽取 workflow steps，并由 psop.compiler.build_formal_v5_scaffold 机械展开为 formal-v5 PSOP-EG candidate。",
        ),
        "source_map": source_map,
        "diagnostics": _diagnostic_list(arguments.get("diagnostics")),
        "repair_history": [
            {
                "stage": "scaffold_generation",
                "summary": "使用结构化 scaffold tool 生成 nodes、guards、merges、wait checkpoints 和 dependency graph，避免模型手写 formal-v5 控制结构。",
            }
        ],
        "validator_summary": {
            "status": validator_status_from_counts(error_count),
            "error_count": error_count,
            "warning_count": warning_count,
        },
    }
    artifact_ref = _write_json_ref(context, SCAFFOLD_ARTIFACT_VIRTUAL_PATH, artifact_payload)
    candidate_ref = _write_json_ref(context, SCAFFOLD_CANDIDATE_VIRTUAL_PATH, candidate)
    context.event_writer.record(
        "agent.scaffold.created",
        {
            "artifact_ref": artifact_ref,
            "candidate_ref": candidate_ref,
            "formal_revision": artifact_payload.get("formal_revision"),
            "node_count": len(artifact_payload.get("nodes") or []),
            "workflow_step_count": len(workflow_steps),
        },
    )
    result = {
        "status": "success",
        "summary": f"已生成 formal-v5 scaffold：{len(workflow_steps)} 个 workflow steps，{len(artifact_payload.get('nodes') or [])} 个 nodes。",
        "artifact_ref": artifact_ref,
        "candidate_ref": candidate_ref,
        "candidate_summary": {
            "compile_reason": candidate["compile_reason"],
            "source_map_count": len(source_map),
            "diagnostic_count": len(candidate["diagnostics"]),
            "repair_history_count": len(candidate["repair_history"]),
        },
        "validation_summary": _normalized_summary(artifact_payload, error_count, warning_count),
        "truncated": False,
        "next_valid_actions": ["psop.compiler.validate_formal_v5", "psop.compiler.submit_candidate"],
    }
    if bool(arguments.get("include_full_candidate")):
        result["full_candidate_omitted"] = True
        result["omission_reason"] = "scaffold 大对象已写入 sandbox；后续工具必须使用 artifact_ref 或 candidate_ref。"
    return result


def _submit_candidate(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    try:
        candidate = _candidate_from_arguments(arguments, context)
    except ValueError as exc:
        return _error_result("validation_failed", str(exc), ["psop.compiler.submit_candidate", "psop.compiler.validate_formal_v5"])
    validation = validate_and_normalize_artifact(candidate.artifact)
    diagnostics = [item.as_dict() for item in validation.diagnostics]
    error_count = sum(1 for item in validation.diagnostics if item.severity == "error")
    warning_count = sum(1 for item in validation.diagnostics if item.severity == "warning")
    artifact_payload = validation.artifact if validation.artifact is not None and not validation.has_errors else candidate.artifact
    payload = candidate.model_dump(mode="json")
    payload["artifact"] = artifact_payload
    payload["submission_validation"] = {
        "status": validator_status_from_counts(error_count),
        "error_count": error_count,
        "warning_count": warning_count,
        "diagnostics": diagnostics,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    artifact_content = json.dumps(artifact_payload, ensure_ascii=False, indent=2, sort_keys=True)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    artifact_hash = hashlib.sha256(artifact_content.encode("utf-8")).hexdigest()
    context.sandbox.write_text(COMPILER_RESULT_VIRTUAL_PATH, content)
    context.sandbox.write_text(COMPILER_EG_ARTIFACT_VIRTUAL_PATH, artifact_content)
    context.event_writer.record(
        "agent.artifact.created",
        {
            "artifact_type": "eg_compile_candidate",
            "artifact_ref": "sandbox://outputs/compiler-result.json",
            "content_hash": content_hash,
            "validation_status": payload["submission_validation"]["status"],
        },
    )
    context.event_writer.record(
        "agent.artifact.created",
        {
            "artifact_type": "eg_compile_artifact_candidate",
            "artifact_ref": "sandbox://outputs/eg.compile.artifact.json",
            "content_hash": artifact_hash,
            "formal_revision": artifact_payload.get("formal_revision") if isinstance(artifact_payload, dict) else "",
        },
    )
    return {
        "status": "success",
        "artifact_ref": "sandbox://outputs/compiler-result.json",
        "eg_artifact_ref": "sandbox://outputs/eg.compile.artifact.json",
        "content_hash": content_hash,
        "eg_content_hash": artifact_hash,
        "validation_summary": _normalized_summary(artifact_payload, error_count, warning_count),
        "next_valid_actions": [],
    }


def _artifact_from_arguments(arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
    artifact = arguments.get("artifact")
    if isinstance(artifact, dict) and artifact:
        return artifact
    candidate_ref = arguments.get("candidate_ref")
    if isinstance(candidate_ref, str) and candidate_ref.strip():
        candidate_payload = _read_json_ref(context, candidate_ref)
        candidate = validate_compiler_candidate(candidate_payload)
        return candidate.artifact
    artifact_ref = arguments.get("artifact_ref")
    if isinstance(artifact_ref, str) and artifact_ref.strip():
        return _read_json_ref(context, artifact_ref)
    raise ValueError("必须提供 artifact、artifact_ref 或 candidate_ref。")


def _candidate_from_arguments(arguments: dict[str, Any], context: ToolExecutionContext):
    candidate_ref = arguments.get("candidate_ref")
    if isinstance(candidate_ref, str) and candidate_ref.strip():
        payload = _read_json_ref(context, candidate_ref)
        return validate_compiler_candidate(payload)
    return validate_compiler_candidate(arguments)


def _write_json_ref(context: ToolExecutionContext, virtual_path: str, payload: dict[str, Any]) -> str:
    context.sandbox.write_text(virtual_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return _sandbox_ref_from_virtual_path(virtual_path)


def _read_json_ref(context: ToolExecutionContext, ref: str) -> Any:
    virtual_path = _virtual_path_from_ref(ref)
    try:
        return json.loads(context.sandbox.read_text(virtual_path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"sandbox JSON ref 不是合法 JSON：{ref}") from exc


def _virtual_path_from_ref(ref: str) -> str:
    value = ref.strip()
    if value.startswith("sandbox://"):
        suffix = value.removeprefix("sandbox://").lstrip("/")
        if not suffix:
            raise ValueError("sandbox ref 不能为空。")
        return f"/mnt/psop/{suffix}"
    if value.startswith("/mnt/psop/"):
        return value
    raise ValueError("只支持 sandbox:// 或 /mnt/psop/ 引用。")


def _sandbox_ref_from_virtual_path(virtual_path: str) -> str:
    if not virtual_path.startswith("/mnt/psop/"):
        raise ValueError("sandbox virtual path 必须位于 /mnt/psop/。")
    return f"sandbox://{virtual_path.removeprefix('/mnt/psop/').lstrip('/')}"


def _context(context: ToolExecutionContext) -> dict[str, Any]:
    return context.invocation_context or {}


def _input(context: ToolExecutionContext) -> dict[str, Any]:
    return context.invocation_input or {}


def _source_payload(context: ToolExecutionContext) -> dict[str, Any]:
    source = _context(context).get("source")
    if not isinstance(source, dict):
        source = _input(context).get("source")
    return source if isinstance(source, dict) else {}


def _source_files(source: dict[str, Any]) -> dict[str, str]:
    files: dict[str, str] = {}
    raw_files = source.get("files")
    if isinstance(raw_files, dict):
        for path, value in raw_files.items():
            if isinstance(value, dict):
                files[str(path)] = str(value.get("content") or "")
            elif isinstance(value, str):
                files[str(path)] = value
    for path in ("README.md", "SKILL.md"):
        if path in source and isinstance(source[path], str):
            files[path] = source[path]
    if "readme_content" in source:
        files.setdefault("README.md", str(source.get("readme_content") or ""))
    if "skill_md_content" in source:
        files.setdefault("SKILL.md", str(source.get("skill_md_content") or ""))
    return {path: content for path, content in files.items() if content}


def _source_commit_sha(context: ToolExecutionContext, source: dict[str, Any]) -> str:
    compile_request = _context(context).get("compile_request")
    if isinstance(compile_request, dict) and compile_request.get("source_commit_sha"):
        return str(compile_request["source_commit_sha"])
    return str(source.get("source_commit_sha") or source.get("head_commit_sha") or "")


def _source_summary(files: dict[str, str]) -> dict[str, Any]:
    return {
        "file_count": len(files),
        "files": [{"path": path, "chars": len(content)} for path, content in sorted(files.items())],
    }


def _normalize_scaffold_steps(raw_steps: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            raise ValueError(f"workflow_steps[{index - 1}] 必须是对象。")
        title = _non_empty_text(raw_step.get("title") or raw_step.get("name"), f"步骤 {index}")
        raw_step_id = raw_step.get("id") or raw_step.get("step_id") or title
        step_id = _safe_step_id(str(raw_step_id), index=index, used_ids=used_ids)
        used_ids.add(step_id)
        source_evidence = _non_empty_text(
            raw_step.get("source_evidence")
            or raw_step.get("source_excerpt")
            or raw_step.get("source_summary"),
            f"SKILL.md 中描述了「{title}」阶段的用户动作、证据和完成标准。",
        )
        expected_evidence = _normalize_expected_evidence(raw_step.get("expected_evidence"))
        normalized_step = {
            "id": step_id,
            "title": title,
            "goal": _non_empty_text(raw_step.get("goal"), f"完成「{title}」并收集可验证现场证据。"),
            "source_evidence": source_evidence,
            "expected_evidence": expected_evidence,
            "source_file": _non_empty_text(raw_step.get("source_file"), "SKILL.md"),
        }
        for optional_key in ("preconditions", "completion_criteria", "stop_conditions", "recovery_path"):
            value = raw_step.get(optional_key)
            if value not in (None, "", [], {}):
                normalized_step[optional_key] = value
        reference_images = _normalize_reference_images(raw_step.get("reference_images"), step_id=step_id)
        if reference_images:
            normalized_step["reference_images"] = reference_images
        normalized.append(normalized_step)
    return normalized


def _source_reference_assets(source: dict[str, Any]) -> list[dict[str, Any]]:
    raw_assets = source.get("reference_assets")
    if not isinstance(raw_assets, list):
        return []
    assets: list[dict[str, Any]] = []
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        reference_path = str(item.get("reference_path") or "").strip()
        artifact_object_id = str(item.get("artifact_object_id") or "").strip()
        mime_type = str(item.get("mime_type") or "").strip()
        if not reference_path or not artifact_object_id or not mime_type:
            continue
        assets.append(
            {
                "reference_path": reference_path,
                "artifact_object_id": artifact_object_id,
                "mime_type": mime_type,
                "title": str(item.get("title") or ""),
                "source_ref": str(item.get("source_ref") or ""),
                "display_order": _bounded_int(item.get("display_order"), default=len(assets) + 1, minimum=0, maximum=1000),
            }
        )
    return assets


def _normalize_reference_images(raw_value: Any, *, step_id: str) -> list[dict[str, Any]]:
    if isinstance(raw_value, dict):
        raw_items = []
        for key, value in raw_value.items():
            if isinstance(value, dict):
                raw_items.append({**value, "reference_image_ref": value.get("reference_image_ref") or str(key)})
    elif isinstance(raw_value, list):
        raw_items = raw_value
    else:
        raw_items = []

    normalized: list[dict[str, Any]] = []
    used_refs: set[str] = set()
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        title = _non_empty_text(item.get("title"), _title_from_reference_image_item(item, index))
        reference_image_ref = str(item.get("reference_image_ref") or item.get("ref") or "").strip()
        if not reference_image_ref:
            reference_image_ref = f"skill-reference://steps/{step_id}/{_safe_reference_slug(title, index)}"
        if reference_image_ref in used_refs:
            reference_image_ref = f"{reference_image_ref}-{index}"
        used_refs.add(reference_image_ref)
        normalized.append(
            {
                "reference_image_ref": reference_image_ref,
                "title": title,
                "caption": str(item.get("caption") or ""),
                "artifact_object_id": str(item.get("artifact_object_id") or ""),
                "mime_type": str(item.get("mime_type") or ""),
                "source_ref": str(item.get("source_ref") or ""),
                "display_order": _bounded_int(item.get("display_order"), default=index, minimum=0, maximum=1000),
            }
        )
    return normalized


def _title_from_reference_image_item(item: dict[str, Any], index: int) -> str:
    path = str(item.get("reference_path") or "")
    if path:
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("-", " ").replace("_", " ")
        if stem.strip():
            return stem.strip()
    return f"参考图 {index}"


def _safe_reference_slug(value: str, index: int) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or f"image-{index}"


def _normalize_expected_evidence(raw_value: Any) -> list[dict[str, Any]]:
    values = raw_value if isinstance(raw_value, list) else []
    normalized: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict):
            kind = _non_empty_text(item.get("kind"), "text")
            evidence = {
                "kind": kind,
                "event_kind": _non_empty_text(item.get("event_kind"), _event_kind_for_evidence(kind)),
            }
            if item.get("description"):
                evidence["description"] = str(item["description"])
            normalized.append(evidence)
        elif isinstance(item, str) and item.strip():
            normalized.append(
                {
                    "kind": "text",
                    "event_kind": "terminal.text.input.v1",
                    "description": item.strip(),
                }
            )
    if normalized:
        return normalized
    return [
        {"kind": "text", "event_kind": "terminal.text.input.v1", "description": "现场状态文字说明。"},
        {"kind": "image", "event_kind": "terminal.image.input.v1", "description": "关键步骤照片或截图。"},
    ]


def _scaffold_artifact(
    *,
    context: ToolExecutionContext,
    execution_goal: str,
    applicability: dict[str, Any],
    workflow_steps: list[dict[str, Any]],
    safety_constraints: list[str],
    completion_criteria: list[str],
    recovery_paths: list[Any],
    llm_route_key: str,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [_start_node(workflow_steps[0]["id"])]
    dependency_graph_for_view: list[dict[str, str]] = [{"from": "start", "to": f"instruct_{workflow_steps[0]['id']}"}]
    for index, step in enumerate(workflow_steps):
        step_id = step["id"]
        next_phase = f"instruct_{workflow_steps[index + 1]['id']}" if index + 1 < len(workflow_steps) else "final_verify"
        nodes.append(_instruct_node(step))
        nodes.append(_evaluate_node(step, next_phase))
        dependency_graph_for_view.append({"from": f"instruct_{step_id}", "to": f"evaluate_{step_id}"})
        dependency_graph_for_view.append({"from": f"evaluate_{step_id}", "to": next_phase})
    nodes.append(_final_verify_node())
    nodes.append(_terminal_node())
    dependency_graph_for_view.append({"from": "final_verify", "to": "terminal"})

    return {
        "artifact_version": ARTIFACT_VERSION,
        "formal_revision": FORMAL_REVISION,
        "skill": _dict_value(_context(context), "skill") or _dict_value(_input(context), "skill"),
        "schema": {
            "token_fields": sorted(DEFAULT_TOKEN_FIELDS),
            "input_name": "user_input",
            "output_name": "final_response",
        },
        "nodes": nodes,
        "init": {"entry_node": "start"},
        "halt": {"success": {"field_equals": {"path": "status", "value": "success"}}},
        "policies": {"selection": "priority_then_order", "max_steps": max(10, 2 * len(workflow_steps) + 6)},
        "dependency_graph_for_view": dependency_graph_for_view,
        "runtime_contract": {
            "llm_route_key": llm_route_key,
            "skill_instruction": "严格遵循 frozen SKILL.md 中的现实世界协作流程；每次只推进当前阶段，等待用户证据后再判断是否进入下一阶段。",
            "execution_goal": execution_goal,
            "applicability": applicability,
            "workflow_steps": [
                {
                    key: value
                    for key, value in step.items()
                    if key not in {"expected_evidence", "source_file"}
                }
                for step in workflow_steps
            ],
            "expected_evidence": {step["id"]: step["expected_evidence"] for step in workflow_steps},
            "safety_constraints": safety_constraints,
            "wait_checkpoints": [
                {
                    "checkpoint_id": f"{step['id']}_evidence",
                    "workflow_step_id": step["id"],
                    "expected_inputs": [
                        {"kind": item.get("kind", "text"), "event_kind": item.get("event_kind", _event_kind_for_evidence(str(item.get("kind") or "text")))}
                        for item in step["expected_evidence"]
                    ],
                }
                for step in workflow_steps
            ],
            "completion_criteria": completion_criteria,
            "recovery_paths": recovery_paths,
        },
    }


def _start_node(first_step_id: str) -> dict[str, Any]:
    return {
        "id": "start",
        "kind": "start",
        "guard": {"phase_is": "start"},
        "actor": {"name": "runtime.start"},
        "merge": [
            {"op": "set", "path": "observations.start", "from": "observation"},
            {"op": "set", "path": "phase", "value": f"instruct_{first_step_id}"},
        ],
        "policy": {"priority": 10},
    }


def _instruct_node(step: dict[str, Any]) -> dict[str, Any]:
    step_id = step["id"]
    node_id = f"instruct_{step_id}"
    return {
        "id": node_id,
        "kind": "llm",
        "guard": {"phase_is": node_id},
        "actor": {"name": "agent.llm"},
        "interaction": {
            "output_to_terminal": True,
            "wait_after_output": True,
            "checkpoint_id": f"{step_id}_evidence",
            "workflow_step_id": step_id,
            "wait_reason": f"等待用户提交「{step['title']}」的现场证据。",
            "expected_inputs": step["expected_evidence"],
            "resume_phase": f"evaluate_{step_id}",
        },
        "projection": {
            "system_template": f"你是 PSOP Runtime 当前阶段指令节点：{step['title']}。只输出当前阶段可执行动作、证据要求和安全提醒。",
            "user_template": (
                f"当前阶段：{step['title']}\n"
                f"阶段目标：{step['goal']}\n"
                f"source evidence：{step['source_evidence']}\n"
                "要求：不要一次性输出后续阶段；说明本阶段需要用户提交的证据；发现安全风险时要求暂停。\n"
                "当前 Token：{{token}}"
            ),
        },
        "merge": [{"op": "set", "path": f"observations.{node_id}", "from": "observation"}],
        "policy": {"priority": 20},
    }


def _evaluate_node(step: dict[str, Any], next_phase: str) -> dict[str, Any]:
    step_id = step["id"]
    node_id = f"evaluate_{step_id}"
    return {
        "id": node_id,
        "kind": "llm",
        "guard": {"phase_is": node_id},
        "actor": {"name": "agent.llm"},
        "interaction": {
            "evaluation": True,
            "transitions": {
                "proceed": next_phase,
                "complete": "terminal",
                "abort": "terminal",
            },
        },
        "projection": {
            "system_template": "你是 PSOP Runtime 证据评估节点。只输出 JSON decision，字段包含 decision、reason、terminal_message；next_phase 是兼容字段，可留空。",
            "user_template": (
                f"评估阶段：{step['title']}\n"
                f"完成目标：{step['goal']}\n"
                f"期望证据：{json.dumps(step['expected_evidence'], ensure_ascii=False)}\n"
                f"如果证据充分且无安全风险，decision 必须是 `proceed`，Runtime 会进入 `{next_phase}`；"
                f"如果证据不足，decision 必须是 `need_more_evidence` 或 `retry`，Runtime 会继续等待 `{step_id}` 的证据；"
                "如果存在不可恢复安全风险，decision 必须是 `abort` 且 terminal_message 说明终止原因。\n"
                "当前 Token：{{token}}"
            ),
        },
        "merge": [{"op": "set", "path": f"observations.{node_id}", "from": "observation"}],
        "policy": {"priority": 30},
    }


def _final_verify_node() -> dict[str, Any]:
    return {
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
            "system_template": "你是 PSOP Runtime 最终验证节点 final_verify。只输出 JSON object，字段包含 decision、reason、terminal_message；next_phase 是兼容字段，可留空。",
            "user_template": (
                "根据 runtime_contract.completion_criteria、所有 workflow step 观察结果和当前 Token 做最终验证。"
                "通过时 decision=`complete`，terminal_message 给出完成结论；未通过时说明缺口并回到相应等待点。\n"
                "当前 Token：{{token}}"
            ),
        },
        "merge": [
            {"op": "set", "path": "observations.final_verify", "from": "observation"},
            {"op": "set", "path": "outputs.final_response", "from": "observation.terminal_message"},
        ],
        "policy": {"priority": 40},
    }


def _terminal_node() -> dict[str, Any]:
    return {
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
    }


def _scaffold_source_map(workflow_steps: list[dict[str, Any]], raw_source_map: Any) -> list[dict[str, Any]]:
    fallback = _default_scaffold_source_map(workflow_steps)
    source_map = [
        item
        for item in (
            _normalize_source_map_item(item, workflow_steps, index)
            for index, item in enumerate(_source_map_list(raw_source_map), start=1)
        )
        if item is not None
    ]
    result = list(source_map)
    existing_targets = {str(item.get("target") or "") for item in result}
    for item in fallback:
        target = str(item.get("target") or "")
        if target not in existing_targets:
            result.append(item)
            existing_targets.add(target)
    return result or fallback


def _default_scaffold_source_map(workflow_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [
        {
            "target": "runtime_contract.execution_goal",
            "source_file": "SKILL.md",
            "source_summary": "Skill source 定义了现实世界协作执行目标和任务目标。",
        }
    ]
    for step in workflow_steps:
        result.append(
            {
                "target": f"runtime_contract.workflow_steps[{step['id']}]",
                "source_file": step["source_file"],
                "source_summary": step["source_evidence"],
            }
        )
        result.append(
            {
                "target": f"nodes[instruct_{step['id']}],nodes[evaluate_{step['id']}]",
                "source_file": step["source_file"],
                "source_summary": f"由 workflow step「{step['title']}」机械展开为指令节点和证据评估节点。",
            }
        )
    result.append(
        {
            "target": "runtime_contract.safety_constraints",
            "source_file": "SKILL.md",
            "source_summary": "Skill source 定义了安全约束总则或阶段停止条件。",
        }
    )
    return result


def _normalize_source_map_item(
    item: dict[str, Any],
    workflow_steps: list[dict[str, Any]],
    index: int,
) -> dict[str, Any] | None:
    target = str(item.get("target") or item.get("target_path") or "").strip()
    if not target:
        return None
    matched_step = _source_map_step_for_target(workflow_steps, target)
    default_source_file = str(matched_step.get("source_file") or "SKILL.md") if matched_step else "SKILL.md"
    source_file = _non_empty_text(item.get("source_file"), default_source_file)
    normalized = {key: value for key, value in item.items() if value not in (None, "", [], {})}
    normalized["target"] = target
    normalized["source_file"] = source_file
    if not str(normalized.get("source_excerpt") or "").strip() and not str(normalized.get("source_summary") or "").strip():
        summary = (
            item.get("source_evidence")
            or item.get("evidence")
            or item.get("source")
            or item.get("description")
            or item.get("rationale")
        )
        if not isinstance(summary, str) or not summary.strip():
            summary = matched_step.get("source_evidence") if matched_step else f"source_map[{index}] 由 compiler scaffold 规范化补齐来源摘要。"
        normalized["source_summary"] = str(summary).strip()
    return normalized


def _source_map_step_for_target(workflow_steps: list[dict[str, Any]], target: str) -> dict[str, Any] | None:
    for step in workflow_steps:
        step_id = str(step.get("id") or "")
        if step_id and step_id in target:
            return step
    return None


def _source_map_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict):
            result.append(dict(item))
    return result


def _diagnostic_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return default
    result = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
    return result or default


def _non_empty_text(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _safe_step_id(value: str, *, index: int, used_ids: set[str]) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    if not normalized:
        normalized = f"workflow_{index}"
    if normalized in {"start", "input", "llm", "tool", "terminal", "final", "finalize", "finish", "end"}:
        normalized = f"{normalized}_stage"
    candidate = normalized
    suffix = 2
    while candidate in used_ids:
        candidate = f"{normalized}_{suffix}"
        suffix += 1
    return candidate


def _event_kind_for_evidence(kind: str) -> str:
    if kind == "image":
        return "terminal.image.input.v1"
    if kind == "file":
        return "terminal.file.input.v1"
    return "terminal.text.input.v1"


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _normalized_summary(artifact: Any, error_count: int, warning_count: int) -> dict[str, Any]:
    artifact = artifact if isinstance(artifact, dict) else {}
    runtime_contract = artifact.get("runtime_contract") if isinstance(artifact.get("runtime_contract"), dict) else {}
    workflow_steps = runtime_contract.get("workflow_steps") if isinstance(runtime_contract.get("workflow_steps"), list) else []
    nodes = artifact.get("nodes") if isinstance(artifact.get("nodes"), list) else []
    return {
        "status": validator_status_from_counts(error_count),
        "formal_revision": artifact.get("formal_revision") or "",
        "node_count": len(nodes),
        "workflow_step_count": len(workflow_steps),
        "graph_summary": artifact.get("graph_summary") if isinstance(artifact.get("graph_summary"), dict) else {},
        "capability_summary": artifact.get("capability_summary") if isinstance(artifact.get("capability_summary"), dict) else {},
        "error_count": error_count,
        "warning_count": warning_count,
    }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _truncate(value: str, max_chars: int) -> str:
    return value[:max_chars]


def _limit_json_payload(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(content) <= max_chars:
        return payload
    limited = dict(payload)
    limited["truncated"] = True
    limited["runtime_policy_snapshot"] = {}
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
