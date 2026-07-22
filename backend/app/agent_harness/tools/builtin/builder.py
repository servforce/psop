from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Any

from app.agent_harness.agents.psop.builder.schemas import (
    REQUIRED_BUILDER_FILES,
    builder_candidate_input_schema,
    reconcile_builder_candidate,
    validate_builder_candidate,
    validation_diagnostics,
)
from app.agent_harness.sandbox.base import PSOP_OUTPUTS_VIRTUAL_ROOT
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec


BUILDER_RESULT_VIRTUAL_PATH = f"{PSOP_OUTPUTS_VIRTUAL_ROOT}/builder-result.json"
BUILDER_DRAFT_FILES_VIRTUAL_ROOT = f"{PSOP_OUTPUTS_VIRTUAL_ROOT}/skill-draft"
BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY = "_psop_builder_reference_asset_files"
BUILDER_REVISION_BASELINE_CONTEXT_KEY = "_psop_builder_revision_baseline"
_REFERENCE_ASSETS_CONTEXT_KEY = "_psop_builder_reference_assets"
_STANDARD_RESULTS_CONTEXT_KEY = "_psop_builder_standard_results"
_STANDARD_SEARCH_STATUS_CONTEXT_KEY = "_psop_builder_standard_search_status"
_SUBMIT_CANDIDATE_ERROR_COUNT_CONTEXT_KEY = "_psop_builder_submit_candidate_error_count"
_SUBMIT_CANDIDATE_REQUIRED_FIELDS = [
    "schema_version",
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
                        "items": {"type": "string", "enum": ["keyframe", "video_keyframe", "image", "clip", "document_excerpt"]},
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
            input_schema=builder_candidate_input_schema(),
            input_schema_mode="raw_json_schema",
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
    baseline = context.invocation_context.get(BUILDER_REVISION_BASELINE_CONTEXT_KEY)
    baseline_summary = baseline.get("summary") if isinstance(baseline, dict) else None
    return {
        "status": "success",
        "summary": f"读取当前 Skill source：{', '.join(files)}。",
        "source_ref": skill.get("source_ref") or "",
        "source_commit_sha": skill.get("source_commit_sha") or "",
        "files": files,
        "trust_level": "current_source",
        "revision_baseline": baseline_summary if isinstance(baseline_summary, dict) else None,
        "truncated": any(item["truncated"] for item in files.values()),
        "next_valid_actions": ["psop.builder.list_materials", "psop.builder.read_material_analysis"],
    }


def _list_materials(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    analyses = _material_analyses(context)
    material_ids = [str(item) for item in _input(context).get("material_ids") or []]
    kinds = {_normalize_material_kind(item) for item in arguments.get("material_kinds") or []}
    statuses = {_normalize_analysis_status(item) for item in arguments.get("analysis_status") or []}
    max_items = _bounded_int(arguments.get("max_items"), default=100, minimum=1, maximum=100)
    items = []
    for index, analysis in enumerate(analyses):
        material_id = _material_id(analysis, material_ids, index)
        kind = _analysis_material_kind(analysis)
        status = _normalize_analysis_status(analysis.get("analysis_status") or analysis.get("status") or "succeeded")
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


def _analysis_material_kind(analysis: dict[str, Any]) -> str:
    source = analysis.get("source") if isinstance(analysis.get("source"), dict) else {}
    mime_type = str(analysis.get("mime_type") or source.get("mime_type") or "")
    return _normalize_material_kind(
        analysis.get("kind")
        or analysis.get("material_kind")
        or analysis.get("material_type")
        or source.get("material_kind")
        or source.get("source_type")
        or _kind_from_mime_type(mime_type)
        or "other"
    )


def _kind_from_mime_type(mime_type: str) -> str:
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type in {"application/pdf", "text/markdown", "text/plain"}:
        return "document"
    return ""


def _normalize_material_kind(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "视频": "video",
        "video_keyframe": "video",
        "keyframe": "video",
        "image_keyframe": "image",
        "图片": "image",
        "图像": "image",
        "音频": "audio",
        "文档": "document",
        "文本": "text",
    }
    return aliases.get(text, text or "other")


def _normalize_analysis_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "ready": "succeeded",
        "success": "succeeded",
        "done": "succeeded",
        "已完成": "succeeded",
        "完成": "succeeded",
    }
    return aliases.get(text, text or "succeeded")


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
    kinds = {_normalize_asset_kind(str(item)) for item in arguments.get("asset_kinds") or []}
    max_items = _bounded_int(arguments.get("max_items"), default=100, minimum=1, maximum=100)
    items = []
    for asset in assets:
        asset_material_id = str(asset.get("material_id") or "")
        source_asset_kind = str(asset.get("asset_kind") or asset.get("kind") or "keyframe")
        asset_kind = _normalize_asset_kind(source_asset_kind)
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
                "source_asset_kind": source_asset_kind,
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
    if "revision_provenance" in arguments:
        return _submit_candidate_error_result(
            context,
            "revision_provenance 只能由平台生成。",
            "schema_validation",
            diagnostics=[
                {
                    "path": "revision_provenance",
                    "code": "platform_field_forbidden",
                    "message": "revision_provenance 只能由平台生成，模型不得提交。",
                    "allowed_values": [],
                    "example": None,
                }
            ],
        )
    reference_assets = _reference_assets_for_validation(context)
    standard_results = context.invocation_context.get(_STANDARD_RESULTS_CONTEXT_KEY)
    if not isinstance(standard_results, list):
        standard_results = []
    try:
        candidate_payload = arguments
        revision_provenance: dict[str, Any] = {}
        inherited_standard_targets: set[tuple[str, str, str, str, str]] = set()
        baseline = context.invocation_context.get(BUILDER_REVISION_BASELINE_CONTEXT_KEY)
        if isinstance(baseline, dict) and baseline.get("inheritance_enabled") is True:
            baseline_candidate = baseline.get("candidate")
            if isinstance(baseline_candidate, dict):
                candidate_payload, revision_provenance, inherited_standard_targets = reconcile_builder_candidate(
                    arguments,
                    baseline_payload=baseline_candidate,
                    baseline_generation_id=str(baseline.get("generation_id") or ""),
                    baseline_commit_sha=str(baseline.get("commit_sha") or ""),
                    baseline_candidate_hash=str(baseline.get("candidate_hash") or ""),
                )
        candidate = validate_builder_candidate(
            candidate_payload,
            candidate_reference_assets=reference_assets,
            standard_search_results=standard_results,
            standard_search_status=str(context.invocation_context.get(_STANDARD_SEARCH_STATUS_CONTEXT_KEY) or "") or None,
            inherited_industry_standard_targets=inherited_standard_targets,
        )
    except ValueError as exc:
        return _submit_candidate_error_result(
            context,
            str(exc),
            "schema_validation",
            diagnostics=validation_diagnostics(exc),
        )
    payload = candidate.model_dump(mode="json")
    if revision_provenance:
        payload["revision_provenance"] = revision_provenance
    selected_reference_assets = [item.model_dump(mode="json") for item in candidate.selected_reference_assets]
    try:
        linked_files, linked_images = _link_reference_images_in_files(
            files=candidate.files,
            selected_reference_assets=selected_reference_assets,
        )
        materialized_reference_images = _write_reference_asset_files(
            selected_reference_assets=selected_reference_assets,
            reference_asset_files=_reference_asset_files(context),
            context=context,
            require_available=BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY in context.invocation_context,
        )
    except ValueError as exc:
        return _submit_candidate_error_result(
            context,
            str(exc),
            "reference_image_materialization",
            diagnostics=validation_diagnostics(exc),
        )
    payload["materialized_reference_image_count"] = len(materialized_reference_images)
    payload["materialized_reference_images"] = materialized_reference_images
    payload["linked_reference_images"] = linked_images
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    context.sandbox.write_text(BUILDER_RESULT_VIRTUAL_PATH, content)
    materialized_files = _write_candidate_files(linked_files, context)
    materialized_files.extend(materialized_reference_images)
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
            "materialized_reference_image_count": len(materialized_reference_images),
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


def _write_reference_asset_files(
    *,
    selected_reference_assets: list[dict[str, Any]],
    reference_asset_files: list[dict[str, Any]],
    context: ToolExecutionContext,
    require_available: bool,
) -> list[dict[str, Any]]:
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
    materialized: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for item in selected_reference_assets:
        asset_id = str(item.get("asset_id") or "").strip()
        reference_path = str(item.get("reference_path") or "").strip()
        if not reference_path or reference_path in seen_paths:
            continue
        file_payload = file_by_asset_id.get(asset_id) or file_by_reference_path.get(reference_path)
        if file_payload is None:
            if require_available and _looks_like_image_reference(reference_path):
                raise ValueError(f"缺少可物化参考图片内容：{reference_path}")
            continue
        mime_type = str(file_payload.get("mime_type") or _mime_type_from_path(reference_path)).strip()
        content_base64 = str(file_payload.get("content_base64") or "").strip()
        if not mime_type.startswith("image/") or not content_base64:
            continue
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"参考图片内容不是合法 base64：{reference_path}") from exc
        virtual_path = f"{BUILDER_DRAFT_FILES_VIRTUAL_ROOT}/{reference_path}"
        resolved_path = context.sandbox.resolve_virtual_path(virtual_path)
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path.write_bytes(content)
        seen_paths.add(reference_path)
        materialized.append(
            {
                "path": reference_path,
                "artifact_ref": f"sandbox://outputs/skill-draft/{reference_path}",
                "content_hash": hashlib.sha256(content).hexdigest(),
                "mime_type": mime_type,
                "size_bytes": len(content),
            }
        )
    return materialized


