from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


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
EVIDENCE_SOURCE_TYPE_ALIASES = {
    "material": "material_analysis",
    "raw_material": "material_analysis",
    "material_result": "material_analysis",
    "material_analysis_result": "material_analysis",
    "keyframe": "reference_asset",
    "key_frame": "reference_asset",
    "reference": "reference_asset",
    "asset": "reference_asset",
    "standard": "industry_standard",
    "industry": "industry_standard",
    "user": "user_description",
}
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:[#:/].+)?$",
    re.IGNORECASE,
)
PLACEHOLDER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"^\s*(?:[-*+]\s*)?(?:\[[ xX]\]\s*)?TODO(?:\s*[:：]|\s*$)",
        r"^\s*(?:[-*+]\s*)?待补充(?:\s*[:：]|\s*$)",
        r"示例路径",
        r"references/\.\.\.",
    )
]


class BuilderCandidateValidationError(ValueError):
    def __init__(self, message: str, diagnostics: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or []


EvidenceSourceType = Literal[
    "user_description",
    "current_source",
    "material_analysis",
    "reference_asset",
    "industry_standard",
    "builder_inference",
    "human_confirmation_required",
]
EvidenceSupportLevel = Literal[
    "observed_fact",
    "standard_reference",
    "current_source_fact",
    "builder_inference",
    "human_confirmation_required",
    "confirmed_instruction",
]


class EvidenceSourceRef(BaseModel):
    model_config = {"extra": "forbid"}

    source_type: EvidenceSourceType = Field(description="证据来源类别。")
    ref: str = Field(default="", description="用户指令、当前源码或推断的可追溯说明。")
    material_id: str = Field(default="", description="素材分析来源的 material_id。")
    asset_id: str = Field(default="", description="参考资产来源的 asset_id。")
    standard_ref: str = Field(default="", description="行业标准编号。")
    clause_ref: str = Field(default="", description="行业标准条款编号。")

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_source_ref(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = _legacy_source_ref_to_object(value)
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        source_type = str(normalized.get("source_type") or normalized.get("type") or "").strip()
        if source_type.lower().startswith("current_source/"):
            normalized["source_type"] = "current_source"
            normalized["ref"] = normalized.get("ref") or _normalize_current_source_ref(source_type.removeprefix("current_source/"))
        elif source_type:
            normalized["source_type"] = _normalize_source_type(source_type)
        normalized.pop("type", None)
        return normalized

    @model_validator(mode="after")
    def _require_type_specific_reference(self) -> "EvidenceSourceRef":
        if self.source_type == "material_analysis" and not (self.material_id or self.ref):
            raise ValueError("material_analysis 必须包含 material_id 或 ref。")
        if self.source_type == "reference_asset" and not (self.asset_id or self.ref):
            raise ValueError("reference_asset 必须包含 asset_id 或 ref。")
        if self.source_type == "industry_standard" and not (self.standard_ref or self.ref):
            raise ValueError("industry_standard 必须包含 standard_ref 或 ref。")
        if self.source_type in {"user_description", "current_source", "builder_inference", "human_confirmation_required"} and not self.ref:
            raise ValueError(f"{self.source_type} 必须包含 ref。")
        if self.source_type == "current_source" and not re.fullmatch(r"(?:README|SKILL)\.md(?:#.+)?", self.ref):
            raise ValueError("current_source.ref 只能引用 README.md 或 SKILL.md（可带 #锚点）。")
        return self


class EvidenceMapItem(BaseModel):
    model_config = {
        "extra": "allow",
        "json_schema_extra": {
            "examples": [{
                "claim": "素材显示机箱侧板已打开。",
                "support_level": "observed_fact",
                "source_refs": [{"source_type": "material_analysis", "material_id": "material-1"}],
                "used_in": ["阶段 1"],
            }]
        },
    }

    claim: str = Field(description="要追溯的结论。")
    support_level: EvidenceSupportLevel = Field(description="结论的证据等级。")
    source_refs: list[EvidenceSourceRef] = Field(description="支撑结论的结构化来源。")
    used_in: list[str] = Field(description="使用该结论的阶段、约束、完成标准或 review_notes。")

    @field_validator("claim")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("字段必须是非空字符串。")
        return value.strip()

    @field_validator("source_refs", "used_in")
    @classmethod
    def _non_empty_list(cls, value: list[Any]) -> list[Any]:
        if not value:
            raise ValueError("字段必须是非空数组。")
        return value


class MaterialUsageItem(BaseModel):
    model_config = {"extra": "forbid", "json_schema_extra": {"examples": [{"material_id": "material-1", "usage": "支撑阶段 1 的可观察动作。"}]}}

    material_id: str = Field(min_length=1, description="已读取素材的 material_id。")
    usage: str = Field(min_length=1, description="该素材如何支撑 candidate。")


class IndustryStandardUsageItem(BaseModel):
    model_config = {"extra": "allow"}

    standard_ref: str = Field(default="", description="标准编号；reference_only 可为空。")
    clause_ref: str = Field(default="", description="条款编号；reference_only 可为空。")
    usage: str = Field(min_length=1, description="使用方式，例如 reference_only 或 mandatory。")
    used_in: list[str] = Field(default_factory=list, description="使用该标准的阶段或约束。")


class SelectedReferenceAsset(BaseModel):
    model_config = {"extra": "allow"}

    asset_id: str = Field(min_length=1, description="从 candidate_reference_assets 选择的 asset_id。")
    material_id: str = Field(default="", description="资产所属素材 ID。")
    reference_path: str = Field(default="", description="references/ 下的相对路径。")
    reason: str = Field(default="", description="选择该资产的理由。")


class MissingQuestionItem(BaseModel):
    model_config = {"extra": "forbid", "json_schema_extra": {"examples": [{"question": "接口型号是否已确认？", "reason": "素材未显示接口标签。", "blocking_level": "non_blocking"}]}}

    question: str = Field(min_length=1, description="需要人工确认的问题。")
    reason: str = Field(min_length=1, description="当前证据不能确认该问题的原因。")
    blocking_level: Literal["blocking", "non_blocking"] = Field(description="是否阻塞生成后的审阅或执行。")


class SafetyConstraintItem(BaseModel):
    model_config = {"extra": "allow"}

    constraint: str = Field(min_length=1, description="安全约束。")
    applies_to: str = Field(min_length=1, description="约束适用的 workflow 阶段或全程。")
    risk_type: str = Field(min_length=1, description="风险类别。")
    required_action: str = Field(min_length=1, description="违反约束时的必需动作。")


class WorkflowStepCandidate(BaseModel):
    model_config = {"extra": "allow"}

    step_id: str = Field(default="", description="SKILL.md 中的阶段编号。")
    stage_id: str = Field(default="", description="step_id 的兼容字段。")
    title: str = Field(default="", description="SKILL.md 中的阶段标题。")
    stage_title: str = Field(default="", description="title 的兼容字段。")

    @model_validator(mode="after")
    def _require_stage_reference(self) -> "WorkflowStepCandidate":
        if not any((self.step_id, self.stage_id, self.title, self.stage_title)):
            raise ValueError("每项必须包含 step_id、stage_id、title 或 stage_title。")
        return self


class ExpectedEvidenceRequirement(BaseModel):
    model_config = {"extra": "allow"}

    stage_id: str = Field(default="", description="关联的 workflow 阶段。")
    step_id: str = Field(default="", description="stage_id 的兼容字段。")
    stage_title: str = Field(default="", description="阶段标题兼容字段。")
    evidence_type: str = Field(min_length=1, description="所需证据类型。")
    completion_criteria: str = Field(min_length=1, description="完成判定条件。")

    @model_validator(mode="after")
    def _require_stage_reference(self) -> "ExpectedEvidenceRequirement":
        if not any((self.stage_id, self.step_id, self.stage_title)):
            raise ValueError("每项必须包含 stage_id、step_id 或 stage_title。")
        return self


class BuilderCandidate(BaseModel):
    model_config = {"extra": "forbid"}

    directory_tree: str
    files: dict[str, str]
    generation_reason: str
    review_notes: list[str] = Field(default_factory=list)
    material_usage: list[MaterialUsageItem] = Field(description="每个使用素材的 ID 与用途。")
    industry_standard_usage: list[IndustryStandardUsageItem] = Field(default_factory=list, description="可追溯标准的使用方式。")
    selected_reference_assets: list[SelectedReferenceAsset] = Field(description="选用的运行时参考资产。")
    evidence_map: list[EvidenceMapItem]
    missing_questions: list[MissingQuestionItem] = Field(default_factory=list, description="必须人工确认的缺口。")
    safety_constraints: list[SafetyConstraintItem] = Field(description="可执行的安全约束。")
    workflow_step_candidates: list[WorkflowStepCandidate] = Field(description="SKILL.md 中的工作流阶段。")
    expected_evidence_requirements: list[ExpectedEvidenceRequirement] = Field(description="阶段完成所需的证据。")

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
    standard_search_status: str | None = None,
) -> BuilderCandidate:
    try:
        candidate = BuilderCandidate.model_validate(
            {
                key: value
                for key, value in payload.items()
                if key not in {"materialized_reference_image_count", "materialized_reference_images", "linked_reference_images"}
            }
        )
    except ValidationError as exc:
        raise BuilderCandidateValidationError(
            _validation_error_message(exc),
            [_pydantic_diagnostic(error) for error in exc.errors()],
        ) from exc
    diagnostics: list[dict[str, Any]] = []
    checks = (
        lambda: _validate_material_usage(candidate.material_usage),
        lambda: _validate_selected_reference_assets(candidate.selected_reference_assets, candidate_reference_assets or []),
        lambda: _validate_industry_standard_usage(candidate.industry_standard_usage, standard_search_results or [], standard_search_status),
        lambda: _validate_standard_search_degradation(candidate, standard_search_status),
        lambda: _validate_evidence_map(candidate.evidence_map),
        lambda: _validate_missing_questions(candidate.missing_questions, candidate.review_notes),
        lambda: _validate_safety_constraints(candidate.safety_constraints),
        lambda: _validate_workflow(candidate.workflow_step_candidates, candidate.expected_evidence_requirements, candidate.files["SKILL.md"]),
        lambda: _validate_evidence_coverage(candidate),
        lambda: _validate_reference_paths_used(candidate.selected_reference_assets, candidate.files),
    )
    for check in checks:
        try:
            check()
        except BuilderCandidateValidationError as exc:
            diagnostics.extend(exc.diagnostics)
    if diagnostics:
        raise BuilderCandidateValidationError(
            "builder candidate 校验失败：" + "; ".join(str(item.get("message") or "") for item in diagnostics),
            diagnostics,
        )
    return candidate


def builder_candidate_input_schema() -> dict[str, Any]:
    """Return the submit_candidate JSON schema from the validation contract."""
    schema = BuilderCandidate.model_json_schema()
    schema["description"] = (
        "完整 PSOP Skill candidate。evidence_map.source_refs 必须使用结构化来源对象；"
        "不得把工具名、超时或任意路径伪装成证据。"
    )
    return schema


def _validate_material_usage(items: list[MaterialUsageItem]) -> None:
    if not items:
        _raise_validation("material_usage", "required", "material_usage 必须非空。", example={"material_id": "material-1", "usage": "支撑阶段 1。"})


def _validate_selected_reference_assets(items: list[SelectedReferenceAsset], candidates: list[dict[str, Any]]) -> None:
    if not items:
        _raise_validation("selected_reference_assets", "required", "selected_reference_assets 必须至少选择一项。")
    if len(items) > MAX_BUILDER_REFERENCE_ASSETS:
        _raise_validation("selected_reference_assets", "max_items", f"selected_reference_assets 数量不能超过 {MAX_BUILDER_REFERENCE_ASSETS}。")
    candidate_ids = {str(item.get("asset_id") or item.get("id") or "").strip() for item in candidates}
    candidate_paths = {str(item.get("reference_path") or "").strip() for item in candidates}
    for index, item in enumerate(items):
        asset_id = item.asset_id.strip()
        reference_path = item.reference_path.strip()
        if candidate_ids and asset_id not in candidate_ids:
            _raise_validation(f"selected_reference_assets.{index}.asset_id", "unknown_reference_asset", f"selected_reference_assets 包含非候选资产：{asset_id}")
        if reference_path and candidate_paths and reference_path not in candidate_paths:
            _raise_validation(f"selected_reference_assets.{index}.reference_path", "unknown_reference_path", f"selected_reference_assets 包含非候选 reference_path：{reference_path}")


def _validate_industry_standard_usage(
    items: list[IndustryStandardUsageItem],
    search_results: list[dict[str, Any]],
    search_status: str | None,
) -> None:
    known_pairs = {
        (str(item.get("standard_ref") or "").strip(), str(item.get("clause_ref") or "").strip())
        for item in search_results
        if str(item.get("citation_status") or "complete") != "incomplete"
    }
    for index, item in enumerate(items):
        usage = item.usage.strip()
        if usage == "reference_only" and search_status in {None, "success"}:
            continue
        required = ["standard_ref", "clause_ref", "usage", "used_in"]
        values = {"standard_ref": item.standard_ref, "clause_ref": item.clause_ref, "usage": item.usage, "used_in": item.used_in}
        missing = [key for key in required if not values[key]]
        if missing:
            _raise_validation(f"industry_standard_usage.{index}", "missing_fields", f"industry_standard_usage 缺少字段：{missing}")
        pair = (item.standard_ref.strip(), item.clause_ref.strip())
        if known_pairs and pair not in known_pairs:
            _raise_validation(f"industry_standard_usage.{index}", "unknown_standard_reference", f"industry_standard_usage 引用了未由 psop.standard.search 返回的标准条款：{pair}")


def _validate_evidence_map(items: list[EvidenceMapItem]) -> None:
    if not items:
        _raise_validation("evidence_map", "required", "evidence_map 必须非空。")


def _validate_standard_search_degradation(candidate: BuilderCandidate, search_status: str | None) -> None:
    if search_status not in {"timeout", "service_unavailable", "internal_error"}:
        return
    diagnostics: list[dict[str, Any]] = []
    if candidate.industry_standard_usage:
        diagnostics.append(
            {
                "path": "industry_standard_usage",
                "code": "standard_search_unavailable",
                "message": "标准检索不可用时不得引用 industry_standard。",
                "allowed_values": [],
                "example": [],
            }
        )
    if not any("标准检索不可用，未引用行业标准" in note for note in candidate.review_notes):
        diagnostics.append(
            {
                "path": "review_notes",
                "code": "missing_standard_unavailable_note",
                "message": "标准检索不可用时 review_notes 必须包含固定说明。",
                "allowed_values": [],
                "example": "标准检索不可用，未引用行业标准。",
            }
        )
    if diagnostics:
        raise BuilderCandidateValidationError("标准检索降级说明不完整。", diagnostics)


def _validate_evidence_coverage(candidate: BuilderCandidate) -> None:
    supported_targets = {
        _normalize_match_text(target)
        for item in candidate.evidence_map
        for target in item.used_in
        if _evidence_item_has_supported_source(item)
    }
    for index, item in enumerate(candidate.workflow_step_candidates):
        target = next((value.strip() for value in (item.step_id, item.stage_id, item.title, item.stage_title) if value.strip()), "")
        if target and _normalize_match_text(target) not in supported_targets:
            _raise_validation(
                f"workflow_step_candidates.{index}",
                "missing_evidence_coverage",
                f"工作流阶段缺少可验证 evidence_map.used_in 覆盖：{target}",
                example={"used_in": [target], "source_refs": [{"source_type": "material_analysis", "material_id": "..."}]},
            )
    for index, item in enumerate(candidate.safety_constraints):
        target = item.applies_to.strip()
        if target and target not in {"全程", "all", "all_stages"} and _normalize_match_text(target) not in supported_targets:
            _raise_validation(
                f"safety_constraints.{index}",
                "missing_evidence_coverage",
                f"安全约束缺少可验证 evidence_map.used_in 覆盖：{target}",
            )
    for index, item in enumerate(candidate.expected_evidence_requirements):
        target = next((value.strip() for value in (item.stage_id, item.step_id, item.stage_title) if value.strip()), "")
        if target and _normalize_match_text(target) not in supported_targets:
            _raise_validation(
                f"expected_evidence_requirements.{index}",
                "missing_evidence_coverage",
                f"预期证据要求缺少可验证 evidence_map.used_in 覆盖：{target}",
            )


def _evidence_item_has_supported_source(item: EvidenceMapItem) -> bool:
    return any(
        source.source_type in {"user_description", "material_analysis", "reference_asset", "industry_standard"}
        for source in item.source_refs
    )


def _validate_missing_questions(items: list[MissingQuestionItem], review_notes: list[str]) -> None:
    if any(item.blocking_level == "blocking" for item in items) and not review_notes:
        _raise_validation("review_notes", "missing_blocking_review_note", "存在 blocking missing_questions 时 review_notes 必须说明审阅风险。")


def _validate_safety_constraints(items: list[SafetyConstraintItem]) -> None:
    if not items:
        _raise_validation("safety_constraints", "required", "safety_constraints 必须非空。")


def _validate_workflow(
    workflow_steps: list[WorkflowStepCandidate],
    evidence_requirements: list[ExpectedEvidenceRequirement],
    skill_md: str,
) -> None:
    diagnostics: list[dict[str, Any]] = []
    if not workflow_steps:
        diagnostics.append({"path": "workflow_step_candidates", "code": "required", "message": "workflow_step_candidates 必须非空。", "allowed_values": [], "example": None})
    if not evidence_requirements:
        diagnostics.append({"path": "expected_evidence_requirements", "code": "required", "message": "expected_evidence_requirements 必须非空。", "allowed_values": [], "example": None})
    if diagnostics:
        raise BuilderCandidateValidationError("工作流元数据不完整。", diagnostics)
    skill_text = skill_md.lower()
    normalized_skill_text = _normalize_match_text(skill_md)
    for index, item in enumerate(workflow_steps):
        step_ref = next((value.strip() for value in (item.step_id, item.stage_id, item.title, item.stage_title) if value.strip()), "")
        if step_ref.lower() not in skill_text and _normalize_match_text(step_ref) not in normalized_skill_text:
            _raise_validation(f"workflow_step_candidates.{index}", "workflow_not_in_skill", f"workflow_step_candidates 阶段未能在 SKILL.md 中找到对应内容：{step_ref}")


def _normalize_match_text(value: str) -> str:
    return re.sub(r"[\s\u3000:：\-—_/、，,。.\(\)（）]+", "", value.lower())


def _validate_reference_paths_used(items: list[SelectedReferenceAsset], files: dict[str, str]) -> None:
    skill_md = files.get("SKILL.md", "")
    docs = "\n".join(files.get(path, "") for path in ("SKILL.md", "references/README.md"))
    for index, item in enumerate(items):
        reference_path = item.reference_path.strip()
        if reference_path and reference_path not in skill_md:
            _raise_validation(f"selected_reference_assets.{index}.reference_path", "reference_not_in_skill", f"选中的 reference_path 未被 SKILL.md 流程内容引用：{reference_path}")
        if reference_path and reference_path not in docs:
            _raise_validation(f"selected_reference_assets.{index}.reference_path", "reference_not_in_docs", f"选中的 reference_path 未被候选文档引用：{reference_path}")


def _reject_placeholder_text(path: str, content: str) -> None:
    for pattern in PLACEHOLDER_PATTERNS:
        if pattern.search(content):
            raise ValueError(f"{path} 包含占位内容：{pattern.pattern}")


def _legacy_source_ref_to_object(value: str) -> dict[str, str]:
    raw = str(value).strip()
    if raw.lower().startswith("current_source/"):
        return {"source_type": "current_source", "ref": _normalize_current_source_ref(raw.removeprefix("current_source/"))}
    if raw.startswith("references/") or "#keyframe" in raw.lower():
        return {"source_type": "reference_asset", "ref": raw}
    if UUID_PATTERN.match(raw):
        return {"source_type": "material_analysis", "ref": raw}
    source_type, separator, reference = raw.partition(":")
    return {"source_type": _normalize_source_type(source_type), "ref": reference if separator else ""}


def _normalize_source_type(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    return EVIDENCE_SOURCE_TYPE_ALIASES.get(normalized, normalized)


def _normalize_current_source_ref(value: str) -> str:
    path = value.strip()
    filename, separator, anchor = path.partition("#")
    normalized_filename = {"readme.md": "README.md", "skill.md": "SKILL.md"}.get(filename.lower(), filename)
    return normalized_filename + (f"#{anchor}" if separator else "")


def _validation_error_message(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []))
        messages.append(f"{location}: {error.get('msg')}")
    return "builder candidate 校验失败：" + "; ".join(messages)


def validation_diagnostics(exc: Exception) -> list[dict[str, Any]]:
    if isinstance(exc, BuilderCandidateValidationError):
        return exc.diagnostics
    if isinstance(exc, ValidationError):
        return [_pydantic_diagnostic(error) for error in exc.errors()]
    return [{"path": "candidate", "code": "invalid_candidate", "message": str(exc), "allowed_values": [], "example": None}]


def _raise_validation(path: str, code: str, message: str, *, example: Any = None) -> None:
    raise BuilderCandidateValidationError(
        message,
        [{"path": path, "code": code, "message": message, "allowed_values": [], "example": example}],
    )


def _pydantic_diagnostic(error: dict[str, Any]) -> dict[str, Any]:
    path = ".".join(str(part) for part in error.get("loc", [])) or "candidate"
    message = str(error.get("msg") or "候选产物不符合 schema。")
    if path.endswith("source_type"):
        allowed_values = sorted(ALLOWED_EVIDENCE_SOURCE_TYPES)
        example: Any = {"source_type": "current_source", "ref": "SKILL.md"}
    elif path.endswith("support_level"):
        allowed_values = ["observed_fact", "standard_reference", "current_source_fact", "builder_inference", "human_confirmation_required", "confirmed_instruction"]
        example = "observed_fact"
    elif ".material_usage." in f".{path}." or path.startswith("material_usage"):
        allowed_values = []
        example = {"material_id": "material-1", "usage": "支撑阶段 1。"}
    elif ".missing_questions." in f".{path}." or path.startswith("missing_questions"):
        allowed_values = []
        example = {"question": "接口型号是否已确认？", "reason": "素材未显示标签。", "blocking_level": "non_blocking"}
    else:
        allowed_values = []
        example = None
    return {"path": path, "code": str(error.get("type") or "schema_validation"), "message": message, "allowed_values": allowed_values, "example": example}
