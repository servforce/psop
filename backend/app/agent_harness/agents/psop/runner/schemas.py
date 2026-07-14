from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


RUNNER_OBSERVATION_SCHEMA = "psop.runner.observation.v1"
RUNNER_OBSERVATION_VIRTUAL_PATH = "/mnt/psop/outputs/runner-observation.json"
RUNNER_OBSERVATION_ARTIFACT_REF = "sandbox://outputs/runner-observation.json"
RUNNER_DECISIONS = {"continue", "need_more_evidence", "retry", "abort", "complete"}
RUNTIME_DECISION_BY_RUNNER_DECISION = {
    "continue": "proceed",
    "need_more_evidence": "need_more_evidence",
    "retry": "retry",
    "abort": "abort",
    "complete": "complete",
}
SUPPORTED_EXPECTED_INPUTS = {"text", "image", "audio", "video"}
REQUIREMENT_RESULT_STATUSES = {"accepted", "rejected", "missing", "ambiguous"}


class RunnerRequirementResult(BaseModel):
    requirement_key: str
    status: Literal["accepted", "rejected", "missing", "ambiguous"]
    event_refs: list[str] = Field(default_factory=list)
    reason: str = ""


class RunnerEvidenceAssessment(BaseModel):
    accepted_event_refs: list[str] = Field(default_factory=list)
    rejected_event_refs: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    unsafe_or_ambiguous_facts: list[str] = Field(default_factory=list)
    requirement_results: list[RunnerRequirementResult] = Field(default_factory=list)


class RunnerReferenceImage(BaseModel):
    reference_image_ref: str
    title: str = ""
    caption: str = ""
    artifact_ref: str = ""
    artifact_object_id: str = ""
    mime_type: str = ""
    workflow_step_id: str = ""
    source_ref: str = ""
    display_order: int = 0


class RunnerSafetyFlag(BaseModel):
    level: str = ""
    code: str = ""
    message: str = ""


class RunnerObservation(BaseModel):
    schema_: Literal["psop.runner.observation.v1"] = Field(alias="schema")
    node_id: str
    decision: Literal["continue", "need_more_evidence", "retry", "abort", "complete"]
    terminal_message: str = ""
    reason: str = ""
    next_phase: str = ""
    wait_reason: str = ""
    expected_inputs: list[str] = Field(default_factory=list)
    evidence_assessment: RunnerEvidenceAssessment = Field(default_factory=RunnerEvidenceAssessment)
    reference_images: list[RunnerReferenceImage] = Field(default_factory=list)
    safety_flags: list[RunnerSafetyFlag] = Field(default_factory=list)
    final_response: str = ""
    source_refs: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"

    @field_validator("expected_inputs")
    @classmethod
    def _validate_expected_inputs(cls, value: list[str]) -> list[str]:
        normalized = [str(item).strip().lower() for item in value if str(item).strip()]
        invalid = sorted(set(normalized) - SUPPORTED_EXPECTED_INPUTS)
        if invalid:
            raise ValueError(f"expected_inputs 包含不支持的类型：{invalid}")
        return normalized


