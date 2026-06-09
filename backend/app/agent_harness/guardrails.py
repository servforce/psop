from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.memory.policy import formal_source_replacement_flags


EVIDENCE_BACKED_MEMORY_TYPES = {"semantic", "episodic", "procedural", "artifact"}
REPLAYABLE_EVIDENCE_REF_KINDS = {
    "agent_event",
    "agent_model_call",
    "agent_tool_authorization",
    "agent_tool_call",
    "run_event",
    "run_replay",
    "run_trace",
    "session_token_snapshot",
}
BUSINESS_WAIT_STATE_KEYS = (
    "clarifying_questions",
    "need_more_evidence",
    "proposal_review_required",
    "require_human_review",
)
PROMPT_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous instructions",
    "bypass tool authorization",
    "skip tool authorization",
    "disable guardrails",
    "override system prompt",
)
RUNTIME_STATE_MUTATION_MARKERS = (
    "session_token_snapshot",
    "session token snapshot",
    "run_event",
    "run event",
    "run_trace",
    "run trace",
    "runtime kernel",
    "token_payload",
)
RUNTIME_STATE_MUTATION_VERBS = (
    "write",
    "update",
    "mutate",
    "modify",
    "insert",
    "delete",
    "patch",
    "写",
    "更新",
    "修改",
    "插入",
    "删除",
)
RUNTIME_STATE_MUTATION_NEGATIONS = (
    "cannot",
    "do not",
    "must not",
    "not directly",
    "without mutating",
    "不直接",
    "不能",
    "不得",
    "禁止",
    "不修改",
)


@dataclass(frozen=True, slots=True)
class GuardrailFinding:
    code: str
    message: str
    path: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class OutputGuardrailResult:
    passed: bool
    findings: list[GuardrailFinding] = field(default_factory=list)
    business_wait_state: str = ""

    def as_event_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [item.as_dict() for item in self.findings],
            "business_wait_state": self.business_wait_state,
            "non_hitl_business_state": bool(self.business_wait_state),
        }


@dataclass(frozen=True, slots=True)
class InputGuardrailResult:
    passed: bool
    findings: list[GuardrailFinding] = field(default_factory=list)

    def as_event_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [item.as_dict() for item in self.findings],
            "warning_count": sum(1 for item in self.findings if item.severity == "warning"),
            "error_count": sum(1 for item in self.findings if item.severity == "error"),
        }


@dataclass(frozen=True, slots=True)
class ToolGuardrailResult:
    passed: bool
    findings: list[GuardrailFinding] = field(default_factory=list)

    def as_event_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [item.as_dict() for item in self.findings],
            "warning_count": sum(1 for item in self.findings if item.severity == "warning"),
            "error_count": sum(1 for item in self.findings if item.severity == "error"),
        }


class InputGuardrail:
    """Inspect Agent input for prompt-injection and state-sovereignty risk signals."""

    def check(self, *, agent_key: str, input_payload: dict[str, Any]) -> InputGuardrailResult:
        findings: list[GuardrailFinding] = []
        for path, text in self._iter_strings(input_payload):
            normalized = text.lower()
            if any(marker in normalized for marker in PROMPT_INJECTION_MARKERS):
                findings.append(
                    GuardrailFinding(
                        code="input_prompt_injection_signal",
                        message="Input contains prompt-injection style instructions; keep system and tool policies authoritative.",
                        path=path,
                        severity="warning",
                    )
                )
            if agent_key == "pskill.runner" and any(marker in normalized for marker in RUNTIME_STATE_MUTATION_MARKERS):
                findings.append(
                    GuardrailFinding(
                        code="input_runtime_state_sovereignty_signal",
                        message="Runner input references Runtime state mutation concepts; Runtime Kernel remains the only formal state writer.",
                        path=path,
                        severity="warning",
                    )
                )
        return InputGuardrailResult(
            passed=not any(item.severity == "error" for item in findings),
            findings=findings,
        )

    def _iter_strings(self, value: Any, *, path: str = "input_payload") -> list[tuple[str, str]]:
        if isinstance(value, str):
            return [(path, value)]
        if isinstance(value, dict):
            results: list[tuple[str, str]] = []
            for key, item in value.items():
                results.extend(self._iter_strings(item, path=f"{path}.{key}"))
            return results
        if isinstance(value, list):
            results = []
            for index, item in enumerate(value):
                results.extend(self._iter_strings(item, path=f"{path}[{index}]"))
            return results
        return []


