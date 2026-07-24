from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


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
BUILDER_CANDIDATE_SCHEMA_VERSION = "2.0"
PLATFORM_BUILDER_FIELDS = {
    "materialized_reference_image_count",
    "materialized_reference_images",
    "linked_reference_images",
    "revision_provenance",
}
BUILDER_ID_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"
ALLOWED_EVIDENCE_SOURCE_TYPES = {
    "user_description",
    "current_source",
    "material_analysis",
    "reference_asset",
    "industry_standard",
    "builder_inference",
    "human_confirmation_required",
}
SUPPORTED_REQUIRED_EVIDENCE_SOURCE_TYPES = {
    "user_description",
    "material_analysis",
    "reference_asset",
    "industry_standard",
}
ALLOWED_EVIDENCE_TARGET_TYPES = {
    "workflow_stage",
    "safety_constraint",
    "expected_evidence",
    "review_notes",
}
PLACEHOLDER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"^\s*(?:[-*+]\s*)?(?:\[[ xX]\]\s*)?TODO(?:\s*[:：]|\s*$)",
        r"^\s*(?:[-*+]\s*)?待补充(?:\s*[:：]|\s*$)",
        r"示例路径",
        r"references/\.\.\.",
    )
]
WORKFLOW_HEADING_PATTERN = re.compile(
    rf"^\s*#{{1,6}}\s+\[(?P<stage_id>{BUILDER_ID_PATTERN[1:-1]})\]\s+(?P<title>.+?)\s*$",
    re.MULTILINE,
)


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
EvidenceTargetType = Literal[
    "workflow_stage",
    "safety_constraint",
    "expected_evidence",
    "review_notes",
]
BuilderStableId = Annotated[str, Field(pattern=BUILDER_ID_PATTERN)]


class EvidenceSourceRef(BaseModel):
    model_config = {"extra": "forbid"}

    source_type: EvidenceSourceType = Field(description="证据来源类别。")
    ref: str = Field(default="", description="用户指令、当前源码或推断的可追溯说明。")
    material_id: str = Field(default="", description="素材分析来源的 material_id。")
    asset_id: str = Field(default="", description="参考资产来源的 asset_id。")
    standard_ref: str = Field(default="", description="行业标准编号。")
    clause_ref: str = Field(default="", description="行业标准条款编号。")

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


class EvidenceUsageTarget(BaseModel):
    model_config = {"extra": "forbid"}

    target_type: EvidenceTargetType = Field(description="证据使用目标类型。")
    target_id: BuilderStableId = Field(description="目标的稳定 ID；review_notes 固定使用 review_notes。")


class EvidenceMapItem(BaseModel):
    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [{
                "claim": "素材显示零件已完成清点。",
                "support_level": "observed_fact",
                "source_refs": [{"source_type": "material_analysis", "material_id": "material-1"}],
                "used_in": [{"target_type": "workflow_stage", "target_id": "stage_01_inventory"}],
            }]
        },
    }

    claim: str = Field(min_length=1, description="要追溯的结论。")
    support_level: EvidenceSupportLevel = Field(description="结论的证据等级。")
    source_refs: list[EvidenceSourceRef] = Field(min_length=1, description="支撑结论的结构化来源。")
    used_in: list[EvidenceUsageTarget] = Field(min_length=1, description="使用该结论的结构化目标引用。")


class MaterialUsageItem(BaseModel):
    model_config = {
        "extra": "forbid",
        "json_schema_extra": {"examples": [{"material_id": "material-1", "usage": "支撑阶段 1 的可观察动作。"}]},
    }

    material_id: str = Field(min_length=1, description="已读取素材的 material_id。")
    usage: str = Field(min_length=1, description="该素材如何支撑 candidate。")


class IndustryStandardUsageItem(BaseModel):
    model_config = {"extra": "forbid"}

    standard_ref: str = Field(default="", description="标准编号；reference_only 可为空。")
    clause_ref: str = Field(default="", description="条款编号；reference_only 可为空。")
    usage: str = Field(min_length=1, description="使用方式，例如 reference_only 或 mandatory。")
    used_in: list[EvidenceUsageTarget] = Field(min_length=1, description="使用该标准的结构化目标引用。")


