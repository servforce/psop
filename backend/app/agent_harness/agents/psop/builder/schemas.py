from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


REQUIRED_BUILDER_FILES = [
    "README.md",
    "SKILL.md",
    "prompts/system.md",
    "references/README.md",
    "examples/input.md",
    "examples/expected-output.md",
    "tests/checklist.md",
]

FORBIDDEN_BUILDER_FILES = {"skill.yaml"}
MAX_BUILDER_REFERENCE_ASSETS = 12
ALLOWED_EVIDENCE_SOURCE_TYPES = {
    "user_description",
    "current_source",
    "material_analysis",
    "reference_asset",
    "industry_standard",
    "builder_inference",
    "human_confirmation_required",
}
PLACEHOLDER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bTODO\b",
        r"待补充",
        r"示例路径",
        r"references/\.\.\.",
    )
]


class BuilderCandidate(BaseModel):
    directory_tree: str
    files: dict[str, str]
    generation_reason: str
    review_notes: list[str] = Field(default_factory=list)
    material_usage: list[dict[str, Any]]
    industry_standard_usage: list[dict[str, Any]] = Field(default_factory=list)
    selected_reference_assets: list[dict[str, Any]]
    evidence_map: list[dict[str, Any]]
    missing_questions: list[dict[str, Any]] = Field(default_factory=list)
    safety_constraints: list[dict[str, Any]]
    workflow_step_candidates: list[dict[str, Any]]
    expected_evidence_requirements: list[dict[str, Any]]

    @field_validator("directory_tree", "generation_reason")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("字段必须是非空字符串。")
        return value.strip()

    @field_validator("files")
    @classmethod
    def _validate_files(cls, value: dict[str, str]) -> dict[str, str]:
        if not isinstance(value, dict) or not value:
            raise ValueError("files 必须是非空对象。")
        normalized: dict[str, str] = {}
        for path, content in value.items():
            normalized_path = normalize_builder_path(str(path))
            if normalized_path in FORBIDDEN_BUILDER_FILES:
                raise ValueError("builder candidate 不得包含 skill.yaml。")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"{normalized_path} 内容不能为空。")
            _reject_placeholder_text(normalized_path, content)
            normalized[normalized_path] = content
        missing = [path for path in REQUIRED_BUILDER_FILES if not normalized.get(path)]
        if missing:
            raise ValueError(f"builder candidate 缺少必需文件：{missing}")
        return normalized


def normalize_builder_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/").lstrip("/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError(f"生成文件路径非法：{value}")
    return "/".join(parts)


def validate_builder_candidate(
    payload: dict[str, Any],
    *,
    candidate_reference_assets: list[dict[str, Any]] | None = None,
    standard_search_results: list[dict[str, Any]] | None = None,
) -> BuilderCandidate:
    try:
        candidate = BuilderCandidate.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(_validation_error_message(exc)) from exc
    _validate_material_usage(candidate.material_usage)
    _validate_selected_reference_assets(candidate.selected_reference_assets, candidate_reference_assets or [])
    _validate_industry_standard_usage(candidate.industry_standard_usage, standard_search_results or [])
    _validate_evidence_map(candidate.evidence_map)
    _validate_missing_questions(candidate.missing_questions, candidate.review_notes)
    _validate_safety_constraints(candidate.safety_constraints)
    _validate_workflow(candidate.workflow_step_candidates, candidate.expected_evidence_requirements, candidate.files["SKILL.md"])
    _validate_reference_paths_used(candidate.selected_reference_assets, candidate.files)
    return candidate


def _validate_material_usage(items: list[dict[str, Any]]) -> None:
    if not items:
        raise ValueError("material_usage 必须非空。")
    for item in items:
        if not str(item.get("material_id") or "").strip() or not str(item.get("usage") or "").strip():
            raise ValueError("material_usage 每项必须包含 material_id 和 usage。")


