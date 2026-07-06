#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent_harness.agents.psop.builder.schemas import REQUIRED_BUILDER_FILES, validate_builder_candidate
from app.agent_harness.models.scripted_builder_chat_model import ScriptedBuilderChatModel
from app.agent_harness.schemas import AgentInvocation
from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PSOP builder agent.")
    parser.add_argument("--fixture", required=True, help="包含 AgentInvocation input/context 的 JSON fixture。")
    parser.add_argument("--scripted", action="store_true", help="使用 deterministic scripted model 运行。")
    parser.add_argument("--full-output", action="store_true", help="打印完整 AgentResult JSON。")
    args = parser.parse_args()

    payload = _read_fixture(Path(args.fixture))
    invocation = AgentInvocation(
        agent_key=str(payload.get("agent_key") or "psop.builder"),
        input=dict(payload.get("input") or {}),
        context=dict(payload.get("context") or {}),
    )
    settings = (
        Settings(standard_lightrag_base_url="", standard_lightrag_api_key="")
        if args.scripted
        else Settings()
    )
    service = AgentHarnessService(
        settings=settings,
        chat_model_factory=(lambda _definition: ScriptedBuilderChatModel()) if args.scripted else None,
    )
    result = service.invoke(invocation)
    if args.full_output:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(_result_summary(result), ensure_ascii=False, indent=2))
    if result.status != "succeeded":
        return 1
    artifact_path = _builder_result_path(result.sandbox_path)
    if artifact_path is None or not artifact_path.exists():
        print("未找到 builder-result.json。", file=sys.stderr)
        return 1
    candidate = json.loads(artifact_path.read_text(encoding="utf-8"))
    validate_builder_candidate(
        candidate,
        candidate_reference_assets=invocation.context.get("candidate_reference_assets") or [],
        standard_search_results=[],
    )
    files_root = _skill_draft_files_root(result.sandbox_path)
    if files_root is None or not files_root.is_dir():
        print("未找到 skill-draft 文件目录。", file=sys.stderr)
        return 1
    materialized_error = _validate_materialized_skill_draft(candidate, files_root)
    if materialized_error:
        print(materialized_error, file=sys.stderr)
        return 1
    required_artifacts = {"skill_draft_candidate", "skill_draft_files"}
    artifact_types = {artifact.artifact_type for artifact in result.artifacts}
    missing_artifacts = sorted(required_artifacts - artifact_types)
    if missing_artifacts:
        print(f"缺少必要 artifact：{missing_artifacts}", file=sys.stderr)
        return 1
    event_types = [event.event_type for event in result.events]
    loaded_skills = [
        event.payload.get("skill_name")
        for event in result.events
        if event.event_type == "agent.skill.loaded"
    ]
    required_skills = {"psop-builder"}
    if not required_skills.issubset(set(str(item) for item in loaded_skills)):
        print(f"未加载全部 builder skills：{loaded_skills}", file=sys.stderr)
        return 1
    loaded_resources = {
        (
            str(event.payload.get("skill_name") or ""),
            str(event.payload.get("resource_path") or ""),
        )
        for event in result.events
        if event.event_type == "agent.skill.resource.loaded"
    }
    required_resources = {
        ("psop-builder", "core/SKILL.md"),
        ("psop-builder", "evidence-mapping/SKILL.md"),
        ("psop-builder", "quality-review/SKILL.md"),
    }
    if not required_resources.issubset(loaded_resources):
        print(f"未加载全部 builder skill resources：{sorted(loaded_resources)}", file=sys.stderr)
        return 1
    if "agent.memory.read" not in event_types:
        print("未记录 agent.memory.read。", file=sys.stderr)
        return 1
    required_tools = {
        "psop.builder.read_current_source",
        "psop.builder.list_materials",
        "psop.builder.read_material_analysis",
        "psop.builder.list_reference_assets",
        "psop.standard.search",
        "psop.builder.submit_candidate",
    }
    completed_tools = {
        str(event.payload.get("tool_name") or "")
        for event in result.events
        if event.event_type == "agent.tool.completed"
    }
    missing_tools = sorted(required_tools - completed_tools)
    if missing_tools:
        print(f"缺少必要 tool completed 事件：{missing_tools}", file=sys.stderr)
        return 1
    return 0