class SelectedReferenceAsset(BaseModel):
    model_config = {"extra": "forbid"}

    asset_id: str = Field(min_length=1, description="从 candidate_reference_assets 选择的 asset_id。")
    material_id: str = Field(min_length=1, description="资产所属素材 ID。")
    reference_path: str = Field(min_length=1, description="references/ 下的相对路径。")
    reason: str = Field(min_length=1, description="选择该资产的理由。")
    stage_ids: list[BuilderStableId] = Field(min_length=1, description="使用该参考资产的 workflow stage_id。")


class MissingQuestionItem(BaseModel):
    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [{"question": "接口型号是否已确认？", "reason": "素材未显示接口标签。", "blocking_level": "non_blocking"}]
        },
    }

    question: str = Field(min_length=1, description="需要人工确认的问题。")
    reason: str = Field(min_length=1, description="当前证据不能确认该问题的原因。")
    blocking_level: Literal["blocking", "non_blocking"] = Field(description="是否阻塞生成后的审阅或执行。")


class SafetyConstraintItem(BaseModel):
    model_config = {"extra": "forbid"}

    constraint_id: BuilderStableId = Field(description="安全约束的稳定 ID。")
    scope: Literal["all_stages", "selected_stages"] = Field(description="安全约束适用范围。")
    stage_ids: list[BuilderStableId] = Field(description="selected_stages 对应的 workflow stage_id；all_stages 时必须为空。")
    constraint: str = Field(min_length=1, description="安全约束。")
    risk_type: str = Field(min_length=1, description="风险类别。")
    required_action: str = Field(min_length=1, description="违反约束时的必需动作。")


class WorkflowStepCandidate(BaseModel):
    model_config = {"extra": "forbid"}

    stage_id: BuilderStableId = Field(description="SKILL.md workflow 阶段的稳定 ID。")
    title: str = Field(min_length=1, description="SKILL.md workflow 阶段标题。")


class ExpectedEvidenceRequirement(BaseModel):
    model_config = {"extra": "forbid"}

    requirement_id: BuilderStableId = Field(description="预期证据要求的稳定 ID。")
    stage_id: BuilderStableId = Field(description="关联的 workflow stage_id。")
    evidence_type: str = Field(min_length=1, description="所需证据类型。")
    completion_criteria: str = Field(min_length=1, description="完成判定条件。")


