from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Any

from app.agent_harness.agents.psop.builder.schemas import REQUIRED_BUILDER_FILES, validate_builder_candidate
from app.agent_harness.sandbox.base import PSOP_OUTPUTS_VIRTUAL_ROOT
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec


BUILDER_RESULT_VIRTUAL_PATH = f"{PSOP_OUTPUTS_VIRTUAL_ROOT}/builder-result.json"
BUILDER_DRAFT_FILES_VIRTUAL_ROOT = f"{PSOP_OUTPUTS_VIRTUAL_ROOT}/skill-draft"
BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY = "_psop_builder_reference_asset_files"
_REFERENCE_ASSETS_CONTEXT_KEY = "_psop_builder_reference_assets"
_STANDARD_RESULTS_CONTEXT_KEY = "_psop_builder_standard_results"
_SUBMIT_CANDIDATE_ERROR_COUNT_CONTEXT_KEY = "_psop_builder_submit_candidate_error_count"
_SUBMIT_CANDIDATE_REQUIRED_FIELDS = [
    "directory_tree",
    "files",
    "generation_reason",
    "review_notes",
    "material_usage",
    "industry_standard_usage",
    "selected_reference_assets",
    "evidence_map",
    "missing_questions",
    "safety_constraints",
    "workflow_step_candidates",
    "expected_evidence_requirements",
]