def _read_fixture(path: Path) -> dict:
    resolved = path if path.is_absolute() else REPO_ROOT / path
    return json.loads(resolved.read_text(encoding="utf-8"))


def _builder_result_path(sandbox_path: str | None) -> Path | None:
    if not sandbox_path:
        return None
    return Path(sandbox_path) / "outputs" / "builder-result.json"


def _skill_draft_files_root(sandbox_path: str | None) -> Path | None:
    if not sandbox_path:
        return None
    return Path(sandbox_path) / "outputs" / "skill-draft"


def _validate_materialized_skill_draft(candidate: dict, files_root: Path) -> str:
    files = candidate.get("files") if isinstance(candidate.get("files"), dict) else {}
    for relative_path in REQUIRED_BUILDER_FILES:
        if relative_path not in files:
            return f"candidate 缺少必需文件：{relative_path}"
    for relative_path in files:
        file_path = files_root / str(relative_path)
        try:
            file_path.resolve().relative_to(files_root.resolve())
        except ValueError:
            return f"物化文件路径越界：{relative_path}"
        if not file_path.is_file():
            return f"缺少物化文件：{relative_path}"
        if not file_path.read_text(encoding="utf-8").strip():
            return f"物化文件内容为空：{relative_path}"

    skill_md_path = files_root / "SKILL.md"
    skill_md = skill_md_path.read_text(encoding="utf-8")
    if "data:image/" in skill_md:
        return "SKILL.md 不应包含 base64 data:image。"
    for heading in ("## 嵌入参考图片", "## 参考图片汇总", "## Reference Images"):
        if heading in skill_md:
            return f"SKILL.md 不应包含集中追加的参考图片段落：{heading}"

    for reference_path in _selected_image_reference_paths(candidate):
        if reference_path not in skill_md:
            return f"SKILL.md 缺少参考图片相对路径：{reference_path}"
        if not _has_markdown_image_link(skill_md, reference_path):
            return f"SKILL.md 未使用 Markdown 图片语法引用参考图片：{reference_path}"

    for item in candidate.get("materialized_reference_images") or []:
        if not isinstance(item, dict):
            continue
        reference_path = str(item.get("path") or "").strip()
        if not reference_path:
            continue
        file_path = files_root / reference_path
        try:
            file_path.resolve().relative_to(files_root.resolve())
        except ValueError:
            return f"参考图片物化路径越界：{reference_path}"
        if not file_path.is_file() or file_path.stat().st_size <= 0:
            return f"参考图片文件未正确物化：{reference_path}"

    return ""


def _selected_image_reference_paths(candidate: dict) -> list[str]:
    paths = []
    for item in candidate.get("selected_reference_assets") or []:
        if not isinstance(item, dict):
            continue
        reference_path = str(item.get("reference_path") or "").strip()
        if reference_path and _looks_like_image_reference(reference_path):
            paths.append(reference_path)
    return paths


def _looks_like_image_reference(path: str) -> bool:
    return path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))


def _has_markdown_image_link(markdown: str, reference_path: str) -> bool:
    import re

    pattern = re.compile(
        r"!\[[^\]]*\]\(\s*<?"
        + re.escape(reference_path)
        + r">?\s*(?:\"[^\"]*\"|'[^']*')?\)"
    )
    return bool(pattern.search(markdown))


def _result_summary(result) -> dict:
    return {
        "agent_run_id": result.agent_run_id,
        "agent_key": result.agent_key,
        "status": result.status,
        "sandbox_path": result.sandbox_path,
        "artifact_types": [artifact.artifact_type for artifact in result.artifacts],
        "event_count": len(result.events),
        "error_message": result.error_message,
    }


if __name__ == "__main__":
    raise SystemExit(main())