class ToolGuardrail:
    """Validate tool-call decisions before ToolPolicy and authorization checks."""

    def check(
        self,
        *,
        agent_key: str,
        tool_name: str,
        arguments_summary: dict[str, Any],
        expected_effect_summary: str = "",
    ) -> ToolGuardrailResult:
        findings: list[GuardrailFinding] = []
        if agent_key == "pskill.runner" and self._mentions_runtime_state_mutation(
            {
                "tool_name": tool_name,
                "arguments_summary": arguments_summary,
                "expected_effect_summary": expected_effect_summary,
            }
        ):
            findings.append(
                GuardrailFinding(
                    code="tool_runtime_state_sovereignty_violation",
                    message=(
                        "pskill.runner cannot request tools that mutate Runtime formal state; "
                        "RuntimeService remains the only writer."
                    ),
                    path="agent_decision.tool_call",
                )
            )
        return ToolGuardrailResult(
            passed=not any(item.severity == "error" for item in findings),
            findings=findings,
        )

    def _mentions_runtime_state_mutation(self, value: Any) -> bool:
        texts = [text.lower() for _path, text in InputGuardrail()._iter_strings(value, path="tool_call")]
        mentions_state = any(marker in text for text in texts for marker in RUNTIME_STATE_MUTATION_MARKERS)
        mentions_mutation = any(verb in text for text in texts for verb in RUNTIME_STATE_MUTATION_VERBS)
        return mentions_state and mentions_mutation


