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
REQUIREMENT_RESULT_STATUSES = {"accepted", "rejected", "missing", "ambiguous", "not_applicable"}


class RunnerObservationValidationError(ValueError):
    def __init__(self, code: str, message: str, *, correction: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.correction = correction or {}


class RunnerRequirementResult(BaseModel):
    requirement_key: str
    status: Literal["accepted", "rejected", "missing", "ambiguous", "not_applicable"]
    event_refs: list[str] = Field(default_factory=list)
    satisfied_by: str = ""
    reason: str = ""


class RunnerEvidenceAssessment(BaseModel):
    evaluated_event_refs: list[str] = Field(default_factory=list)
    accepted_event_refs: list[str] = Field(default_factory=list)
    rejected_event_refs: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    unsafe_or_ambiguous_facts: list[str] = Field(default_factory=list)
    requirement_results: list[RunnerRequirementResult] = Field(default_factory=list)


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
    if "reference_images" in candidate:
        raise ValueError("Runner observation 不再支持 reference_images。")
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

    _validate_source_refs(observation.get("source_refs") or [], invocation_context)
    evidence_assessment = observation.get("evidence_assessment") or {}
    _validate_evidence_event_refs(_evidence_event_refs(evidence_assessment), invocation_context)
    _validate_latest_evidence_freshness(
        evidence_assessment,
        invocation_input=invocation_input,
        invocation_context=invocation_context,
    )
    _validate_requirement_results(
        evidence_assessment,
        decision=observation["decision"],
        invocation_context=invocation_context,
    )
    observation["evidence_assessment"] = _normalize_evidence_assessment(
        evidence_assessment,
        invocation_context=invocation_context,
    )
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
    results = [item for item in evidence_assessment.get("requirement_results") or [] if isinstance(item, dict)]
    top_level_keys = ("evaluated_event_refs",) if results else (
        "evaluated_event_refs",
        "accepted_event_refs",
        "rejected_event_refs",
    )
    for key in top_level_keys:
        value = evidence_assessment.get(key)
        if isinstance(value, list):
            refs.extend(str(item) for item in value if str(item).strip())
    for item in results:
        event_refs = item.get("event_refs")
        if isinstance(event_refs, list):
            refs.extend(str(ref) for ref in event_refs if str(ref).strip())
    return refs


def _validate_latest_evidence_freshness(
    evidence_assessment: dict[str, Any],
    *,
    invocation_input: dict[str, Any],
    invocation_context: dict[str, Any],
) -> None:
    node = invocation_input.get("node")
    if not isinstance(node, dict) or str(node.get("mode") or "") != "evidence_evaluation":
        return
    latest = invocation_context.get("latest_evidence")
    latest_seq = latest.get("seq_no") if isinstance(latest, dict) else None
    if not isinstance(latest_seq, int) or isinstance(latest_seq, bool):
        return
    evaluated_refs = _string_list(evidence_assessment.get("evaluated_event_refs"))
    if not any(_terminal_ref_seq(ref) == latest_seq for ref in evaluated_refs):
        raise RunnerObservationValidationError(
            "latest_evidence_not_evaluated",
            f"evaluated_event_refs 必须包含最新 evidence terminal_event:{latest_seq} 或其 part。",
            correction={"add_evaluated_event_ref": f"terminal_event:{latest_seq}"},
        )
    result_refs = [
        ref
        for item in evidence_assessment.get("requirement_results") or []
        if isinstance(item, dict)
        for ref in _string_list(item.get("event_refs"))
    ]
    latest_is_requirement_evidence = any(
        _event_ref_matches_option(f"terminal_event:{latest_seq}", option, invocation_context)
        for requirement in _evidence_requirements(invocation_context).values()
        for option in _requirement_evidence_options(requirement)
    )
    if latest_is_requirement_evidence and result_refs and not any(_terminal_ref_seq(ref) == latest_seq for ref in result_refs):
        raise RunnerObservationValidationError(
            "latest_evidence_not_reflected_in_ledger",
            f"requirement_results 仍未引用最新 evidence terminal_event:{latest_seq}；请重新评估受影响 requirement。",
            correction={"reevaluate_event_ref": f"terminal_event:{latest_seq}"},
        )


def _validate_requirement_results(
    evidence_assessment: dict[str, Any],
    *,
    decision: str,
    invocation_context: dict[str, Any],
) -> None:
    results = evidence_assessment.get("requirement_results")
    if not isinstance(results, list) or not results:
        if _evidence_requirements(invocation_context):
            raise RunnerObservationValidationError(
                "missing_requirement_ledger",
                "evidence evaluation 必须提交 requirement_results。",
                correction={"required_field": "evidence_assessment.requirement_results"},
            )
        return
    requirements = _evidence_requirements(invocation_context)
    allowed_keys = set(requirements)
    if not allowed_keys:
        raise ValueError("requirement_results 需要当前上下文提供 evidence_progress.requirements。")
    result_statuses: dict[str, str] = {}
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("requirement_results 每项必须是 JSON object。")
        key = str(item.get("requirement_key") or "").strip()
        if key not in allowed_keys:
            raise ValueError(f"requirement_results 引用了当前 checkpoint 不存在的 requirement_key：{key}")
        status = str(item.get("status") or "").strip().lower()
        if status not in REQUIREMENT_RESULT_STATUSES:
            raise ValueError(f"requirement_results.status 不受支持：{status}")
        if key in result_statuses:
            raise RunnerObservationValidationError(
                "duplicate_requirement_result",
                f"requirement_results 包含重复 requirement_key：{key}",
                correction={"requirement_key": key, "action": "仅保留一个结果"},
            )
        result_statuses[key] = status
        event_refs = _string_list(item.get("event_refs"))
        requirement = requirements[key]
        if status == "accepted" and not event_refs:
            raise RunnerObservationValidationError(
                "accepted_without_event_refs",
                f"accepted requirement `{key}` 必须引用至少一个 terminal event。",
                correction={"requirement_key": key, "required_field": "event_refs"},
            )
        if status == "not_applicable" and bool(requirement.get("required", True)):
            raise RunnerObservationValidationError(
                "required_requirement_not_applicable",
                f"必选 requirement `{key}` 不允许使用 not_applicable。",
                correction={"requirement_key": key, "allowed_statuses": ["accepted", "rejected", "missing", "ambiguous"]},
            )
        if status == "not_applicable" and (event_refs or str(item.get("satisfied_by") or "").strip()):
            raise RunnerObservationValidationError(
                "not_applicable_has_evidence",
                f"not_applicable requirement `{key}` 不应包含 event_refs 或 satisfied_by。",
                correction={"requirement_key": key, "clear_fields": ["event_refs", "satisfied_by"]},
            )
        _validate_satisfied_by(
            requirement=requirement,
            result=item,
            invocation_context=invocation_context,
        )
        _validate_checkpoint_event_refs(event_refs, invocation_context)

    if _is_evidence_contract_v2(invocation_context):
        missing_results = sorted(allowed_keys - set(result_statuses))
        if missing_results:
            raise RunnerObservationValidationError(
                "incomplete_requirement_ledger",
                f"v2 requirement_results 必须覆盖当前步骤全部 requirements，缺少：{missing_results}",
                correction={"add_requirement_results": missing_results},
            )

    if decision == "continue":
        unresolved = []
        for key, requirement in requirements.items():
            if not bool(requirement.get("required", True)):
                continue
            status = result_statuses.get(key, str(requirement.get("status") or "missing").strip().lower())
            if status != "accepted":
                unresolved.append({"requirement_key": key, "status": status})
        if unresolved:
            raise RunnerObservationValidationError(
                "continue_with_unresolved_requirements",
                "decision=continue 时所有必选 requirement 必须为 accepted。",
                correction={"unresolved_requirements": unresolved, "allowed_decision": "need_more_evidence"},
            )


def _validate_satisfied_by(
    *,
    requirement: dict[str, Any],
    result: dict[str, Any],
    invocation_context: dict[str, Any],
) -> None:
    if not _is_evidence_contract_v2(invocation_context):
        return
    key = str(requirement.get("requirement_key") or "")
    status = str(result.get("status") or "").strip().lower()
    satisfied_by = str(result.get("satisfied_by") or "").strip()
    options = {
        str(item.get("option_key") or "").strip(): item
        for item in requirement.get("evidence_options") or []
        if isinstance(item, dict) and str(item.get("option_key") or "").strip()
    }
    if status != "accepted":
        if satisfied_by:
            raise RunnerObservationValidationError(
                "satisfied_by_on_unaccepted_requirement",
                f"未 accepted 的 requirement `{key}` 不应声明 satisfied_by。",
                correction={"requirement_key": key, "clear_field": "satisfied_by"},
            )
        return
    if satisfied_by not in options:
        raise RunnerObservationValidationError(
            "invalid_satisfied_by",
            f"accepted requirement `{key}` 的 satisfied_by 必须引用合法 evidence option。",
            correction={"requirement_key": key, "allowed_option_keys": sorted(options)},
        )
    option = options[satisfied_by]
    event_refs = _string_list(result.get("event_refs"))
    if not any(_event_ref_matches_option(ref, option, invocation_context) for ref in event_refs):
        raise RunnerObservationValidationError(
            "evidence_option_mismatch",
            f"requirement `{key}` 的 event_refs 与 evidence option `{satisfied_by}` 不匹配。",
            correction={
                "requirement_key": key,
                "satisfied_by": satisfied_by,
                "expected_kind": str(option.get("kind") or ""),
                "expected_event_kind": str(option.get("event_kind") or ""),
            },
        )


def _validate_checkpoint_event_refs(event_refs: list[str], invocation_context: dict[str, Any]) -> None:
    allowed = _checkpoint_event_refs(invocation_context)
    if not allowed:
        return
    for ref in event_refs:
        seq_no = _terminal_ref_seq(ref)
        if seq_no is None or not any(_terminal_ref_seq(candidate) == seq_no for candidate in allowed):
            raise RunnerObservationValidationError(
                "evidence_outside_checkpoint",
                f"requirement_results 引用了当前 checkpoint 之外的 evidence：{ref}",
                correction={"remove_event_ref": ref},
            )


def _checkpoint_event_refs(invocation_context: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    checkpoint = invocation_context.get("current_checkpoint")
    evidence = checkpoint.get("evidence") if isinstance(checkpoint, dict) else None
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict):
                refs.extend(_event_refs_for_bundle(item))
    latest = invocation_context.get("latest_evidence")
    if isinstance(latest, dict):
        refs.extend(_event_refs_for_bundle(latest))
    return _unique_strings(refs)


def _event_refs_for_bundle(event: dict[str, Any]) -> list[str]:
    seq_no = event.get("seq_no")
    if not isinstance(seq_no, int) or isinstance(seq_no, bool):
        return []
    refs = [f"terminal_event:{seq_no}"]
    for part in event.get("parts") or []:
        if isinstance(part, dict) and str(part.get("part_id") or "").strip():
            refs.append(f"terminal_event:{seq_no}:{str(part['part_id']).strip()}")
    return refs


def _event_ref_matches_option(ref: str, option: dict[str, Any], invocation_context: dict[str, Any]) -> bool:
    seq_no = _terminal_ref_seq(ref)
    if seq_no is None:
        return False
    expected_kind = str(option.get("kind") or "").strip().lower()
    expected_event_kind = str(option.get("event_kind") or "").strip()
    proof_mode = str(option.get("proof_mode") or "").strip().lower()
    if proof_mode == "visual" and expected_kind not in {"image", "video"}:
        return False
    if proof_mode == "attestation" and expected_kind not in {"text", "audio"}:
        return False
    for event in _visible_terminal_events(invocation_context):
        if event.get("seq_no") != seq_no:
            continue
        actual_event_kind = str(event.get("event_kind") or "")
        logical_multimodal_alias = (
            actual_event_kind == "terminal.multimodal.input.v1"
            and expected_event_kind == f"terminal.{expected_kind}.input.v1"
        )
        if expected_event_kind and actual_event_kind != expected_event_kind and not logical_multimodal_alias:
            continue
        if not expected_kind:
            return True
        part_id = ref.split(":", 2)[2] if ref.count(":") == 2 else ""
        parts = [item for item in event.get("parts") or [] if isinstance(item, dict)]
        if part_id:
            return any(
                str(item.get("part_id") or "") == part_id
                and str(item.get("kind") or "").strip().lower() == expected_kind
                for item in parts
            )
        if any(str(item.get("kind") or "").strip().lower() == expected_kind for item in parts):
            return True
        if expected_kind == "text" and bool(event.get("text") or event.get("payload_inline")):
            return True
    return False


def _requirement_evidence_options(requirement: dict[str, Any]) -> list[dict[str, Any]]:
    options = [item for item in requirement.get("evidence_options") or [] if isinstance(item, dict)]
    if options:
        return options
    if requirement.get("kind") or requirement.get("event_kind"):
        return [
            {
                "kind": str(requirement.get("kind") or ""),
                "event_kind": str(requirement.get("event_kind") or ""),
            }
        ]
    return []


def _visible_terminal_events(invocation_context: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[int] = set()
    candidates: list[Any] = [*_list_value(invocation_context, "terminal_events")]
    latest = invocation_context.get("latest_evidence")
    if isinstance(latest, dict):
        candidates.append(latest)
    checkpoint = invocation_context.get("current_checkpoint")
    if isinstance(checkpoint, dict) and isinstance(checkpoint.get("evidence"), list):
        candidates.extend(checkpoint["evidence"])
    for item in candidates:
        seq_no = item.get("seq_no") if isinstance(item, dict) else None
        if isinstance(seq_no, int) and not isinstance(seq_no, bool) and seq_no not in seen:
            seen.add(seq_no)
            events.append(item)
    return events


def _normalize_evidence_assessment(
    evidence_assessment: dict[str, Any],
    *,
    invocation_context: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(evidence_assessment)
    results = [item for item in evidence_assessment.get("requirement_results") or [] if isinstance(item, dict)]
    if not results:
        return normalized
    requirements = _evidence_requirements(invocation_context)
    normalized["accepted_event_refs"] = _unique_strings(
        [ref for item in results if item.get("status") == "accepted" for ref in _string_list(item.get("event_refs"))]
    )
    normalized["rejected_event_refs"] = _unique_strings(
        [ref for item in results if item.get("status") == "rejected" for ref in _string_list(item.get("event_refs"))]
    )
    normalized["missing_evidence"] = [
        str(requirements.get(str(item.get("requirement_key") or ""), {}).get("description") or item.get("requirement_key") or "")
        for item in results
        if str(item.get("status") or "") in {"missing", "rejected", "ambiguous"}
        and bool(requirements.get(str(item.get("requirement_key") or ""), {}).get("required", True))
    ]
    return normalized


def _evidence_requirements(invocation_context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    progress = _dict_value(invocation_context, "evidence_progress")
    requirements = progress.get("requirements")
    if not isinstance(requirements, list):
        return {}
    return {
        str(item.get("requirement_key") or "").strip(): item
        for item in requirements
        if isinstance(item, dict) and str(item.get("requirement_key") or "").strip()
    }


def _is_evidence_contract_v2(invocation_context: dict[str, Any]) -> bool:
    runtime_contract = _dict_value(invocation_context, "runtime_contract")
    return str(runtime_contract.get("evidence_contract_version") or "") == "psop-evidence/v2"


def _terminal_ref_seq(ref: str) -> int | None:
    match = re.fullmatch(r"terminal_event:(\d+)(?::[^:\s]+)?", str(ref or "").strip())
    return int(match.group(1)) if match else None


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _list_value(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}