def _hash_materialized_files(files: list[dict[str, Any]]) -> str:
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


def _normalize_asset_kind(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"video_keyframe", "key_frame", "keyframe_image"}:
        return "keyframe"
    return normalized or "keyframe"


def _reference_asset_files(context: ToolExecutionContext) -> list[dict[str, Any]]:
    raw = context.invocation_context.get(BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY)
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _reference_assets_for_validation(context: ToolExecutionContext) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in (context.invocation_context.get(_REFERENCE_ASSETS_CONTEXT_KEY), _candidate_reference_assets(context)):
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
            reference_path = str(item.get("reference_path") or "").strip()
            key = (asset_id, reference_path)
            if key in seen:
                continue
            seen.add(key)
            assets.append(item)
    return assets


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


def _link_reference_images_in_files(
    *,
    files: dict[str, str],
    selected_reference_assets: list[dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    image_refs = _build_markdown_image_refs(selected_reference_assets)
    if not image_refs:
        return dict(files), []

    linked_files: dict[str, str] = {}
    linked_images: list[dict[str, Any]] = []
    for path, content in files.items():
        updated, count_by_reference_path = _link_reference_images_in_markdown(content, image_refs)
        linked_files[path] = updated
        for reference_path, count in count_by_reference_path.items():
            if count:
                linked_images.append({"file_path": path, "reference_path": reference_path, "link_count": count})
    return linked_files, linked_images


def _build_markdown_image_refs(selected_reference_assets: list[dict[str, Any]]) -> list[dict[str, str]]:
    image_refs: list[dict[str, str]] = []
    for index, item in enumerate(selected_reference_assets, start=1):
        reference_path = str(item.get("reference_path") or "").strip()
        if not _looks_like_image_reference(reference_path):
            continue
        image_refs.append(
            {
                "reference_path": reference_path,
                "label": _reference_image_label(index, str(item.get("reason") or item.get("asset_id") or reference_path)),
            }
        )
    return image_refs


def _link_reference_images_in_markdown(content: str, image_refs: list[dict[str, str]]) -> tuple[str, dict[str, int]]:
    count_by_reference_path = {item["reference_path"]: 0 for item in image_refs}
    lines = content.splitlines()
    trailing_newline = content.endswith("\n")
    output: list[str] = []
    for line in lines:
        matched_refs: list[dict[str, str]] = []
        for item in image_refs:
            reference_path = item["reference_path"]
            if reference_path not in line:
                continue
            if _line_has_markdown_image_for_reference(line, reference_path):
                count_by_reference_path[reference_path] += 1
                continue
            matched_refs.append(item)
        output.append(line)
        for item in matched_refs:
            output.append("")
            output.append(f"![{item['label']}]({item['reference_path']})")
            count_by_reference_path[item["reference_path"]] += 1

    result = "\n".join(output)
    if trailing_newline:
        result += "\n"
    return result, count_by_reference_path


def _line_has_markdown_image_for_reference(line: str, reference_path: str) -> bool:
    pattern = re.compile(
        r"!\[([^\]]*)\]\(\s*<?"
        + re.escape(reference_path)
        + r">?\s*(?:\"[^\"]*\"|'[^']*')?\)"
    )
    return bool(pattern.search(line))


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


def _submit_candidate_error_result(
    context: ToolExecutionContext,
    message: str,
    validation_stage: str,
    *,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
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
            "diagnostic_count": len(diagnostics),
            "diagnostics": diagnostics,
            "repair_checklist": _repair_checklist(diagnostics),
        },
    )
    result = _error_result("invalid_arguments", message, ["psop.builder.submit_candidate", "workspace.write_text"])
    result.update(
        {
            "retryable": True,
            "retry_requires_argument_correction": True,
            "attempt": attempt,
            "validation_stage": validation_stage,
            "diagnostic_count": len(diagnostics),
            "diagnostics": diagnostics,
            "repair_checklist": _repair_checklist(diagnostics),
            "required_top_level_fields": _SUBMIT_CANDIDATE_REQUIRED_FIELDS,
            "required_files": REQUIRED_BUILDER_FILES,
            "correction_hint": _correction_hint(diagnostics),
        }
    )
    return result


def _correction_hint(diagnostics: list[dict[str, Any]]) -> str:
    return (
        "请依据 repair_checklist 一次性修复所有列出的字段后，重新调用 psop.builder.submit_candidate 并提交完整 candidate；"
        "不要只修复第一项，也不要只提交部分 metadata、workspace 文件路径或缺少完整内容的 files；"
        "files 对象必须包含所有必需 Markdown 文件。"
    )


def _repair_checklist(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for diagnostic in diagnostics:
        path = str(diagnostic.get("path") or "candidate")
        root = path.split(".", 1)[0]
        group = grouped.setdefault(root, {"field": root, "items": []})
        group["items"].append(
            {
                "path": path,
                "code": str(diagnostic.get("code") or "invalid_candidate"),
                "reason": str(diagnostic.get("message") or "候选字段无效。"),
                "allowed_values": diagnostic.get("allowed_values") if isinstance(diagnostic.get("allowed_values"), list) else [],
                "minimal_example": diagnostic.get("example"),
            }
        )
    return list(grouped.values())
