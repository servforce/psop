from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.domain.compiler.formal_v5 import FORMAL_REVISION


COMPILER_RESULT_VIRTUAL_PATH = "/mnt/psop/outputs/compiler-result.json"
COMPILER_EG_ARTIFACT_VIRTUAL_PATH = "/mnt/psop/outputs/eg.compile.artifact.json"
REQUIRED_COMPILER_CANDIDATE_FIELDS = [
    "artifact",
    "compile_reason",
    "source_map",
    "diagnostics",
    "repair_history",
    "validator_summary",
]
REQUIRED_ARTIFACT_FIELDS = [
    "formal_revision",
    "schema",
    "nodes",
    "init",
    "halt",
    "policies",
    "dependency_graph_for_view",
    "runtime_contract",
]


class CompilerCandidate(BaseModel):
    artifact: dict[str, Any]
    compile_reason: str
    source_map: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    repair_history: list[dict[str, Any]] = Field(default_factory=list)
    validator_summary: dict[str, Any]

    @field_validator("compile_reason")
    @classmethod
    def _validate_compile_reason(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("compile_reason 必须是非空字符串。")
        return value.strip()

    @field_validator("artifact")
    @classmethod
    def _validate_artifact_shape(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict) or not value:
            raise ValueError("artifact 必须是非空对象。")
        missing = [field for field in REQUIRED_ARTIFACT_FIELDS if field not in value]
        if missing:
            raise ValueError(f"artifact 缺少必需字段：{missing}")
        if value.get("formal_revision") != FORMAL_REVISION:
            raise ValueError(f"artifact.formal_revision 必须是 `{FORMAL_REVISION}`。")
        runtime_contract = value.get("runtime_contract")
        if not isinstance(runtime_contract, dict):
            raise ValueError("artifact.runtime_contract 必须是对象。")
        workflow_steps = runtime_contract.get("workflow_steps")
        if not isinstance(workflow_steps, list) or not workflow_steps:
            raise ValueError("artifact.runtime_contract.workflow_steps 必须是非空数组。")
        return value

    @field_validator("source_map")
    @classmethod
    def _validate_source_map(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise ValueError("source_map 必须是非空数组。")
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValueError(f"source_map[{index}] 必须是对象。")
            if not str(item.get("target") or "").strip():
                raise ValueError(f"source_map[{index}] 必须包含 target。")
            if not str(item.get("source_file") or "").strip():
                raise ValueError(f"source_map[{index}] 必须包含 source_file。")
            has_source_text = str(item.get("source_excerpt") or "").strip() or str(item.get("source_summary") or "").strip()
            if not has_source_text:
                raise ValueError(f"source_map[{index}] 必须包含 source_excerpt 或 source_summary。")
        return value

    @field_validator("diagnostics", "repair_history")
    @classmethod
    def _validate_list(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ValueError("字段必须是数组。")
        return value

    @field_validator("validator_summary")
    @classmethod
    def _validate_validator_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("validator_summary 必须是对象。")
        status = str(value.get("status") or "").strip()
        if status not in {"passed", "failed", "not_run"}:
            raise ValueError("validator_summary.status 必须是 passed、failed 或 not_run。")
        for field_name in ("error_count", "warning_count"):
            if not isinstance(value.get(field_name), int) or value[field_name] < 0:
                raise ValueError(f"validator_summary.{field_name} 必须是非负整数。")
        return value

    @model_validator(mode="after")
    def _validate_validator_consistency(self) -> "CompilerCandidate":
        status = str(self.validator_summary.get("status") or "")
        error_count = int(self.validator_summary.get("error_count") or 0)
        if status == "passed" and error_count > 0:
            raise ValueError("validator_summary.status=passed 时 error_count 必须为 0。")
        return self


def validate_compiler_candidate(payload: Any) -> CompilerCandidate:
    try:
        return CompilerCandidate.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(_validation_error_message(exc)) from exc


def validator_status_from_counts(error_count: int) -> Literal["passed", "failed"]:
    return "failed" if error_count else "passed"


def _validation_error_message(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []))
        messages.append(f"{location}: {error.get('msg')}")
    return "compiler candidate 校验失败：" + "; ".join(messages)