class BuilderCandidate(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: Literal["2.0"]
    directory_tree: str = Field(min_length=1)
    files: dict[str, str]
    generation_reason: str = Field(min_length=1)
    review_notes: list[str]
    material_usage: list[MaterialUsageItem]
    industry_standard_usage: list[IndustryStandardUsageItem]
    selected_reference_assets: list[SelectedReferenceAsset]
    evidence_map: list[EvidenceMapItem]
    missing_questions: list[MissingQuestionItem]
    safety_constraints: list[SafetyConstraintItem]
    workflow_step_candidates: list[WorkflowStepCandidate]
    expected_evidence_requirements: list[ExpectedEvidenceRequirement]


def parse_builder_candidate(payload: dict[str, Any]) -> BuilderCandidate:
    return BuilderCandidate.model_validate({key: value for key, value in payload.items() if key not in PLATFORM_BUILDER_FIELDS})


def reconcile_builder_candidate(
    payload: dict[str, Any],
    *,
    baseline_payload: dict[str, Any],
    baseline_generation_id: str,
    baseline_commit_sha: str,
    baseline_candidate_hash: str,
) -> tuple[dict[str, Any], dict[str, Any], set[tuple[str, str, str, str, str]]]:
    """Mechanically inherit provenance for targets unchanged from an exact-commit baseline."""
    try:
        candidate = parse_builder_candidate(payload)
        baseline = parse_builder_candidate(baseline_payload)
    except ValidationError:
        return payload, {}, set()

    candidate_stage_bodies = _workflow_stage_bodies(candidate.files.get("SKILL.md", ""))
    baseline_stage_bodies = _workflow_stage_bodies(baseline.files.get("SKILL.md", ""))
    target_specs = (
        (
            "workflow_stage",
            candidate.workflow_step_candidates,
            baseline.workflow_step_candidates,
            "stage_id",
            lambda item, bodies: {"title": item.title, "body": bodies.get(item.stage_id, "")},
        ),
        (
            "safety_constraint",
            candidate.safety_constraints,
            baseline.safety_constraints,
            "constraint_id",
            lambda item, _bodies: item.model_dump(mode="json", exclude={"constraint_id"}),
        ),
        (
            "expected_evidence",
            candidate.expected_evidence_requirements,
            baseline.expected_evidence_requirements,
            "requirement_id",
            lambda item, _bodies: item.model_dump(mode="json", exclude={"requirement_id"}),
        ),
    )
    inherited: dict[str, list[str]] = {item[0]: [] for item in target_specs}
    changed: dict[str, list[str]] = {item[0]: [] for item in target_specs}
    added: dict[str, list[str]] = {item[0]: [] for item in target_specs}
    removed: dict[str, list[str]] = {item[0]: [] for item in target_specs}
    rename_diagnostics: list[dict[str, Any]] = []

    for target_type, current_items, baseline_items, id_field, business_value in target_specs:
        current_by_id = {getattr(item, id_field): item for item in current_items}
        baseline_by_id = {getattr(item, id_field): item for item in baseline_items}
        current_bodies = candidate_stage_bodies if target_type == "workflow_stage" else {}
        old_bodies = baseline_stage_bodies if target_type == "workflow_stage" else {}
        for target_id, item in current_by_id.items():
            old = baseline_by_id.get(target_id)
            if old is None:
                added[target_type].append(target_id)
                renamed_from = next(
                    (
                        old_id
                        for old_id, old_item in baseline_by_id.items()
                        if old_id not in current_by_id
                        and business_value(item, current_bodies) == business_value(old_item, old_bodies)
                    ),
                    None,
                )
                if renamed_from:
                    rename_diagnostics.append(
                        _diagnostic(
                            f"{_target_collection_name(target_type)}.{target_id}",
                            "stable_id_changed",
                            f"未变化的 {target_type} 不得重命名稳定 ID；请恢复原 ID：{renamed_from}",
                            example=renamed_from,
                        )
                    )
            elif business_value(item, current_bodies) == business_value(old, old_bodies):
                inherited[target_type].append(target_id)
            else:
                changed[target_type].append(target_id)
        removed[target_type].extend(sorted(set(baseline_by_id) - set(current_by_id)))

    if rename_diagnostics:
        diagnostics = _sort_diagnostics(rename_diagnostics)
        raise BuilderCandidateValidationError(_diagnostics_message(diagnostics), diagnostics)

    inherited_targets = {
        (target_type, target_id)
        for target_type, target_ids in inherited.items()
        for target_id in target_ids
    }
    candidate_payload = candidate.model_dump(mode="json")
    current_evidence_targets = {
        (target.target_type, target.target_id)
        for item in candidate.evidence_map
        if _evidence_item_has_supported_source(item)
        for target in item.used_in
    }
    missing_evidence_targets = inherited_targets - current_evidence_targets
    inherited_evidence_count = 0
    for item in baseline.evidence_map:
        selected_targets = [
            target.model_dump(mode="json")
            for target in item.used_in
            if (target.target_type, target.target_id) in missing_evidence_targets
        ]
        if not selected_targets:
            continue
        inherited_item = item.model_dump(mode="json")
        inherited_item["used_in"] = selected_targets
        candidate_payload["evidence_map"].append(inherited_item)
        inherited_evidence_count += len(selected_targets)

    baseline_standard_targets = {
        (item.standard_ref, item.clause_ref, item.usage, target.target_type, target.target_id)
        for item in baseline.industry_standard_usage
        for target in item.used_in
        if (target.target_type, target.target_id) in inherited_targets
    }
    current_standard_targets = {
        (target.target_type, target.target_id)
        for item in candidate.industry_standard_usage
        for target in item.used_in
    }
    missing_standard_targets = inherited_targets - current_standard_targets
    inherited_standard_count = 0
    for item in baseline.industry_standard_usage:
        selected_targets = [
            target.model_dump(mode="json")
            for target in item.used_in
            if (target.target_type, target.target_id) in missing_standard_targets
        ]
        if not selected_targets:
            continue
        inherited_item = item.model_dump(mode="json")
        inherited_item["used_in"] = selected_targets
        candidate_payload["industry_standard_usage"].append(inherited_item)
        inherited_standard_count += len(selected_targets)

    revision_provenance = {
        "baseline_generation_id": baseline_generation_id,
        "baseline_commit_sha": baseline_commit_sha,
        "baseline_candidate_hash": baseline_candidate_hash,
        "inherited_target_ids": {key: sorted(value) for key, value in inherited.items()},
        "changed_target_ids": {key: sorted(value) for key, value in changed.items()},
        "added_target_ids": {key: sorted(value) for key, value in added.items()},
        "removed_target_ids": {key: sorted(value) for key, value in removed.items()},
        "inherited_evidence_count": inherited_evidence_count,
        "inherited_industry_standard_count": inherited_standard_count,
    }
    return candidate_payload, revision_provenance, baseline_standard_targets


def _target_collection_name(target_type: str) -> str:
    return {
        "workflow_stage": "workflow_step_candidates",
        "safety_constraint": "safety_constraints",
        "expected_evidence": "expected_evidence_requirements",
    }[target_type]


def _workflow_stage_bodies(skill_md: str) -> dict[str, str]:
    normalized = skill_md.replace("\r\n", "\n").replace("\r", "\n")
    headings = list(re.finditer(r"^(?P<marks>#{1,6})\s+.*$", normalized, re.MULTILINE))
    bodies: dict[str, str] = {}
    for index, heading in enumerate(headings):
        stage_match = WORKFLOW_HEADING_PATTERN.fullmatch(heading.group(0))
        if stage_match is None:
            continue
        level = len(heading.group("marks"))
        end = len(normalized)
        for following in headings[index + 1 :]:
            if len(following.group("marks")) <= level:
                end = following.start()
                break
        bodies[stage_match.group("stage_id")] = normalized[heading.end() : end].strip()
    return bodies


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
    inherited_industry_standard_targets: set[tuple[str, str, str, str, str]] | None = None,
) -> BuilderCandidate:
    try:
        candidate = parse_builder_candidate(payload)
    except ValidationError as exc:
        diagnostics = _sort_diagnostics([_pydantic_diagnostic(error) for error in exc.errors()])
        raise BuilderCandidateValidationError(_diagnostics_message(diagnostics), diagnostics) from exc

    diagnostics: list[dict[str, Any]] = []
    normalized_files, file_diagnostics = _validate_files(candidate.files)
    diagnostics.extend(file_diagnostics)
    diagnostics.extend(_validate_material_usage(candidate.material_usage))
    diagnostics.extend(_validate_selected_reference_assets(candidate.selected_reference_assets, candidate_reference_assets or []))
    diagnostics.extend(
        _validate_industry_standard_usage(
            candidate.industry_standard_usage,
            standard_search_results or [],
            standard_search_status,
            inherited_industry_standard_targets or set(),
        )
    )
    diagnostics.extend(
        _validate_standard_search_degradation(
            candidate,
            standard_search_status,
            inherited_industry_standard_targets or set(),
        )
    )
    diagnostics.extend(_validate_required_collections(candidate))
    diagnostics.extend(_validate_missing_questions(candidate.missing_questions, candidate.review_notes))
    diagnostics.extend(_validate_identifiers_and_references(candidate))
    diagnostics.extend(_validate_workflow(candidate.workflow_step_candidates, normalized_files.get("SKILL.md", "")))
    diagnostics.extend(_validate_evidence_coverage(candidate))
    diagnostics.extend(_validate_reference_paths_used(candidate.selected_reference_assets, normalized_files))
    diagnostics = _sort_diagnostics(diagnostics)
    if diagnostics:
        raise BuilderCandidateValidationError(_diagnostics_message(diagnostics), diagnostics)
    candidate.files = normalized_files
    return candidate