def register_builder_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="psop.builder.read_current_source",
            description="读取本次构建请求中的当前 Skill source。",
            purpose="用于 psop.builder 获取已由应用层准备好的 README.md 和 SKILL.md，不直接访问 GitLab。",
            input_schema={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["README.md", "SKILL.md"]},
                        "minItems": 1,
                        "maxItems": 2,
                    }
                },
                "additionalProperties": False,
            },
            max_result_chars=40000,
        ),
        _read_current_source,
    )
    registry.register(
        ToolSpec(
            name="psop.builder.list_materials",
            description="列出本次构建可使用的素材和分析摘要。",
            purpose="用于 psop.builder 建立素材边界，不返回完整 OCR/ASR 或视觉分析正文。",
            input_schema={
                "type": "object",
                "properties": {
                    "material_kinds": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["video", "image", "audio", "document", "text", "other"]},
                        "maxItems": 8,
                    },
                    "analysis_status": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["pending", "running", "succeeded", "failed"]},
                        "maxItems": 4,
                    },
                    "max_items": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            max_result_chars=20000,
        ),
        _list_materials,
    )
    registry.register(
        ToolSpec(
            name="psop.builder.read_material_analysis",
            description="读取指定素材的裁剪后分析结果。",
            purpose="用于 psop.builder 获取素材中的结构化事实、风险、动作、状态、证据候选和不确定项。",
            input_schema={
                "type": "object",
                "required": ["material_id"],
                "properties": {
                    "material_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "detail_level": {"type": "string", "enum": ["summary", "evidence", "full"], "default": "evidence"},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 24000},
                },
                "additionalProperties": False,
            },
            max_result_chars=26000,
        ),
        _read_material_analysis,
    )
    registry.register(
        ToolSpec(
            name="psop.builder.list_reference_assets",
            description="列出本次构建可选择的运行时参考资产。",
            purpose="用于 psop.builder 选择真正支持运行时判断的关键帧或参考片段。",
            input_schema={
                "type": "object",
                "properties": {
                    "material_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "asset_kinds": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["keyframe", "image", "clip", "document_excerpt"]},
                        "maxItems": 8,
                    },
                    "max_items": {"type": "integer", "minimum": 1, "maximum": 100},
                    "cursor": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
            max_result_chars=20000,
        ),
        _list_reference_assets,
    )
    registry.register(
        ToolSpec(
            name="psop.builder.submit_candidate",
            description="提交并校验 PSOP Skill draft candidate。",
            purpose="用于 psop.builder 写入正式候选产物 builder-result.json，并将 PSOP Skill files 物化到 sandbox outputs/skill-draft。参数必须直接包含完整 candidate，不接受 workspace 文件路径或只包含 evidence_map 的部分参数。",
            input_schema={
                "type": "object",
                "required": _SUBMIT_CANDIDATE_REQUIRED_FIELDS,
                "properties": {
                    "directory_tree": {"type": "string", "minLength": 1, "description": "生成文件树，必须列出所有必需 Markdown 文件。"},
                    "files": {
                        "type": "object",
                        "description": "完整 PSOP Skill 文件内容对象。键必须至少包含 README.md、SKILL.md、prompts/system.md、references/README.md、examples/input.md、examples/expected-output.md、tests/checklist.md；值必须是完整 Markdown 文本。",
                        "additionalProperties": {"type": "string", "minLength": 1},
                    },
                    "generation_reason": {"type": "string", "minLength": 1, "description": "说明本次生成依据和主要取舍。"},
                    "review_notes": {"type": "array", "items": {"type": "string"}, "description": "审阅注意事项和不可阻塞风险。"},
                    "material_usage": {"type": "array", "items": {"type": "object"}, "description": "素材使用说明，每项必须包含 material_id 和 usage。"},
                    "industry_standard_usage": {"type": "array", "items": {"type": "object"}, "description": "行业标准使用说明；无可追溯结果时传空数组并在 review_notes 说明。"},
                    "selected_reference_assets": {"type": "array", "items": {"type": "object"}, "description": "选中的参考资产，最多 12 项，每项包含 asset_id、material_id、reference_path、reason。"},
                    "evidence_map": {"type": "array", "items": {"type": "object"}, "description": "关键结论证据映射，每项包含 claim、support_level、source_refs、used_in。"},
                    "missing_questions": {"type": "array", "items": {"type": "object"}, "description": "需要人工确认的问题，每项包含 question、reason、blocking_level。"},
                    "safety_constraints": {"type": "array", "items": {"type": "object"}, "description": "安全约束，每项包含 constraint、applies_to、risk_type、required_action。"},
                    "workflow_step_candidates": {"type": "array", "items": {"type": "object"}, "description": "工作流阶段候选，阶段编号或标题必须能在 SKILL.md 中找到。"},
                    "expected_evidence_requirements": {"type": "array", "items": {"type": "object"}, "description": "预期证据要求，每项包含 evidence_type 和 completion_criteria。"},
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


def _read_current_source(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    current_source = _input(context).get("current_source")
    if not isinstance(current_source, dict):
        return _error_result("not_found", "本次 invocation input 中缺少 current_source。", ["psop.builder.list_materials"])
    requested = arguments.get("paths")
    paths = [str(path) for path in requested] if isinstance(requested, list) and requested else ["README.md", "SKILL.md"]
    files = {}
    for path in paths:
        if path not in {"README.md", "SKILL.md"}:
            return _error_result("invalid_arguments", f"不支持读取 source path：{path}", ["psop.builder.read_current_source"])
        content = str(current_source.get(path) or "")
        files[path] = {"content": _truncate(content, 40000), "truncated": len(content) > 40000}
    skill = _input(context).get("skill") if isinstance(_input(context).get("skill"), dict) else {}
    return {
        "status": "success",
        "summary": f"读取当前 Skill source：{', '.join(files)}。",
        "source_ref": skill.get("source_ref") or "",
        "source_commit_sha": skill.get("source_commit_sha") or "",
        "files": files,
        "trust_level": "current_source",
        "truncated": any(item["truncated"] for item in files.values()),
        "next_valid_actions": ["psop.builder.list_materials", "psop.builder.read_material_analysis"],
    }


def _list_materials(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    analyses = _material_analyses(context)
    material_ids = [str(item) for item in _input(context).get("material_ids") or []]
    kinds = set(str(item) for item in arguments.get("material_kinds") or [])
    statuses = set(str(item) for item in arguments.get("analysis_status") or [])
    max_items = _bounded_int(arguments.get("max_items"), default=100, minimum=1, maximum=100)
    items = []
    for index, analysis in enumerate(analyses):
        material_id = _material_id(analysis, material_ids, index)
        kind = str(analysis.get("kind") or analysis.get("material_kind") or analysis.get("source_type") or "other")
        status = str(analysis.get("analysis_status") or analysis.get("status") or "succeeded")
        if kinds and kind not in kinds:
            continue
        if statuses and status not in statuses:
            continue
        items.append(
            {
                "material_id": material_id,
                "kind": kind,
                "filename": str(analysis.get("filename") or analysis.get("name") or ""),
                "analysis_id": str(analysis.get("analysis_id") or ""),
                "analysis_status": status,
                "summary": _analysis_summary(analysis),
                "artifact_ref": str(analysis.get("artifact_ref") or ""),
            }
        )
    items = items[:max_items]
    return {
        "status": "success",
        "summary": f"列出 {len(items)} 个可用素材。",
        "items": items,
        "truncated": len(items) >= max_items,
        "next_valid_actions": ["psop.builder.read_material_analysis", "psop.builder.list_reference_assets"],
    }


def _read_material_analysis(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    material_id = _require_str(arguments, "material_id")
    detail_level = str(arguments.get("detail_level") or "evidence")
    max_chars = _bounded_int(arguments.get("max_chars"), default=24000, minimum=1000, maximum=24000)
    material_ids = [str(item) for item in _input(context).get("material_ids") or []]
    for index, analysis in enumerate(_material_analyses(context)):
        if _material_id(analysis, material_ids, index) != material_id:
            continue
        result = {
            "status": "success",
            "material_id": material_id,
            "analysis_id": str(analysis.get("analysis_id") or ""),
            "analysis_summary": _analysis_summary(analysis),
            "observed_actions": _list_value(analysis, "observed_actions", "actions", "workflow_steps"),
            "observed_states": _list_value(analysis, "observed_states", "states"),
            "detected_risks": _list_value(analysis, "detected_risks", "risks", "safety_risks"),
            "uncertainties": _list_value(analysis, "uncertainties", "unknowns"),
            "evidence_candidates": _list_value(analysis, "evidence_candidates", "evidence_items"),
            "artifact_ref": str(analysis.get("artifact_ref") or ""),
            "trust_level": "untrusted_material_analysis",
            "truncated": False,
            "next_valid_actions": ["psop.builder.list_reference_assets", "psop.standard.search"],
        }
        if detail_level == "full":
            raw = json.dumps(analysis, ensure_ascii=False, sort_keys=True)
            result["raw_analysis"] = _truncate(raw, max_chars)
            result["truncated"] = len(raw) > max_chars
        return result
    return _error_result("not_found", f"未找到素材分析结果：{material_id}", ["psop.builder.list_materials"])


def _list_reference_assets(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    assets = _candidate_reference_assets(context)
    material_id = str(arguments.get("material_id") or "").strip()
    kinds = set(str(item) for item in arguments.get("asset_kinds") or [])
    max_items = _bounded_int(arguments.get("max_items"), default=100, minimum=1, maximum=100)
    items = []
    for asset in assets:
        asset_material_id = str(asset.get("material_id") or "")
        asset_kind = str(asset.get("asset_kind") or asset.get("kind") or "keyframe")
        if material_id and asset_material_id != material_id:
            continue
        if kinds and asset_kind not in kinds:
            continue
        observations = asset.get("observations") if isinstance(asset.get("observations"), list) else []
        items.append(
            {
                "asset_id": str(asset.get("asset_id") or asset.get("id") or ""),
                "material_id": asset_material_id,
                "asset_kind": asset_kind,
                "reference_path": str(asset.get("reference_path") or ""),
                "timestamp_ms": asset.get("timestamp_ms"),
                "observation_summary": _truncate("; ".join(str(item) for item in observations) or str(asset.get("label") or ""), 1000),
                "suggested_use": str(asset.get("suggested_use") or asset.get("label") or ""),
                "confidence": asset.get("confidence")
                or ((asset.get("asset_metadata") or {}).get("confidence") if isinstance(asset.get("asset_metadata"), dict) else None),
            }
        )
    items = items[:max_items]
    context.invocation_context[_REFERENCE_ASSETS_CONTEXT_KEY] = items
    return {
        "status": "success",
        "summary": f"列出 {len(items)} 个候选参考资产。",
        "items": items,
        "next_cursor": None,
        "truncated": len(items) >= max_items,
        "next_valid_actions": ["workspace.write_text", "psop.standard.search", "psop.builder.submit_candidate"],
    }


def _submit_candidate(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    reference_assets = context.invocation_context.get(_REFERENCE_ASSETS_CONTEXT_KEY)
    if not isinstance(reference_assets, list) or not reference_assets:
        reference_assets = _candidate_reference_assets(context)
    standard_results = context.invocation_context.get(_STANDARD_RESULTS_CONTEXT_KEY)
    if not isinstance(standard_results, list):
        standard_results = []
    try:
        candidate = validate_builder_candidate(
            arguments,
            candidate_reference_assets=reference_assets,
            standard_search_results=standard_results,
        )
    except ValueError as exc:
        return _submit_candidate_error_result(context, str(exc), "schema_validation")
    payload = candidate.model_dump(mode="json")
    try:
        embedded_files, embedded_image_count, embedded_images = _embed_reference_images_in_files(
            files=candidate.files,
            selected_reference_assets=candidate.selected_reference_assets,
            reference_asset_files=_reference_asset_files(context),
            require_available=BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY in context.invocation_context,
        )
    except ValueError as exc:
        return _submit_candidate_error_result(context, str(exc), "reference_image_embedding")
    payload["embedded_reference_image_count"] = embedded_image_count
    payload["embedded_reference_images"] = embedded_images
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    context.sandbox.write_text(BUILDER_RESULT_VIRTUAL_PATH, content)
    materialized_files = _write_candidate_files(embedded_files, context)
    files_hash = _hash_materialized_files(materialized_files)
    context.event_writer.record(
        "agent.artifact.created",
        {
            "artifact_type": "skill_draft_candidate",
            "artifact_ref": "sandbox://outputs/builder-result.json",
            "content_hash": content_hash,
        },
    )
    context.event_writer.record(
        "agent.artifact.created",
        {
            "artifact_type": "skill_draft_files",
            "artifact_ref": "sandbox://outputs/skill-draft",
            "content_hash": files_hash,
            "file_count": len(materialized_files),
        },
    )
    return {
        "status": "success",
        "artifact_ref": "sandbox://outputs/builder-result.json",
        "files_root_ref": "sandbox://outputs/skill-draft",
        "content_hash": content_hash,
        "files_content_hash": files_hash,
        "materialized_files": [item["artifact_ref"] for item in materialized_files],
        "validation_summary": {
            "file_count": len(candidate.files),
            "reference_asset_count": len(candidate.selected_reference_assets),
            "embedded_reference_image_count": embedded_image_count,
            "standard_usage_count": len(candidate.industry_standard_usage),
            "warning_count": len(candidate.review_notes),
        },
        "next_valid_actions": [],
    }


def _write_candidate_files(files: dict[str, str], context: ToolExecutionContext) -> list[dict[str, str]]:
    materialized = []
    for relative_path, content in sorted(files.items()):
        virtual_path = f"{BUILDER_DRAFT_FILES_VIRTUAL_ROOT}/{relative_path}"
        context.sandbox.write_text(virtual_path, content)
        materialized.append(
            {
                "path": relative_path,
                "artifact_ref": f"sandbox://outputs/skill-draft/{relative_path}",
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    return materialized


def _hash_materialized_files(files: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(str(item.get("path") or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item.get("content_hash") or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _input(context: ToolExecutionContext) -> dict[str, Any]:
    return context.invocation_input or {}


def _material_analyses(context: ToolExecutionContext) -> list[dict[str, Any]]:
    raw = context.invocation_context.get("material_analysis_results")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _candidate_reference_assets(context: ToolExecutionContext) -> list[dict[str, Any]]:
    raw = context.invocation_context.get("candidate_reference_assets")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _reference_asset_files(context: ToolExecutionContext) -> list[dict[str, Any]]:
    raw = context.invocation_context.get(BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY)
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _material_id(analysis: dict[str, Any], material_ids: list[str], index: int) -> str:
    return str(
        analysis.get("material_id")
        or analysis.get("raw_material_id")
        or analysis.get("id")
        or (material_ids[index] if index < len(material_ids) else f"material-{index + 1}")
    )


def _analysis_summary(analysis: dict[str, Any]) -> str:
    for key in ("summary", "analysis_summary", "caption", "description"):
        value = analysis.get(key)
        if isinstance(value, str) and value.strip():
            return _truncate(value.strip(), 1200)
    content = analysis.get("content")
    if isinstance(content, dict):
        text = str(content.get("summary") or content.get("text") or "")
        if text.strip():
            return _truncate(text.strip(), 1200)
    return _truncate(json.dumps(analysis, ensure_ascii=False, sort_keys=True), 1200)


def _list_value(payload: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空字符串。")
    return value.strip()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 20].rstrip() + "\n...[truncated]"


def _embed_reference_images_in_files(
    *,
    files: dict[str, str],
    selected_reference_assets: list[dict[str, Any]],
    reference_asset_files: list[dict[str, Any]],
    require_available: bool,
) -> tuple[dict[str, str], int, list[dict[str, Any]]]:
    image_refs = _build_image_refs(
        selected_reference_assets=selected_reference_assets,
        reference_asset_files=reference_asset_files,
        require_available=require_available,
    )
    if not image_refs:
        return dict(files), 0, []

    embedded_files: dict[str, str] = {}
    total_count = 0
    embedded_images: list[dict[str, Any]] = []
    for path, content in files.items():
        updated, count_by_reference_path = _embed_reference_images_in_markdown(content, image_refs)
        embedded_files[path] = updated
        total_count += sum(count_by_reference_path.values())
        for reference_path, count in count_by_reference_path.items():
            if count:
                embedded_images.append({"file_path": path, "reference_path": reference_path, "embed_count": count})
    return embedded_files, total_count, embedded_images


def _build_image_refs(
    *,
    selected_reference_assets: list[dict[str, Any]],
    reference_asset_files: list[dict[str, Any]],
    require_available: bool,
) -> list[dict[str, str]]:
    file_by_asset_id = {
        str(item.get("asset_id") or "").strip(): item
        for item in reference_asset_files
        if str(item.get("asset_id") or "").strip()
    }
    file_by_reference_path = {
        str(item.get("reference_path") or "").strip(): item
        for item in reference_asset_files
        if str(item.get("reference_path") or "").strip()
    }
    image_refs: list[dict[str, str]] = []
    for index, item in enumerate(selected_reference_assets, start=1):
        asset_id = str(item.get("asset_id") or "").strip()
        reference_path = str(item.get("reference_path") or "").strip()
        file_payload = file_by_asset_id.get(asset_id) or file_by_reference_path.get(reference_path)
        if file_payload is None:
            if require_available and _looks_like_image_reference(reference_path):
                raise ValueError(f"缺少可内嵌参考图片内容：{reference_path}")
            continue
        mime_type = str(file_payload.get("mime_type") or _mime_type_from_path(reference_path)).strip()
        content_base64 = str(file_payload.get("content_base64") or "").strip()
        if not mime_type.startswith("image/") or not content_base64:
            continue
        try:
            base64.b64decode(content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"参考图片内容不是合法 base64：{reference_path}") from exc
        image_refs.append(
            {
                "reference_path": reference_path,
                "data_uri": f"data:{mime_type};base64,{content_base64}",
                "label": _reference_image_label(index, str(item.get("reason") or item.get("asset_id") or reference_path)),
            }
        )
    return image_refs


def _embed_reference_images_in_markdown(content: str, image_refs: list[dict[str, str]]) -> tuple[str, dict[str, int]]:
    updated = content
    count_by_reference_path = {item["reference_path"]: 0 for item in image_refs}
    for item in image_refs:
        updated, markdown_image_count = _replace_markdown_image_target(
            updated,
            item["reference_path"],
            item["data_uri"],
            item["label"],
        )
        count_by_reference_path[item["reference_path"]] += markdown_image_count

    lines = updated.splitlines()
    trailing_newline = updated.endswith("\n")
    output: list[str] = []
    for line in lines:
        matched_refs: list[dict[str, str]] = []
        updated_line = line
        for item in image_refs:
            reference_path = item["reference_path"]
            if reference_path not in updated_line:
                continue
            updated_line = updated_line.replace(f"`{reference_path}`", item["label"])
            updated_line = updated_line.replace(reference_path, item["label"])
            matched_refs.append(item)
        output.append(updated_line)
        for item in matched_refs:
            output.append("")
            output.append(f"![{item['label']}]({item['data_uri']})")
            count_by_reference_path[item["reference_path"]] += 1

    result = "\n".join(output)
    if trailing_newline:
        result += "\n"
    return result, count_by_reference_path


def _replace_markdown_image_target(content: str, reference_path: str, data_uri: str, fallback_label: str) -> tuple[str, int]:
    pattern = re.compile(
        r"!\[([^\]]*)\]\(\s*<?"
        + re.escape(reference_path)
        + r">?\s*(?:\"[^\"]*\"|'[^']*')?\)"
    )

    def replace(match: re.Match[str]) -> str:
        alt = match.group(1).strip() or fallback_label
        return f"![{alt}]({data_uri})"

    return pattern.subn(replace, content)


def _reference_image_label(index: int, value: str) -> str:
    normalized = " ".join(str(value).strip().split())
    if not normalized:
        return f"参考图片 {index}"
    if len(normalized) > 60:
        normalized = normalized[:60].rstrip()
    return f"参考图片 {index}：{normalized}"


def _looks_like_image_reference(path: str) -> bool:
    return _mime_type_from_path(path).startswith("image/")


def _mime_type_from_path(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    return "application/octet-stream"


def _error_result(error_type: str, message: str, next_valid_actions: list[str]) -> dict[str, Any]:
    return {
        "status": "error",
        "type": error_type,
        "message": message,
        "retryable": error_type in {"timeout", "rate_limited", "service_unavailable"},
        "next_valid_actions": next_valid_actions,
    }


def _submit_candidate_error_result(context: ToolExecutionContext, message: str, validation_stage: str) -> dict[str, Any]:
    attempt = int(context.invocation_context.get(_SUBMIT_CANDIDATE_ERROR_COUNT_CONTEXT_KEY) or 0) + 1
    context.invocation_context[_SUBMIT_CANDIDATE_ERROR_COUNT_CONTEXT_KEY] = attempt
    context.event_writer.record(
        "agent.validation.failed",
        {
            "tool_name": "psop.builder.submit_candidate",
            "validation_stage": validation_stage,
            "attempt": attempt,
            "error_type": "invalid_arguments",
            "error": _truncate(message, 1000),
        },
    )
    result = _error_result("invalid_arguments", message, ["psop.builder.submit_candidate", "workspace.write_text"])
    result.update(
        {
            "retryable": True,
            "retry_requires_argument_correction": True,
            "attempt": attempt,
            "validation_stage": validation_stage,
            "required_top_level_fields": _SUBMIT_CANDIDATE_REQUIRED_FIELDS,
            "required_files": REQUIRED_BUILDER_FILES,
            "correction_hint": (
                "请重新调用 psop.builder.submit_candidate，并在本次 tool 参数中直接提供完整 candidate。"
                "不要只提交 evidence_map、workflow_step_candidates 或 workspace 文件路径；files 对象必须包含所有必需 Markdown 文件的完整内容。"
            ),
        }
    )
    return result
