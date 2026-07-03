from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


RUNNER_OBSERVATION_VIRTUAL_PATH = "/mnt/psop/outputs/runner-observation.json"
RUNNER_OBSERVATION_SCHEMA = "psop.runner.observation.v1"
RUNNER_DECISIONS = {"continue", "need_more_evidence", "retry", "abort", "complete"}
SUPPORTED_TERMINAL_INPUT_KINDS = {"text", "image", "audio", "video"}


class RunnerEvidenceAssessment(BaseModel):
    accepted_event_refs: list[str] = Field(default_factory=list)
    rejected_event_refs: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    unsafe_or_ambiguous_facts: list[str] = Field(default_factory=list)


class RunnerReferenceImage(BaseModel):
    reference_image_ref: str
    title: str = ""
    caption: str = ""
    source_ref: str = ""
    display_order: int = 0

    @field_validator("reference_image_ref")
    @classmethod
    def _validate_reference_ref(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("reference_image_ref 必须是非空字符串。")
        return value.strip()


class RunnerSafetyFlag(BaseModel):
    level: str = "warning"
    code: str = ""
    message: str = ""


class RunnerObservation(BaseModel):
    schema: Literal["psop.runner.observation.v1"]
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
    confidence: str = "medium"

    @field_validator("node_id", "decision")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("字段必须是非空字符串。")
        return value.strip()

    @field_validator("terminal_message", "reason", "next_phase", "wait_reason", "final_response", "confidence")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else ""

    @field_validator("expected_inputs")
    @classmethod
    def _validate_expected_inputs(cls, value: list[str]) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("expected_inputs 必须是数组。")
        normalized: list[str] = []
        for item in value:
            item_value = str(item or "").strip().lower()
            if item_value not in SUPPORTED_TERMINAL_INPUT_KINDS:
                raise ValueError(f"expected_inputs 包含不支持的输入类型：{item!r}")
            if item_value not in normalized:
                normalized.append(item_value)
        return normalized

    @field_validator("source_refs")
    @classmethod
    def _validate_source_refs(cls, value: list[str]) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("source_refs 必须是数组。")
        return [str(item).strip() for item in value if str(item or "").strip()]

    @model_validator(mode="after")
    def _validate_final_response_scope(self) -> "RunnerObservation":
        if self.final_response and self.decision not in {"complete", "abort"}:
            raise ValueError("final_response 只允许在 decision=complete 或 abort 时非空。")
        return self


def validate_runner_observation(
    payload: Any,
    *,
    node_id: str,
    output_contract: dict[str, Any] | None = None,
    step_reference_images: list[dict[str, Any]] | None = None,
    terminal_cursor: int = 0,
) -> RunnerObservation:
    try:
        observation = RunnerObservation.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(_validation_error_message(exc)) from exc

    output_contract = output_contract or {}
    if observation.node_id != node_id:
        raise ValueError(f"RunnerObservation.node_id 必须等于当前节点 `{node_id}`。")

    allowed_decisions = {
        str(item)
        for item in output_contract.get("allowed_decisions", RUNNER_DECISIONS)
        if isinstance(item, str) and item
    } or RUNNER_DECISIONS
    if observation.decision not in allowed_decisions:
        raise ValueError(f"decision `{observation.decision}` 不在允许集合中。")

    max_message_chars = int(output_contract.get("max_terminal_message_chars") or 2000)
    if len(observation.terminal_message) > max_message_chars:
        raise ValueError(f"terminal_message 超过 {max_message_chars} 字符。")
    for field_name, value in (("terminal_message", observation.terminal_message), ("final_response", observation.final_response)):
        if _contains_internal_secret(value):
            raise ValueError(f"{field_name} 包含内部地址、对象存储 key 或 credential 风险文本。")

    allowed_phases = _allowed_phases(output_contract)
    if observation.next_phase and allowed_phases and observation.next_phase not in allowed_phases:
        raise ValueError(f"next_phase `{observation.next_phase}` 不在当前 output contract 允许集合中。")

    allowed_refs = {
        str(item.get("reference_image_ref") or "")
        for item in (step_reference_images or [])
        if isinstance(item, dict) and item.get("reference_image_ref")
    }
    if observation.reference_images:
        if not allowed_refs:
            raise ValueError("当前步骤没有允许的 reference_images。")
        for image in observation.reference_images:
            if image.reference_image_ref not in allowed_refs:
                raise ValueError(f"reference_image_ref `{image.reference_image_ref}` 不属于当前步骤。")

    cursor = max(0, int(terminal_cursor or 0))
    for event_ref in _terminal_event_refs(observation):
        if event_ref > cursor:
            raise ValueError(f"source_refs 中 terminal_event:{event_ref} 晚于当前 terminal cursor {cursor}。")

    sorted_images = sorted(observation.reference_images, key=lambda item: (item.display_order, item.reference_image_ref))
    return observation.model_copy(update={"reference_images": sorted_images})


def _allowed_phases(output_contract: dict[str, Any]) -> set[str]:
    allowed = output_contract.get("allowed_next_phases") or output_contract.get("allowed_phases") or []
    if not isinstance(allowed, list):
        return set()
    return {str(item) for item in allowed if isinstance(item, str) and item}


def _terminal_event_refs(observation: RunnerObservation) -> set[int]:
    refs: set[str] = set(observation.source_refs)
    refs.update(observation.evidence_assessment.accepted_event_refs)
    refs.update(observation.evidence_assessment.rejected_event_refs)
    result: set[int] = set()
    for ref in refs:
        match = re.fullmatch(r"terminal_event:(\d+)", str(ref).strip())
        if match:
            result.add(int(match.group(1)))
    return result


def _contains_internal_secret(value: str) -> bool:
    lowered = (value or "").lower()
    forbidden = (
        "object_key",
        "access_key",
        "secret_key",
        "credential",
        "x-amz-",
        "minio",
        "presigned",
    )
    return any(item in lowered for item in forbidden)


def _validation_error_message(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []))
        messages.append(f"{location}: {error.get('msg')}")
    return "RunnerObservation 校验失败：" + "; ".join(messages)