def builder_candidate_input_schema() -> dict[str, Any]:
    """Return the submit_candidate JSON schema from the validation contract."""
    schema = BuilderCandidate.model_json_schema()
    schema["description"] = (
        "PSOP Builder Candidate Schema v2。所有跨对象关联必须使用稳定 ID 和结构化 target；"
        "evidence_map.source_refs 必须使用结构化来源对象。"
    )
    return schema


def _validate_files(files: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    normalized: dict[str, str] = {}
    if not files:
        diagnostics.append(_diagnostic("files", "required", "files 必须是非空对象。"))
        return normalized, diagnostics
    for raw_path, content in files.items():
        try:
            path = normalize_builder_path(raw_path)
        except ValueError as exc:
            diagnostics.append(_diagnostic(f"files.{raw_path}", "invalid_path", str(exc)))
            continue
        if path in normalized:
            diagnostics.append(_diagnostic(f"files.{raw_path}", "duplicate_path", f"生成文件路径归一化后重复：{path}"))
            continue
        if path in FORBIDDEN_BUILDER_FILES:
            diagnostics.append(_diagnostic(f"files.{raw_path}", "forbidden_file", "builder candidate 不得包含 skill.yaml。"))
        if not content.strip():
            diagnostics.append(_diagnostic(f"files.{raw_path}", "empty_file", f"{path} 内容不能为空。"))
        for pattern in PLACEHOLDER_PATTERNS:
            if pattern.search(content):
                diagnostics.append(_diagnostic(f"files.{raw_path}", "placeholder_content", f"{path} 包含占位内容：{pattern.pattern}"))
        normalized[path] = content
    for path in REQUIRED_BUILDER_FILES:
        if not normalized.get(path):
            diagnostics.append(_diagnostic(f"files.{path}", "missing_required_file", f"builder candidate 缺少必需文件：{path}"))
    return normalized, diagnostics


def _validate_material_usage(items: list[MaterialUsageItem]) -> list[dict[str, Any]]:
    if items:
        return []
    return [_diagnostic("material_usage", "required", "material_usage 必须非空。", example={"material_id": "material-1", "usage": "支撑阶段 1。"})]


def _validate_selected_reference_assets(
    items: list[SelectedReferenceAsset],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    if not items:
        diagnostics.append(_diagnostic("selected_reference_assets", "required", "selected_reference_assets 必须至少选择一项。"))
    if len(items) > MAX_BUILDER_REFERENCE_ASSETS:
        diagnostics.append(
            _diagnostic(
                "selected_reference_assets",
                "max_items",
                f"selected_reference_assets 数量不能超过 {MAX_BUILDER_REFERENCE_ASSETS}。",
            )
        )
    candidate_ids = {str(item.get("asset_id") or item.get("id") or "").strip() for item in candidates}
    candidate_paths = {str(item.get("reference_path") or "").strip() for item in candidates}
    for index, item in enumerate(items):
        if candidate_ids and item.asset_id not in candidate_ids:
            diagnostics.append(
                _diagnostic(
                    f"selected_reference_assets.{index}.asset_id",
                    "unknown_reference_asset",
                    f"selected_reference_assets 包含非候选资产：{item.asset_id}",
                )
            )
        if candidate_paths and item.reference_path not in candidate_paths:
            diagnostics.append(
                _diagnostic(
                    f"selected_reference_assets.{index}.reference_path",
                    "unknown_reference_path",
                    f"selected_reference_assets 包含非候选 reference_path：{item.reference_path}",
                )
            )
    return diagnostics


def _validate_industry_standard_usage(
    items: list[IndustryStandardUsageItem],
    search_results: list[dict[str, Any]],
    search_status: str | None,
    inherited_targets: set[tuple[str, str, str, str, str]],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    known_pairs = {
        (str(item.get("standard_ref") or "").strip(), str(item.get("clause_ref") or "").strip())
        for item in search_results
        if str(item.get("citation_status") or "complete") != "incomplete"
    }
    for index, item in enumerate(items):
        if _industry_usage_is_inherited(item, inherited_targets):
            continue
        if item.usage == "reference_only" and search_status in {None, "success"}:
            continue
        missing = [
            key
            for key, value in {
                "standard_ref": item.standard_ref,
                "clause_ref": item.clause_ref,
                "usage": item.usage,
                "used_in": item.used_in,
            }.items()
            if not value
        ]
        if missing:
            diagnostics.append(
                _diagnostic(
                    f"industry_standard_usage.{index}",
                    "missing_fields",
                    f"industry_standard_usage 缺少字段：{missing}",
                )
            )
        pair = (item.standard_ref.strip(), item.clause_ref.strip())
        if known_pairs and pair not in known_pairs:
            diagnostics.append(
                _diagnostic(
                    f"industry_standard_usage.{index}",
                    "unknown_standard_reference",
                    f"industry_standard_usage 引用了未由 psop.standard.search 返回的标准条款：{pair}",
                )
            )
    return diagnostics


def _validate_standard_search_degradation(
    candidate: BuilderCandidate,
    search_status: str | None,
    inherited_targets: set[tuple[str, str, str, str, str]],
) -> list[dict[str, Any]]:
    if search_status not in {"timeout", "service_unavailable", "internal_error"}:
        return []
    diagnostics: list[dict[str, Any]] = []
    current_usage = [
        item
        for item in candidate.industry_standard_usage
        if not _industry_usage_is_inherited(item, inherited_targets)
    ]
    if current_usage:
        diagnostics.append(
            _diagnostic(
                "industry_standard_usage",
                "standard_search_unavailable",
                "标准检索不可用时不得引用 industry_standard。",
                example=[],
            )
        )
    if not any("标准检索不可用，未引用行业标准" in note for note in candidate.review_notes):
        diagnostics.append(
            _diagnostic(
                "review_notes",
                "missing_standard_unavailable_note",
                "标准检索不可用时 review_notes 必须包含固定说明。",
                example="标准检索不可用，未引用行业标准。",
            )
        )
    return diagnostics


def _industry_usage_is_inherited(
    item: IndustryStandardUsageItem,
    inherited_targets: set[tuple[str, str, str, str, str]],
) -> bool:
    return bool(item.used_in) and all(
        (item.standard_ref, item.clause_ref, item.usage, target.target_type, target.target_id) in inherited_targets
        for target in item.used_in
    )


def _validate_required_collections(candidate: BuilderCandidate) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for field_name, items, message in (
        ("evidence_map", candidate.evidence_map, "evidence_map 必须非空。"),
        ("safety_constraints", candidate.safety_constraints, "safety_constraints 必须非空。"),
        ("workflow_step_candidates", candidate.workflow_step_candidates, "workflow_step_candidates 必须非空。"),
        ("expected_evidence_requirements", candidate.expected_evidence_requirements, "expected_evidence_requirements 必须非空。"),
    ):
        if not items:
            diagnostics.append(_diagnostic(field_name, "required", message))
    return diagnostics


def _validate_missing_questions(
    items: list[MissingQuestionItem],
    review_notes: list[str],
) -> list[dict[str, Any]]:
    if any(item.blocking_level == "blocking" for item in items) and not review_notes:
        return [_diagnostic("review_notes", "missing_blocking_review_note", "存在 blocking missing_questions 时 review_notes 必须说明审阅风险。")]
    return []


def _validate_identifiers_and_references(candidate: BuilderCandidate) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    stage_ids = [item.stage_id for item in candidate.workflow_step_candidates]
    constraint_ids = [item.constraint_id for item in candidate.safety_constraints]
    requirement_ids = [item.requirement_id for item in candidate.expected_evidence_requirements]
    diagnostics.extend(_duplicate_id_diagnostics("workflow_step_candidates", "stage_id", stage_ids))
    diagnostics.extend(_duplicate_id_diagnostics("safety_constraints", "constraint_id", constraint_ids))
    diagnostics.extend(_duplicate_id_diagnostics("expected_evidence_requirements", "requirement_id", requirement_ids))

    known_stage_ids = set(stage_ids)
    for index, item in enumerate(candidate.safety_constraints):
        if item.scope == "all_stages" and item.stage_ids:
            diagnostics.append(
                _diagnostic(
                    f"safety_constraints.{index}.stage_ids",
                    "invalid_scope_stage_ids",
                    "scope=all_stages 时 stage_ids 必须为空。",
                    example=[],
                )
            )
        if item.scope == "selected_stages" and not item.stage_ids:
            diagnostics.append(
                _diagnostic(
                    f"safety_constraints.{index}.stage_ids",
                    "missing_stage_ids",
                    "scope=selected_stages 时 stage_ids 必须非空。",
                    example=["stage_01_inventory"],
                )
            )
        diagnostics.extend(_unknown_ids(f"safety_constraints.{index}.stage_ids", item.stage_ids, known_stage_ids, "unknown_stage_id"))

    for index, item in enumerate(candidate.expected_evidence_requirements):
        if item.stage_id not in known_stage_ids:
            diagnostics.append(
                _diagnostic(
                    f"expected_evidence_requirements.{index}.stage_id",
                    "unknown_stage_id",
                    f"expected evidence 引用了未声明的 stage_id：{item.stage_id}",
                )
            )

    for index, item in enumerate(candidate.selected_reference_assets):
        diagnostics.extend(
            _unknown_ids(
                f"selected_reference_assets.{index}.stage_ids",
                item.stage_ids,
                known_stage_ids,
                "unknown_stage_id",
            )
        )

    known_targets = {
        "workflow_stage": known_stage_ids,
        "safety_constraint": set(constraint_ids),
        "expected_evidence": set(requirement_ids),
        "review_notes": {"review_notes"},
    }
    for field_name, groups in (
        ("evidence_map", [item.used_in for item in candidate.evidence_map]),
        ("industry_standard_usage", [item.used_in for item in candidate.industry_standard_usage]),
    ):
        for item_index, targets in enumerate(groups):
            for target_index, target in enumerate(targets):
                if target.target_id not in known_targets[target.target_type]:
                    diagnostics.append(
                        _diagnostic(
                            f"{field_name}.{item_index}.used_in.{target_index}.target_id",
                            "unknown_evidence_target",
                            f"{target.target_type} 引用了未声明的 target_id：{target.target_id}",
                        )
                    )
    return diagnostics


def _validate_workflow(
    workflow_steps: list[WorkflowStepCandidate],
    skill_md: str,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    headings: dict[str, list[str]] = {}
    for match in WORKFLOW_HEADING_PATTERN.finditer(skill_md):
        headings.setdefault(match.group("stage_id"), []).append(match.group("title").strip())
    for index, item in enumerate(workflow_steps):
        titles = headings.get(item.stage_id, [])
        if not titles:
            diagnostics.append(
                _diagnostic(
                    f"workflow_step_candidates.{index}.stage_id",
                    "workflow_not_in_skill",
                    f"SKILL.md 缺少 workflow 标题：[{item.stage_id}] {item.title}",
                    example=f"### [{item.stage_id}] {item.title}",
                )
            )
            continue
        if len(titles) > 1:
            diagnostics.append(
                _diagnostic(
                    f"workflow_step_candidates.{index}.stage_id",
                    "duplicate_workflow_heading",
                    f"SKILL.md 中 stage_id 重复：{item.stage_id}",
                )
            )
        if not any(_normalize_match_text(title) == _normalize_match_text(item.title) for title in titles):
            diagnostics.append(
                _diagnostic(
                    f"workflow_step_candidates.{index}.title",
                    "workflow_title_mismatch",
                    f"SKILL.md 中 stage_id={item.stage_id} 的标题与 candidate 不一致。",
                    example=f"### [{item.stage_id}] {item.title}",
                )
            )
    return diagnostics


def _validate_evidence_coverage(candidate: BuilderCandidate) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    supported_targets = {
        (target.target_type, target.target_id)
        for item in candidate.evidence_map
        if _evidence_item_has_supported_source(item)
        for target in item.used_in
    }
    required_targets = [
        *(
            ("workflow_step_candidates", index, "workflow_stage", item.stage_id, "工作流阶段")
            for index, item in enumerate(candidate.workflow_step_candidates)
        ),
        *(
            ("safety_constraints", index, "safety_constraint", item.constraint_id, "安全约束")
            for index, item in enumerate(candidate.safety_constraints)
        ),
        *(
            ("expected_evidence_requirements", index, "expected_evidence", item.requirement_id, "预期证据要求")
            for index, item in enumerate(candidate.expected_evidence_requirements)
        ),
    ]
    for field_name, index, target_type, target_id, label in required_targets:
        if (target_type, target_id) not in supported_targets:
            matching_evidence = [
                (evidence_index, item)
                for evidence_index, item in enumerate(candidate.evidence_map)
                if any(target.target_type == target_type and target.target_id == target_id for target in item.used_in)
            ]
            if matching_evidence:
                evidence_index, item = matching_evidence[0]
                unsupported_sources = sorted({source.source_type for source in item.source_refs})
                diagnostics.append(
                    _diagnostic(
                        f"evidence_map.{evidence_index}.source_refs",
                        "unsupported_evidence_source_for_required_target",
                        (
                            f"{label} {target_id} 仅由不可验证来源支撑：{unsupported_sources}；"
                            "强制目标必须引用用户确认、素材分析、参考资产或可追溯行业标准。"
                        ),
                        allowed_values=sorted(SUPPORTED_REQUIRED_EVIDENCE_SOURCE_TYPES),
                        example={
                            "source_refs": [{"source_type": "material_analysis", "material_id": "material-1"}],
                            "used_in": [{"target_type": target_type, "target_id": target_id}],
                        },
                    )
                )
                continue
            diagnostics.append(
                _diagnostic(
                    f"{field_name}.{index}",
                    "missing_evidence_coverage",
                    f"{label}缺少可验证 evidence_map.used_in 覆盖：{target_id}",
                    example={"target_type": target_type, "target_id": target_id},
                )
            )
    return diagnostics


def _evidence_item_has_supported_source(item: EvidenceMapItem) -> bool:
    return any(
        source.source_type in SUPPORTED_REQUIRED_EVIDENCE_SOURCE_TYPES
        for source in item.source_refs
    )


def _validate_reference_paths_used(
    items: list[SelectedReferenceAsset],
    files: dict[str, str],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    skill_md = files.get("SKILL.md", "")
    docs = "\n".join(files.get(path, "") for path in ("SKILL.md", "references/README.md"))
    for index, item in enumerate(items):
        if item.reference_path not in skill_md:
            diagnostics.append(
                _diagnostic(
                    f"selected_reference_assets.{index}.reference_path",
                    "reference_not_in_skill",
                    f"选中的 reference_path 未被 SKILL.md 流程内容引用：{item.reference_path}",
                )
            )
        if item.reference_path not in docs:
            diagnostics.append(
                _diagnostic(
                    f"selected_reference_assets.{index}.reference_path",
                    "reference_not_in_docs",
                    f"选中的 reference_path 未被候选文档引用：{item.reference_path}",
                )
            )
    return diagnostics


def _duplicate_id_diagnostics(field_name: str, id_field: str, values: list[str]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        if value in seen:
            diagnostics.append(
                _diagnostic(
                    f"{field_name}.{index}.{id_field}",
                    "duplicate_id",
                    f"{id_field} 必须唯一：{value}",
                )
            )
        seen.add(value)
    return diagnostics


def _unknown_ids(path: str, values: list[str], known: set[str], code: str) -> list[dict[str, Any]]:
    return [
        _diagnostic(path, code, f"引用了未声明的 stage_id：{value}")
        for value in values
        if value not in known
    ]


def _normalize_match_text(value: str) -> str:
    return re.sub(r"[\s\u3000:：\-—_/、，,。.\(\)（）]+", "", value.lower())


def validation_diagnostics(exc: Exception) -> list[dict[str, Any]]:
    if isinstance(exc, BuilderCandidateValidationError):
        return exc.diagnostics
    if isinstance(exc, ValidationError):
        return _sort_diagnostics([_pydantic_diagnostic(error) for error in exc.errors()])
    return [_diagnostic("candidate", "invalid_candidate", str(exc))]


def _pydantic_diagnostic(error: dict[str, Any]) -> dict[str, Any]:
    path = ".".join(str(part) for part in error.get("loc", [])) or "candidate"
    message = str(error.get("msg") or "候选产物不符合 schema。")
    if path.endswith("source_type"):
        allowed_values = sorted(ALLOWED_EVIDENCE_SOURCE_TYPES)
        example: Any = {"source_type": "current_source", "ref": "SKILL.md"}
    elif path.endswith("target_type"):
        allowed_values = sorted(ALLOWED_EVIDENCE_TARGET_TYPES)
        example = {"target_type": "workflow_stage", "target_id": "stage_01_inventory"}
    elif path.endswith("support_level"):
        allowed_values = [
            "observed_fact",
            "standard_reference",
            "current_source_fact",
            "builder_inference",
            "human_confirmation_required",
            "confirmed_instruction",
        ]
        example = "observed_fact"
    elif path == "schema_version":
        allowed_values = [BUILDER_CANDIDATE_SCHEMA_VERSION]
        example = BUILDER_CANDIDATE_SCHEMA_VERSION
    elif path.endswith(".scope"):
        allowed_values = ["all_stages", "selected_stages"]
        example = "selected_stages"
    elif path.startswith("material_usage"):
        allowed_values = []
        example = {"material_id": "material-1", "usage": "支撑阶段 1。"}
    elif path.startswith("missing_questions"):
        allowed_values = []
        example = {"question": "接口型号是否已确认？", "reason": "素材未显示标签。", "blocking_level": "non_blocking"}
    else:
        allowed_values = []
        example = None
    return _diagnostic(path, str(error.get("type") or "schema_validation"), message, allowed_values=allowed_values, example=example)


def _diagnostic(
    path: str,
    code: str,
    message: str,
    *,
    allowed_values: list[Any] | None = None,
    example: Any = None,
) -> dict[str, Any]:
    return {
        "path": path,
        "code": code,
        "message": message,
        "allowed_values": allowed_values or [],
        "example": example,
    }


def _sort_diagnostics(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for diagnostic in diagnostics:
        key = (
            str(diagnostic.get("path") or "candidate"),
            str(diagnostic.get("code") or "invalid_candidate"),
            str(diagnostic.get("message") or "候选字段无效。"),
        )
        unique.setdefault(key, diagnostic)
    return [unique[key] for key in sorted(unique)]


def _diagnostics_message(diagnostics: list[dict[str, Any]]) -> str:
    if not diagnostics:
        return "builder candidate 校验失败。"
    first = diagnostics[0]
    return (
        f"builder candidate 校验失败（共 {len(diagnostics)} 项）："
        f"{first.get('path') or 'candidate'}：{first.get('message') or '候选字段无效。'}"
    )
