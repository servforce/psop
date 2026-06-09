from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


EVIDENCE_BACKED_MEMORY_TYPES = {"semantic", "episodic", "procedural", "artifact"}
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


class OutputGuardrail:
    """Validate Agent final output before it is accepted by the harness."""

    def check(self, *, agent_key: str, output_payload: dict[str, Any]) -> OutputGuardrailResult:
        findings: list[GuardrailFinding] = []
        business_wait_state = self._business_wait_state(output_payload)
        candidates = output_payload.get("memory_candidates")
        if isinstance(candidates, list):
            findings.extend(self._memory_candidate_findings(candidates))
        return OutputGuardrailResult(
            passed=not any(item.severity == "error" for item in findings),
            findings=findings,
            business_wait_state=business_wait_state,
        )

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