def _validate_selected_reference_assets(items: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> None:
    if not items:
        raise ValueError("selected_reference_assets 必须至少选择一项。")
    if len(items) > MAX_BUILDER_REFERENCE_ASSETS:
        raise ValueError(f"selected_reference_assets 数量不能超过 {MAX_BUILDER_REFERENCE_ASSETS}。")
    candidate_ids = {str(item.get("asset_id") or item.get("id") or "").strip() for item in candidates}
    candidate_paths = {str(item.get("reference_path") or "").strip() for item in candidates}
    for item in items:
        asset_id = str(item.get("asset_id") or "").strip()
        reference_path = str(item.get("reference_path") or "").strip()
        if not asset_id:
            raise ValueError("selected_reference_assets 每项必须包含 asset_id。")
        if candidate_ids and asset_id not in candidate_ids:
            raise ValueError(f"selected_reference_assets 包含非候选资产：{asset_id}")
        if reference_path and candidate_paths and reference_path not in candidate_paths:
            raise ValueError(f"selected_reference_assets 包含非候选 reference_path：{reference_path}")


def _validate_industry_standard_usage(items: list[dict[str, Any]], search_results: list[dict[str, Any]]) -> None:
    known_pairs = {
        (str(item.get("standard_ref") or "").strip(), str(item.get("clause_ref") or "").strip())
        for item in search_results
        if str(item.get("citation_status") or "complete") != "incomplete"
    }
    for item in items:
        usage = str(item.get("usage") or "").strip()
        if usage == "reference_only":
            continue
        required = ["standard_ref", "clause_ref", "usage", "used_in"]
        missing = [key for key in required if not item.get(key)]
        if missing:
            raise ValueError(f"industry_standard_usage 缺少字段：{missing}")
        pair = (str(item.get("standard_ref") or "").strip(), str(item.get("clause_ref") or "").strip())
        if known_pairs and pair not in known_pairs:
            raise ValueError(f"industry_standard_usage 引用了未由 psop.standard.search 返回的标准条款：{pair}")


def _validate_evidence_map(items: list[dict[str, Any]]) -> None:
    if not items:
        raise ValueError("evidence_map 必须非空。")
    for item in items:
        for key in ("claim", "support_level", "source_refs", "used_in"):
            if not item.get(key):
                raise ValueError(f"evidence_map 每项必须包含 {key}。")
        source_refs = item.get("source_refs")
        if not isinstance(source_refs, list) or not source_refs:
            raise ValueError("evidence_map.source_refs 必须是非空数组。")
        for source_ref in source_refs:
            source_type = _source_type(source_ref)
            if source_type not in ALLOWED_EVIDENCE_SOURCE_TYPES:
                raise ValueError(f"非法 evidence source_type：{source_type}")


def _validate_missing_questions(items: list[dict[str, Any]], review_notes: list[str]) -> None:
    for item in items:
        for key in ("question", "reason", "blocking_level"):
            if not item.get(key):
                raise ValueError(f"missing_questions 每项必须包含 {key}。")
    if any(item.get("blocking_level") == "blocking" for item in items) and not review_notes:
        raise ValueError("存在 blocking missing_questions 时 review_notes 必须说明审阅风险。")


def _validate_safety_constraints(items: list[dict[str, Any]]) -> None:
    if not items:
        raise ValueError("safety_constraints 必须非空。")
    for item in items:
        for key in ("constraint", "applies_to", "risk_type", "required_action"):
            if not item.get(key):
                raise ValueError(f"safety_constraints 每项必须包含 {key}。")


def _validate_workflow(workflow_steps: list[dict[str, Any]], evidence_requirements: list[dict[str, Any]], skill_md: str) -> None:
    if not workflow_steps:
        raise ValueError("workflow_step_candidates 必须非空。")
    if not evidence_requirements:
        raise ValueError("expected_evidence_requirements 必须非空。")
    skill_text = skill_md.lower()
    for item in workflow_steps:
        step_ref = str(item.get("step_id") or item.get("stage_id") or item.get("title") or item.get("stage_title") or "").strip()
        if not step_ref:
            raise ValueError("workflow_step_candidates 每项必须包含阶段编号或标题。")
        if step_ref.lower() not in skill_text:
            raise ValueError(f"workflow_step_candidates 阶段未能在 SKILL.md 中找到对应内容：{step_ref}")
    for item in evidence_requirements:
        if not item.get("evidence_type") or not item.get("completion_criteria"):
            raise ValueError("expected_evidence_requirements 每项必须包含 evidence_type 和 completion_criteria。")


def _validate_reference_paths_used(items: list[dict[str, Any]], files: dict[str, str]) -> None:
    docs = "\n".join(files.get(path, "") for path in ("SKILL.md", "references/README.md"))
    for item in items:
        reference_path = str(item.get("reference_path") or "").strip()
        if reference_path and reference_path not in docs:
            raise ValueError(f"选中的 reference_path 未被 SKILL.md 或 references/README.md 引用：{reference_path}")


def _reject_placeholder_text(path: str, content: str) -> None:
    for pattern in PLACEHOLDER_PATTERNS:
        if pattern.search(content):
            raise ValueError(f"{path} 包含占位内容：{pattern.pattern}")


def _source_type(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("source_type") or value.get("type") or "").strip()
    return str(value).split(":", 1)[0].strip()


def _validation_error_message(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []))
        messages.append(f"{location}: {error.get('msg')}")
    return "builder candidate 校验失败：" + "; ".join(messages)