class OutputGuardrail:
    """Validate Agent final output before it is accepted by the harness."""

    def check(
        self,
        *,
        agent_key: str,
        output_payload: dict[str, Any],
        spec: dict[str, Any] | None = None,
    ) -> OutputGuardrailResult:
        findings: list[GuardrailFinding] = []
        business_wait_state = self._business_wait_state(output_payload)
        findings.extend(self._output_schema_findings(output_payload=output_payload, spec=spec))
        findings.extend(self._guardrail_policy_findings(output_payload=output_payload, spec=spec))
        candidates = output_payload.get("memory_candidates")
        if isinstance(candidates, list):
            findings.extend(self._memory_candidate_findings(candidates))
        return OutputGuardrailResult(
            passed=not any(item.severity == "error" for item in findings),
            findings=findings,
            business_wait_state=business_wait_state,
        )

    def _output_schema_findings(
        self,
        *,
        output_payload: dict[str, Any],
        spec: dict[str, Any] | None,
    ) -> list[GuardrailFinding]:
        output_schema = spec.get("output_schema") if isinstance(spec, dict) else None
        if not isinstance(output_schema, dict):
            return []
        required = output_schema.get("required")
        if not isinstance(required, list):
            return []
        missing_fields = [
            field
            for field in (str(item).strip() for item in required)
            if field and field not in output_payload
        ]
        return [
            GuardrailFinding(
                code="output_schema_required_missing",
                message=f"output payload is missing required field: {field}.",
                path=f"output_payload.{field}",
            )
            for field in missing_fields
        ]

    def _guardrail_policy_findings(
        self,
        *,
        output_payload: dict[str, Any],
        spec: dict[str, Any] | None,
    ) -> list[GuardrailFinding]:
        guardrail_policy = spec.get("guardrail_policy") if isinstance(spec, dict) else None
        if not isinstance(guardrail_policy, dict):
            return []
        findings: list[GuardrailFinding] = []
        if guardrail_policy.get("require_evidence_refs") is True and not self._has_valid_source_ref(
            output_payload.get("evidence_refs")
        ):
            findings.append(
                GuardrailFinding(
                    code="output_evidence_refs_required",
                    message="output payload requires non-empty replayable evidence_refs.",
                    path="output_payload.evidence_refs",
                )
            )
        if guardrail_policy.get("require_replayable_evidence_refs") is True and not self._has_replayable_evidence_ref(
            output_payload.get("evidence_refs")
        ):
            findings.append(
                GuardrailFinding(
                    code="output_replayable_evidence_refs_required",
                    message="output payload requires non-empty replayable evidence_refs.",
                    path="output_payload.evidence_refs",
                )
            )
        if guardrail_policy.get("require_replay_evidence") is True and not self._has_nested_replayable_evidence_ref(
            output_payload
        ):
            findings.append(
                GuardrailFinding(
                    code="output_replay_evidence_required",
                    message="output payload requires replay evidence in evidence_refs or nested diagnostics.",
                    path="output_payload",
                )
            )
        if guardrail_policy.get("deny_runtime_state_mutation") is True:
            findings.extend(self._runtime_state_mutation_findings(output_payload))
        if guardrail_policy.get("deny_direct_publish") is True and self._declares_direct_publish(output_payload):
            findings.append(
                GuardrailFinding(
                    code="output_direct_publish_denied",
                    message="builder output must not directly publish or activate a PSkill version.",
                    path="output_payload",
                )
            )
        if (
            guardrail_policy.get("require_reviewable_patch") is True
            and output_payload.get("ready_for_human_review") is True
            and not self._has_reviewable_patch(output_payload)
        ):
            findings.append(
                GuardrailFinding(
                    code="output_reviewable_patch_required",
                    message="builder output marked ready_for_human_review requires a reviewable patch.",
                    path="output_payload",
                )
            )
        if guardrail_policy.get("require_rollback_plan") is True and not self._has_rollback_plan(
            output_payload.get("activation_plan")
        ):
            findings.append(
                GuardrailFinding(
                    code="output_rollback_plan_required",
                    message="governance output requires an activation_plan with rollback steps or conditions.",
                    path="output_payload.activation_plan",
                )
            )
        if guardrail_policy.get("deny_direct_activation_without_authorization") is True and self._declares_direct_activation(
            output_payload
        ):
            findings.append(
                GuardrailFinding(
                    code="output_direct_activation_denied",
                    message="governance output must not allow or perform direct activation without tool authorization.",
                    path="output_payload",
                )
            )
        if guardrail_policy.get("deny_tool_permission_expansion") is True and self._declares_tool_permission_expansion(
            output_payload
        ):
            findings.append(
                GuardrailFinding(
                    code="output_tool_permission_expansion_denied",
                    message="governance output must not directly expand tool permissions.",
                    path="output_payload",
                )
            )
        if guardrail_policy.get("require_reviewable_patch_and_tests") is True:
            if not self._has_non_empty_list(output_payload.get("proposed_changes")):
                findings.append(
                    GuardrailFinding(
                        code="output_reviewable_changes_required",
                        message="governance output requires reviewable proposed_changes.",
                        path="output_payload.proposed_changes",
                    )
                )
            if not self._has_non_empty_list(output_payload.get("required_tests")):
                findings.append(
                    GuardrailFinding(
                        code="output_required_tests_required",
                        message="governance output requires required_tests before review or activation.",
                        path="output_payload.required_tests",
                    )
                )
        return findings

    def _runtime_state_mutation_findings(self, output_payload: dict[str, Any]) -> list[GuardrailFinding]:
        findings: list[GuardrailFinding] = []
        for path, text in InputGuardrail()._iter_strings(output_payload, path="output_payload"):
            normalized = text.lower()
            if self._is_negated_runtime_boundary_statement(normalized):
                continue
            mentions_state = any(marker in normalized for marker in RUNTIME_STATE_MUTATION_MARKERS)
            mentions_mutation = any(verb in normalized for verb in RUNTIME_STATE_MUTATION_VERBS)
            if mentions_state and mentions_mutation:
                findings.append(
                    GuardrailFinding(
                        code="output_runtime_state_sovereignty_violation",
                        message="output payload must not declare direct mutation of Runtime formal state.",
                        path=path,
                    )
                )
        return findings

    def _memory_candidate_findings(self, candidates: list[Any]) -> list[GuardrailFinding]:
        findings: list[GuardrailFinding] = []
        for index, item in enumerate(candidates):
            if not isinstance(item, dict):
                findings.append(
                    GuardrailFinding(
                        code="memory_candidate_invalid",
                        message="memory candidate must be an object.",
                        path=f"memory_candidates[{index}]",
                    )
                )
                continue
            memory_type = str(item.get("memory_type") or "").strip()
            if memory_type not in EVIDENCE_BACKED_MEMORY_TYPES:
                continue
            source_refs = item.get("source_refs")
            if not self._has_valid_source_ref(source_refs):
                findings.append(
                    GuardrailFinding(
                        code="memory_candidate_missing_source_refs",
                        message=f"{memory_type} memory candidate requires source_refs.",
                        path=f"memory_candidates[{index}].source_refs",
                    )
                )
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                replacement_flags = formal_source_replacement_flags(metadata)
                if replacement_flags:
                    findings.append(
                        GuardrailFinding(
                            code="memory_candidate_replaces_formal_source",
                            message="memory candidate must not replace Runtime, Git, or EG formal sources.",
                            path=f"memory_candidates[{index}].metadata",
                        )
                    )
        return findings

    @staticmethod
    def _has_valid_source_ref(source_refs: Any) -> bool:
        if not isinstance(source_refs, list) or not source_refs:
            return False
        for item in source_refs:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            has_locator = any(str(item.get(key) or "").strip() for key in ("id", "seq_no", "path", "object_key"))
            if kind and has_locator:
                return True
        return False

    @classmethod
    def _has_replayable_evidence_ref(cls, source_refs: Any) -> bool:
        if not isinstance(source_refs, list) or not source_refs:
            return False
        for item in source_refs:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            if kind not in REPLAYABLE_EVIDENCE_REF_KINDS:
                continue
            if cls._has_valid_source_ref([item]) or str(item.get("run_id") or "").strip():
                return True
        return False

    @classmethod
    def _has_nested_replayable_evidence_ref(cls, value: Any) -> bool:
        if isinstance(value, dict):
            if cls._looks_like_replayable_ref(value):
                return True
            for key in ("evidence_refs", "source_refs"):
                if cls._has_replayable_evidence_ref(value.get(key)):
                    return True
            return any(cls._has_nested_replayable_evidence_ref(item) for item in value.values())
        if isinstance(value, list):
            return any(cls._has_nested_replayable_evidence_ref(item) for item in value)
        return False

    @classmethod
    def _looks_like_replayable_ref(cls, value: dict[str, Any]) -> bool:
        kind = str(value.get("kind") or "").strip()
        if kind not in REPLAYABLE_EVIDENCE_REF_KINDS:
            return False
        return cls._has_valid_source_ref([value]) or bool(str(value.get("run_id") or "").strip())

    @staticmethod
    def _is_negated_runtime_boundary_statement(normalized_text: str) -> bool:
        return any(marker in normalized_text for marker in RUNTIME_STATE_MUTATION_NEGATIONS)

    @classmethod
    def _has_rollback_plan(cls, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        if cls._text_mentions_rollback(value.get("strategy")):
            return True
        if cls._text_mentions_rollback(value.get("rollback_conditions")):
            return True
        if isinstance(value.get("rollback"), dict) or isinstance(value.get("rollback"), list):
            return True
        steps = value.get("steps")
        if isinstance(steps, list) and any(cls._text_mentions_rollback(item) for item in steps):
            return True
        return False

    @staticmethod
    def _text_mentions_rollback(value: Any) -> bool:
        if isinstance(value, str):
            return "rollback" in value.lower() or "回滚" in value
        if isinstance(value, list):
            return any(OutputGuardrail._text_mentions_rollback(item) for item in value)
        if isinstance(value, dict):
            return any(OutputGuardrail._text_mentions_rollback(item) for item in value.values())
        return False

    @classmethod
    def _declares_direct_activation(cls, value: Any) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = str(key).strip()
                if normalized_key in {"direct_activation_allowed", "direct_activation_performed"} and item is True:
                    return True
                if cls._declares_direct_activation(item):
                    return True
        if isinstance(value, list):
            return any(cls._declares_direct_activation(item) for item in value)
        return False

    @classmethod
    def _declares_direct_publish(cls, value: Any) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = str(key).strip()
                if normalized_key in {
                    "direct_publish_allowed",
                    "direct_publish_performed",
                    "publish_directly",
                    "published",
                    "publish_performed",
                    "version_activated",
                } and item is True:
                    return True
                if cls._declares_direct_publish(item):
                    return True
        if isinstance(value, list):
            return any(cls._declares_direct_publish(item) for item in value)
        return False

    @classmethod
    def _declares_tool_permission_expansion(cls, value: Any) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = str(key).strip()
                if normalized_key in {
                    "allow_all_tools",
                    "allowed_tools_expanded",
                    "bypass_tool_policy",
                    "direct_tool_permission_expansion",
                    "permission_expansion_performed",
                    "tool_permission_expansion_performed",
                    "tool_policy_relaxed",
                } and item is True:
                    return True
                if cls._declares_tool_permission_expansion(item):
                    return True
        if isinstance(value, list):
            return any(cls._declares_tool_permission_expansion(item) for item in value)
        return False

    @staticmethod
    def _has_non_empty_list(value: Any) -> bool:
        return isinstance(value, list) and bool(value)

    @classmethod
    def _has_reviewable_patch(cls, output_payload: dict[str, Any]) -> bool:
        files = output_payload.get("files")
        if isinstance(files, list) and bool(files):
            return True
        manifest_patch = output_payload.get("manifest_patch")
        if isinstance(manifest_patch, dict) and bool(manifest_patch):
            return True
        for key in ("draft_patch", "manifest_diff", "patch", "source_patch"):
            patch = output_payload.get(key)
            if isinstance(patch, str) and patch.strip():
                return True
            if isinstance(patch, dict) and (
                str(patch.get("diff") or "").strip()
                or cls._has_non_empty_list(patch.get("file_changes"))
                or cls._has_non_empty_list(patch.get("files"))
            ):
                return True
        return False

    @staticmethod
    def _business_wait_state(output_payload: dict[str, Any]) -> str:
        for key in BUSINESS_WAIT_STATE_KEYS:
            value = output_payload.get(key)
            if isinstance(value, list) and value:
                return key
            if isinstance(value, dict) and value:
                return key
            if isinstance(value, str) and value.strip():
                return key
            if value is True:
                return key
        return ""