def validate_runner_observation(
    candidate: Any,
    *,
    invocation_input: dict[str, Any] | None = None,
    invocation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("Runner observation 必须是 JSON object。")
    observation = RunnerObservation.model_validate(candidate).model_dump(mode="json", by_alias=True)
    invocation_input = invocation_input or {}
    invocation_context = invocation_context or {}

    node_id = _current_node_id(invocation_input)
    if node_id and observation["node_id"] != node_id:
        raise ValueError(f"node_id 必须等于当前 Runtime 节点 ID：{node_id}")

    allowed_decisions = _allowed_decisions(invocation_input)
    if observation["decision"] not in allowed_decisions:
        raise ValueError(f"decision 不在 output contract 允许集合中：{observation['decision']}")

    max_message_chars = _max_terminal_message_chars(invocation_context)
    if len(observation.get("terminal_message") or "") > max_message_chars:
        raise ValueError(f"terminal_message 超过长度上限：{max_message_chars}")

    if observation["final_response"] and observation["decision"] not in {"complete", "abort"}:
        raise ValueError("final_response 只允许在 decision=complete 或 decision=abort 时非空。")

    _validate_reference_images(observation.get("reference_images") or [], invocation_context)
    _validate_source_refs(observation.get("source_refs") or [], invocation_context)
    evidence_assessment = observation.get("evidence_assessment") or {}
    _validate_evidence_event_refs(_evidence_event_refs(evidence_assessment), invocation_context)
    _validate_requirement_results(evidence_assessment, invocation_context)
    observation["runtime_decision"] = runner_decision_to_runtime_decision(observation["decision"])
    return observation


def runner_decision_to_runtime_decision(decision: str) -> str:
    normalized = decision.strip().lower()
    if normalized not in RUNTIME_DECISION_BY_RUNNER_DECISION:
        raise ValueError(f"不支持的 runner decision：{decision}")
    return RUNTIME_DECISION_BY_RUNNER_DECISION[normalized]


def _current_node_id(invocation_input: dict[str, Any]) -> str:
    node = invocation_input.get("node")
    if isinstance(node, dict):
        return str(node.get("id") or "")
    return ""


def _allowed_decisions(invocation_input: dict[str, Any]) -> set[str]:
    output_contract = invocation_input.get("output_contract")
    if isinstance(output_contract, dict) and isinstance(output_contract.get("allowed_decisions"), list):
        return {str(item).strip().lower() for item in output_contract["allowed_decisions"] if str(item).strip()}
    return set(RUNNER_DECISIONS)


def _max_terminal_message_chars(invocation_context: dict[str, Any]) -> int:
    allowed_runtime = invocation_context.get("allowed_runtime")
    if isinstance(allowed_runtime, dict):
        value = allowed_runtime.get("max_terminal_message_chars")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return 2000


def _validate_reference_images(items: list[dict[str, Any]], invocation_context: dict[str, Any]) -> None:
    allowed_refs = {
        str(item.get("reference_image_ref") or "")
        for item in _list_value(invocation_context, "step_reference_images")
        if isinstance(item, dict)
    }
    if items and not allowed_refs:
        raise ValueError("当前步骤没有允许的 reference_images，Runner 不能提交参考图片引用。")
    for item in items:
        ref = str(item.get("reference_image_ref") or "")
        if not ref:
            raise ValueError("reference_images 每项必须包含 reference_image_ref。")
        if ref not in allowed_refs:
            raise ValueError(f"reference_image_ref 不属于当前步骤允许集合：{ref}")


def _validate_source_refs(source_refs: list[str], invocation_context: dict[str, Any]) -> None:
    for source_ref in source_refs:
        ref = str(source_ref or "").strip()
        if not ref:
            raise ValueError("source_refs 不能包含空引用。")
        if re.fullmatch(r"terminal_event:\d+(?::[^:\s]+)?", ref):
            _validate_terminal_event_ref(ref, invocation_context)
            continue
        if ref.startswith("runtime_contract.workflow_steps."):
            step_id = _segment_after_prefix(ref, "runtime_contract.workflow_steps.")
            if step_id not in _runtime_contract_workflow_step_ids(invocation_context):
                raise ValueError(f"source_refs 引用了不存在的 workflow step：{ref}")
            continue
        if ref.startswith("runtime_contract.expected_evidence."):
            step_id = _segment_after_prefix(ref, "runtime_contract.expected_evidence.")
            if step_id not in _runtime_contract_expected_evidence_keys(invocation_context):
                raise ValueError(f"source_refs 引用了不存在的 expected evidence：{ref}")
            continue
        if ref.startswith("runtime_contract.wait_checkpoints."):
            checkpoint_id = _segment_after_prefix(ref, "runtime_contract.wait_checkpoints.")
            if checkpoint_id not in _runtime_contract_wait_checkpoint_ids(invocation_context):
                raise ValueError(f"source_refs 引用了不存在的 wait checkpoint：{ref}")
            continue
        if ref in {
            "runtime_contract.execution_goal",
            "runtime_contract.applicability",
            "runtime_contract.safety_constraints",
            "runtime_contract.completion_criteria",
        }:
            field_name = ref.removeprefix("runtime_contract.")
            if not _path_exists(_dict_value(invocation_context, "runtime_contract"), field_name):
                raise ValueError(f"source_refs 引用了不存在的 runtime contract 字段：{ref}")
            continue
        if ref.startswith("task_identity."):
            if not _path_exists(_dict_value(invocation_context, "task_identity"), ref.removeprefix("task_identity.")):
                raise ValueError(f"source_refs 引用了不存在的 task identity 路径：{ref}")
            continue
        if ref.startswith("prompt_view."):
            if not _path_exists(_dict_value(invocation_context, "prompt_view"), ref.removeprefix("prompt_view.")):
                raise ValueError(f"source_refs 引用了不存在的 prompt view 路径：{ref}")
            continue
        if ref.startswith("current_checkpoint."):
            if not _path_exists(
                _dict_value(invocation_context, "current_checkpoint"),
                ref.removeprefix("current_checkpoint."),
            ):
                raise ValueError(f"source_refs 引用了不存在的 current checkpoint 路径：{ref}")
            continue
        if ref.startswith("trace_summary:"):
            if not _trace_summary_ref_exists(ref, invocation_context):
                raise ValueError(f"source_refs 引用了不存在的 trace summary：{ref}")
            continue
        raise ValueError(f"source_refs 包含不支持的引用前缀：{ref}")


def _validate_evidence_event_refs(source_refs: list[str], invocation_context: dict[str, Any]) -> None:
    for source_ref in source_refs:
        ref = str(source_ref or "").strip()
        if not ref:
            raise ValueError("evidence event refs 不能包含空引用。")
        if not re.fullmatch(r"terminal_event:\d+(?::[^:\s]+)?", ref):
            raise ValueError(f"evidence event refs 只能引用 terminal_event 或 terminal part：{ref}")
        _validate_terminal_event_ref(ref, invocation_context)


def _validate_terminal_event_ref(ref: str, invocation_context: dict[str, Any]) -> None:
    events = _terminal_event_seq_by_ref(invocation_context)
    seq_no = events.get(ref)
    if seq_no is None:
        raise ValueError(f"source_refs 引用了不存在或不可见的 terminal event：{ref}")
    cursor = _terminal_cursor(invocation_context, events)
    if seq_no > cursor:
        raise ValueError(f"source_refs 引用了晚于 terminal cursor 的 terminal event：{ref}")


def _terminal_event_seq_by_ref(invocation_context: dict[str, Any]) -> dict[str, int]:
    refs: dict[str, int] = {}
    for event in _list_value(invocation_context, "terminal_events"):
        if not isinstance(event, dict):
            continue
        _collect_terminal_event_refs(refs, event)
    latest = invocation_context.get("latest_evidence")
    if isinstance(latest, dict):
        _collect_terminal_event_refs(refs, latest)
    checkpoint = invocation_context.get("current_checkpoint")
    evidence_items = checkpoint.get("evidence") if isinstance(checkpoint, dict) else None
    if isinstance(evidence_items, list):
        for item in evidence_items:
            if isinstance(item, dict):
                _collect_terminal_event_refs(refs, item)
    return refs


def _collect_terminal_event_refs(refs: dict[str, int], event: dict[str, Any]) -> None:
    seq_no = event.get("seq_no")
    if not isinstance(seq_no, int) or isinstance(seq_no, bool):
        return
    refs[f"terminal_event:{seq_no}"] = seq_no
    for part in _list_value(event, "parts"):
        if not isinstance(part, dict):
            continue
        part_id = str(part.get("part_id") or "").strip()
        if part_id:
            refs[f"terminal_event:{seq_no}:{part_id}"] = seq_no


def _terminal_cursor(invocation_context: dict[str, Any], events: dict[str, int]) -> int:
    value = invocation_context.get("terminal_cursor")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return max(events.values(), default=0)


def _runtime_contract_workflow_step_ids(invocation_context: dict[str, Any]) -> set[str]:
    runtime_contract = _dict_value(invocation_context, "runtime_contract")
    return {
        str(item.get("id") or "")
        for item in _list_value(runtime_contract, "workflow_steps")
        if isinstance(item, dict) and str(item.get("id") or "")
    }


def _runtime_contract_expected_evidence_keys(invocation_context: dict[str, Any]) -> set[str]:
    runtime_contract = _dict_value(invocation_context, "runtime_contract")
    expected_evidence = runtime_contract.get("expected_evidence")
    return set(str(key) for key in expected_evidence.keys()) if isinstance(expected_evidence, dict) else set()


def _runtime_contract_wait_checkpoint_ids(invocation_context: dict[str, Any]) -> set[str]:
    runtime_contract = _dict_value(invocation_context, "runtime_contract")
    return {
        str(item.get("checkpoint_id") or "")
        for item in _list_value(runtime_contract, "wait_checkpoints")
        if isinstance(item, dict) and str(item.get("checkpoint_id") or "")
    }


def _segment_after_prefix(value: str, prefix: str) -> str:
    tail = value.removeprefix(prefix)
    return tail.split(".", 1)[0].strip()


def _path_exists(payload: Any, path: str) -> bool:
    if not isinstance(path, str) or not path.strip():
        return False
    current = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
            continue
        return False
    return current not in (None, "", [], {})


def _trace_summary_ref_exists(ref: str, invocation_context: dict[str, Any]) -> bool:
    trace_summary = _list_value(invocation_context, "trace_summary")
    key = ref.removeprefix("trace_summary:").strip()
    if not key:
        return False
    if key.isdigit() and int(key) < len(trace_summary):
        return True
    for item in trace_summary:
        if isinstance(item, dict) and str(item.get("seq_no") or "") == key:
            return True
    return False


def _evidence_event_refs(evidence_assessment: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("accepted_event_refs", "rejected_event_refs"):
        value = evidence_assessment.get(key)
        if isinstance(value, list):
            refs.extend(str(item) for item in value if str(item).strip())
    for item in evidence_assessment.get("requirement_results") or []:
        if not isinstance(item, dict):
            continue
        event_refs = item.get("event_refs")
        if isinstance(event_refs, list):
            refs.extend(str(ref) for ref in event_refs if str(ref).strip())
    return refs


def _validate_requirement_results(evidence_assessment: dict[str, Any], invocation_context: dict[str, Any]) -> None:
    results = evidence_assessment.get("requirement_results")
    if not isinstance(results, list) or not results:
        return
    allowed_keys = _evidence_requirement_keys(invocation_context)
    if not allowed_keys:
        raise ValueError("requirement_results 需要当前上下文提供 evidence_progress.requirements。")
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("requirement_results 每项必须是 JSON object。")
        key = str(item.get("requirement_key") or "").strip()
        if key not in allowed_keys:
            raise ValueError(f"requirement_results 引用了当前 checkpoint 不存在的 requirement_key：{key}")
        status = str(item.get("status") or "").strip().lower()
        if status not in REQUIREMENT_RESULT_STATUSES:
            raise ValueError(f"requirement_results.status 不受支持：{status}")


def _evidence_requirement_keys(invocation_context: dict[str, Any]) -> set[str]:
    progress = _dict_value(invocation_context, "evidence_progress")
    requirements = progress.get("requirements")
    if not isinstance(requirements, list):
        return set()
    return {
        str(item.get("requirement_key") or "").strip()
        for item in requirements
        if isinstance(item, dict) and str(item.get("requirement_key") or "").strip()
    }


def _list_value(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}
